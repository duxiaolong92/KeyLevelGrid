"""
关键位网格策略主类

组装所有模块，实现完整的交易逻辑
"""

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import yaml

from key_level_grid.utils.logger import get_logger
from key_level_grid.executor.gate_executor import GateExecutor
from key_level_grid.utils.config import SafetyConfig
from key_level_grid.breakout_filter import (
    BreakoutFilter,
    BreakoutFilterConfig,
)
from key_level_grid.filter import FilterConfig, SignalFilterChain
from key_level_grid.indicator import IndicatorConfig, KeyLevelGridIndicator
from key_level_grid.kline_feed import BinanceKlineFeed
from key_level_grid.models import Kline, KlineFeedConfig, Timeframe, KeyLevelGridState
from key_level_grid.mtf_manager import MultiTimeframeManager
from key_level_grid.position import PositionConfig, KeyLevelPositionManager
from key_level_grid.signal import SignalConfig, SignalType, KeyLevelSignal, KeyLevelSignalGenerator


@dataclass
class KeyLevelGridConfig:
    """关键位网格策略完整配置"""
    # 交易配置
    symbol: str = "XPLUSDT"
    exchange: str = "binance"
    market_type: str = "futures"  # futures / spot
    margin_mode: str = "cross"    # cross (全仓) / isolated (逐仓)
    leverage: int = 3             # 杠杆倍数
    
    # API 配置 (环境变量名)
    api_key_env: str = ""
    api_secret_env: str = ""
    
    # 子模块配置
    kline_config: KlineFeedConfig = None
    indicator_config: IndicatorConfig = None
    signal_config: SignalConfig = None
    filter_config: FilterConfig = None
    breakout_config: BreakoutFilterConfig = None
    position_config: PositionConfig = None
    grid_config: "GridConfig" = None  # V2.3: 网格配置
    
    # 运行模式
    dry_run: bool = True                  # 模拟交易
    auto_trade: bool = False              # 自动交易 (需TG确认)
    
    # Telegram
    tg_enabled: bool = False
    tg_confirmation: bool = True
    tg_timeout_sec: int = 60
    tg_bot_token: str = ""
    tg_chat_id: str = ""
    tg_notify_config: dict = None  # 通知配置
    
    def __post_init__(self):
        if self.kline_config is None:
            self.kline_config = KlineFeedConfig(symbol=self.symbol)
        if self.indicator_config is None:
            self.indicator_config = IndicatorConfig()
        if self.signal_config is None:
            self.signal_config = SignalConfig()
        if self.filter_config is None:
            self.filter_config = FilterConfig()
        if self.breakout_config is None:
            self.breakout_config = BreakoutFilterConfig()
        if self.position_config is None:
            self.position_config = PositionConfig()


class KeyLevelGridStrategy:
    """
    关键位网格趋势策略
    
    核心流程:
    1. 接收K线数据
    2. 计算通道指标
    3. 生成交易信号
    4. 过滤信号
    5. 仓位管理
    6. 执行交易
    """
    
    def __init__(self, config: KeyLevelGridConfig):
        self.config = config
        self.logger = get_logger(__name__)
        
        # 初始化子模块
        self.kline_feed = BinanceKlineFeed(config.kline_config)
        self.indicator = KeyLevelGridIndicator(
            config.indicator_config, 
            symbol=config.symbol
        )
        self.mtf_manager = MultiTimeframeManager(
            self.kline_feed, 
            self.indicator
        )
        self.signal_generator = KeyLevelSignalGenerator(
            config.signal_config,
            symbol=config.symbol
        )
        self.filter_chain = SignalFilterChain(config.filter_config)
        self.breakout_filter = BreakoutFilter(config.breakout_config)
        # V2.3: 网格仓位管理器
        from key_level_grid.position import (
            GridConfig, StopLossConfig, TakeProfitConfig, ResistanceConfig
        )
        # 使用配置中的 grid_config，如果未设置则使用默认值
        grid_config = config.grid_config if config.grid_config else GridConfig()
        self.position_manager = KeyLevelPositionManager(
            grid_config=grid_config,
            position_config=config.position_config,
            stop_loss_config=StopLossConfig(),
            take_profit_config=TakeProfitConfig(),
            resistance_config=ResistanceConfig(min_strength=80),
            symbol=config.symbol
        )
        
        # 初始化交易所执行器 (Gate)
        self._executor: Optional[GateExecutor] = None
        self._init_executor()
        
        # 账户余额缓存
        self._account_balance: Dict[str, float] = {"total": 0, "free": 0, "used": 0}
        self._balance_updated_at: float = 0
        
        # Gate 挂单缓存
        self._gate_open_orders: List[Dict] = []
        self._orders_updated_at: float = 0
        # 最近一次获取到的合约大小（BTC/contract）
        self._contract_size: float = 1.0
        
        # Gate 持仓缓存
        self._gate_position: Dict[str, Any] = {}  # 当前持仓
        self._position_updated_at: float = 0
        self._last_position_usdt: float = 0  # 上次持仓价值（用于检测变化）
        self._tp_orders_submitted: bool = False  # 止盈单是否已提交
        
        # 止损单状态
        self._stop_loss_order_id: Optional[str] = None  # 当前止损单 ID
        self._stop_loss_contracts: float = 0  # 止损单覆盖的张数
        self._sl_order_updated_at: float = 0  # 止损单更新时间
        
        # Gate 成交记录缓存
        self._gate_trades: List[Dict] = []
        self._trades_updated_at: float = 0
        self._strategy_start_time: float = 0  # 策略启动时间戳
        
        # 状态
        self._running = False
        self._current_state: Optional[KeyLevelGridState] = None
        self._pending_signal: Optional[KeyLevelSignal] = None
        self._restored_state = False
        self._grid_created = False  # 网格是否已创建
        
        # 回调
        self._on_signal_callback = None
        self._on_trade_callback = None
        
        # Telegram 通知
        self._notifier: Optional["NotificationManager"] = None
        self._tg_bot = None  # Telegram Bot 实例
        self._tg_bot_checked_at: float = 0  # Bot 健康检查时间戳
        self._init_notifier()
    
    def _init_executor(self) -> None:
        """初始化交易所执行器"""
        config = self.config
        
        # 从环境变量读取 API 密钥
        api_key = os.getenv(config.api_key_env, "") if config.api_key_env else ""
        api_secret = os.getenv(config.api_secret_env, "") if config.api_secret_env else ""
        
        # 根据策略配置推导一个更合理的单笔最大金额（用于执行器安全检查）
        # 说明：默认 SafetyConfig.max_position_value=100，会拦截网格策略的正常挂单
        try:
            pos_cfg = self.position_manager.position_config
            max_position_usdt = float(getattr(pos_cfg, "max_position_usdt", 0) or 0)
        except Exception:
            max_position_usdt = 0.0

        safety_config = SafetyConfig(
            # 单笔最大金额：允许至少覆盖“最大仓位/网格数”的量级，这里取 max_position_usdt 作为上限更直观
            max_position_value=max(500.0, max_position_usdt if max_position_usdt > 0 else 500.0),
            emergency_stop_loss=max(50.0, (max_position_usdt * 0.2) if max_position_usdt > 0 else 50.0),
        )

        if config.dry_run:
            self.logger.info("🧪 Dry Run 模式，使用模拟交易")
            self._executor = GateExecutor(paper_trading=True, safety_config=safety_config)
        elif config.exchange.lower() == "gate" and api_key and api_secret:
            self.logger.info(f"🔗 连接 Gate.io 交易所 (market={config.market_type})")
            self._executor = GateExecutor(
                api_key=api_key,
                api_secret=api_secret,
                paper_trading=False,
                safety_config=safety_config,
            )
        else:
            self.logger.warning(
                f"⚠️ 未配置有效的交易所 API，回退到模拟模式 "
                f"(exchange={config.exchange}, api_key_env={config.api_key_env})"
            )
            self._executor = GateExecutor(paper_trading=True, safety_config=safety_config)
    
    def _init_notifier(self) -> None:
        """初始化 Telegram 通知器"""
        config = self.config
        
        if not config.tg_enabled:
            self.logger.info("📵 Telegram 通知未启用")
            return
        
        if not config.tg_bot_token or not config.tg_chat_id:
            self.logger.warning("⚠️ Telegram 配置不完整，通知功能已禁用")
            return
        
        try:
            from key_level_grid.telegram.notify import NotificationManager, NotifyConfig
            from key_level_grid.telegram.bot import KeyLevelTelegramBot, TelegramConfig
            
            # 创建通知配置
            notify_raw = config.tg_notify_config or {}
            notify_config = NotifyConfig(
                startup=notify_raw.get('startup', True),
                shutdown=notify_raw.get('shutdown', True),
                error=notify_raw.get('error', True),
                order_filled=notify_raw.get('order_filled', True),
                order_placed=notify_raw.get('order_placed', False),
                grid_rebuild=notify_raw.get('grid_rebuild', True),
                orders_summary=notify_raw.get('orders_summary', True),
                risk_warning=notify_raw.get('risk_warning', True),
                near_stop_loss_pct=notify_raw.get('near_stop_loss_pct', 0.02),
                daily_summary=notify_raw.get('daily_summary', True),
                daily_summary_time=notify_raw.get('daily_summary_time', '20:00'),
                heartbeat=notify_raw.get('heartbeat', False),
                heartbeat_interval_hours=notify_raw.get('heartbeat_interval_hours', 4),
            )
            
            # 创建 Bot 配置
            tg_config = TelegramConfig(
                bot_token=config.tg_bot_token,
                chat_id=config.tg_chat_id,
            )
            
            # 创建 Bot 和通知管理器
            self._tg_bot = KeyLevelTelegramBot(tg_config, strategy=self)
            self._notifier = NotificationManager(
                bot=self._tg_bot, 
                config=notify_config,
                bot_token=config.tg_bot_token,
                chat_id=config.tg_chat_id,
            )
            
            self.logger.info("📱 Telegram 通知已启用")
        except ImportError as e:
            self.logger.warning(f"⚠️ Telegram 模块导入失败: {e}")
        except Exception as e:
            self.logger.error(f"❌ 初始化 Telegram 通知失败: {e}")
    
    @classmethod
    def from_yaml(cls, config_path: str) -> "KeyLevelGridStrategy":
        """从 YAML 文件加载配置 (V2.3 简化版)"""
        with open(config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
        
        # 解析配置
        trading = raw_config.get('trading', {})
        symbol = trading.get('symbol', 'BTCUSDT')
        
        # K线配置
        kline_raw = raw_config.get('kline_feed', {})
        kline_config = KlineFeedConfig(
            symbol=symbol,
            primary_timeframe=Timeframe.from_string(trading.get('timeframe', '4h')),
            auxiliary_timeframes=[
                Timeframe.from_string(tf) for tf in trading.get('aux_timeframes', ['1d'])
            ],
            history_bars=kline_raw.get('history_bars', 500),
            max_retries=kline_raw.get('max_retries', 3),
        )
        
        # V2.3: 指标配置简化 (只保留 MACD)
        indicator_config = IndicatorConfig(
            macd_enabled=True,
        )
        
        # V2.3: 信号配置简化 (基于支撑/阻力位)
        resistance_raw = raw_config.get('resistance', {})
        signal_config = SignalConfig(
            min_score=resistance_raw.get('min_strength', 80),  # 使用支撑位强度阈值
        )
        
        # V2.3: 仓位配置 (网格模式)
        pos_raw = raw_config.get('position', {})
        # 杠杆优先使用 trading.leverage，确保两者一致
        trading_leverage = trading.get('leverage', 3)
        position_leverage = pos_raw.get('max_leverage', trading_leverage)
        # 如果 position.max_leverage 未设置或与 trading.leverage 不同，使用 trading.leverage
        if position_leverage != trading_leverage:
            import logging
            logging.warning(
                f"[Config] position.max_leverage({position_leverage}) 与 trading.leverage({trading_leverage}) 不一致，"
                f"使用 trading.leverage={trading_leverage}"
            )
            position_leverage = trading_leverage
        
        position_config = PositionConfig(
            total_capital=pos_raw.get('total_capital', 5000),
            max_leverage=position_leverage,
            max_capital_usage=pos_raw.get('max_capital_usage', 0.8),
            allocation_mode=pos_raw.get('allocation_mode', 'equal'),
        )
        
        # 打印配置验证
        import logging
        logging.info(f"[Config] 仓位配置: total_capital={position_config.total_capital}, "
                     f"max_leverage={position_config.max_leverage}, "
                     f"max_capital_usage={position_config.max_capital_usage}, "
                     f"max_position_usdt={position_config.max_position_usdt}")
        
        # V2.3: 网格配置
        from key_level_grid.position import GridConfig
        grid_raw = raw_config.get('grid', {})
        grid_config = GridConfig(
            range_mode=grid_raw.get('range_mode', 'auto'),
            manual_upper=grid_raw.get('manual_upper', 0.0),
            manual_lower=grid_raw.get('manual_lower', 0.0),
            count_mode=grid_raw.get('count_mode', 'by_levels'),
            fixed_count=grid_raw.get('fixed_count', 5),
            max_grids=grid_raw.get('max_grids', 10),
            floor_buffer=grid_raw.get('floor_buffer', 0.005),
            rebuild_enabled=grid_raw.get('rebuild_enabled', True),
            rebuild_threshold_pct=grid_raw.get('rebuild_threshold_pct', 0.02),
            rebuild_cooldown_sec=grid_raw.get('rebuild_cooldown_sec', 900),
        )
        logging.info(f"[Config] 网格配置: rebuild_enabled={grid_config.rebuild_enabled}, "
                     f"rebuild_threshold={grid_config.rebuild_threshold_pct:.2%}, "
                     f"cooldown={grid_config.rebuild_cooldown_sec}s")
        
        # API 配置
        api_config = raw_config.get('api', {})
        
        # Telegram 配置
        tg_config = raw_config.get('telegram', {})
        tg_enabled = tg_config.get('enabled', False)
        tg_bot_token = os.getenv(tg_config.get('bot_token_env', 'TG_BOT_TOKEN'), '')
        tg_chat_id = os.getenv(tg_config.get('chat_id_env', 'TG_CHAT_ID'), '')
        tg_notify_config = tg_config.get('notifications', {})
        
        config = KeyLevelGridConfig(
            symbol=symbol,
            exchange=trading.get('exchange', 'binance'),
            market_type=trading.get('market_type', 'futures'),
            margin_mode=trading.get('margin_mode', 'cross'),
            leverage=trading.get('leverage', 3),
            api_key_env=api_config.get('key_env', ''),
            api_secret_env=api_config.get('secret_env', ''),
            kline_config=kline_config,
            indicator_config=indicator_config,
            signal_config=signal_config,
            position_config=position_config,
            grid_config=grid_config,
            dry_run=raw_config.get('dry_run', True),
            tg_enabled=tg_enabled,
            tg_bot_token=tg_bot_token,
            tg_chat_id=tg_chat_id,
            tg_notify_config=tg_notify_config,
        )
        
        return cls(config)
    
    async def start(self) -> None:
        """启动策略"""
        import time
        if self._running:
            self.logger.warning("策略已在运行")
            return
        
        self._running = True
        self._strategy_start_time = time.time() * 1000  # 毫秒时间戳
        self.logger.info(f"启动关键位网格策略: {self.config.symbol}")
        
        # 启动数据源
        await self.kline_feed.start()
        
        # 启动 WebSocket 订阅
        self.kline_feed.start_ws_subscription(self._on_kline_close)
        
        # 启动 Telegram Bot（如果已配置）
        if self._tg_bot:
            try:
                await self._tg_bot.start()
                self.logger.info("📱 Telegram Bot 已启动，可响应命令")
            except Exception as e:
                self.logger.error(f"Telegram Bot 启动失败: {e}")
        
        # 标记是否已发送启动通知
        self._startup_notified = False
        
        # 主循环
        while self._running:
            try:
                await self._update_cycle()
                
                # 首次运行后发送启动通知
                if not self._startup_notified and self._current_state:
                    await self._send_startup_notification()
                    self._startup_notified = True
                
                await asyncio.sleep(self.config.kline_config.update_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"策略更新异常: {e}", exc_info=True)
                # 发送错误通知
                await self._notify_error("StrategyError", str(e), "主循环更新")
                await asyncio.sleep(5)
        
        await self.stop()
    
    async def stop(self, reason: str = "手动停止") -> None:
        """停止策略"""
        self._running = False
        await self.kline_feed.stop()
        
        # 停止 Telegram Bot
        if self._tg_bot:
            try:
                await self._tg_bot.stop()
                self.logger.info("📱 Telegram Bot 已停止")
            except Exception as e:
                self.logger.error(f"Telegram Bot 停止失败: {e}")
        
        self.logger.info("策略已停止")
        
        # 发送停止通知
        await self._send_shutdown_notification(reason)
    
    async def _update_cycle(self) -> None:
        """更新周期"""
        # 获取最新K线
        klines = await self.kline_feed.get_latest_klines(
            self.config.kline_config.primary_timeframe
        )
        
        if len(klines) < 170:
            return
        
        # 首次运行：先获取账户余额，用真实余额覆盖配置的 total_capital
        import time
        if self._balance_updated_at == 0:
            await self._update_account_balance()
            # 用真实账户余额覆盖配置的 total_capital
            real_balance = self._account_balance.get("total", 0)
            pos_config = self.position_manager.position_config
            if real_balance > 0:
                pos_config.total_capital = real_balance
                self.logger.info(
                    f"📊 使用真实余额: total_capital={real_balance:.2f} USDT "
                    f"(覆盖配置值)"
                )
            self.logger.info(
                f"📊 仓位配置: total_capital={pos_config.total_capital:.2f}, "
                f"max_leverage={pos_config.max_leverage}x, "
                f"max_position={pos_config.max_position_usdt:.2f} USDT"
            )
        
        # 尝试恢复网格状态 (仅一次)
        if not self._restored_state:
            current_price = klines[-1].close if klines else 0
            if current_price > 0:
                restored = self.position_manager.restore_state(current_price)
                if restored:
                    self.logger.info("已从持久化恢复网格状态")
                    self._grid_created = True  # 恢复成功，标记网格已创建
                    # 如果没有挂单（例如手动全撤），重新提交
                    if not self.config.dry_run and self._executor and not self._gate_open_orders:
                        await self._submit_grid_orders(self.position_manager.state)
            self._restored_state = True
        
        # 更新实时K线
        await self.kline_feed.update_latest(
            self.config.kline_config.primary_timeframe
        )
        
        # 计算通道状态
        self._current_state = self.indicator.calculate(klines)
        
        # 定期更新账户余额 (每 60 秒)
        if time.time() - self._balance_updated_at > 60:
            await self._update_account_balance()
        # 定期同步 Gate 挂单 (每 30 秒)
        if time.time() - self._orders_updated_at > 30:
            await self._update_gate_orders()
        # 定期同步 Gate 持仓 (每 15 秒)
        if time.time() - self._position_updated_at > 15:
            await self._update_gate_position()
        # 定期同步 Gate 成交记录 (每 60 秒)
        if time.time() - self._trades_updated_at > 60:
            await self._update_gate_trades()
        # 定期检查 Telegram Bot 状态 (每 5 分钟)
        await self._check_telegram_bot()
        
        # 首次创建网格 (需要价格数据和支撑/阻力位计算完成)
        if not self._grid_created and self._current_state:
            await self._create_initial_grid(klines)

        # 价格偏离触发：自动重建网格（方案A：重建模式跳过均价保护）
        if self._grid_created and self._current_state and self.position_manager.state:
            await self._maybe_rebuild_grid(klines)
        
        # 检测持仓变化，提交止盈挂单
        await self._check_and_submit_take_profit_orders()
        
        # 检测持仓变化，更新止损单
        await self._check_and_update_stop_loss_order()
        
        # 更新仓位 (如果有)
        if self.position_manager.state:
            result = self.position_manager.update_position(
                self._current_state.close,
                self._current_state
            )
            
            if result.get('status') == 'stop_loss_triggered':
                await self._handle_stop_loss(result)
            
            for action in result.get('actions', []):
                await self._handle_action(action)

    async def _maybe_rebuild_grid(self, klines: List[Kline]) -> None:
        """
        当价格相对网格锚点偏离超过阈值时，自动重建网格。

        - 触发条件：abs(current - anchor) / anchor > 2%
        - 冷却：避免频繁重建（默认 15 分钟）
        - 方案A：重建模式下提交买单时跳过“均价保护”过滤
        """
        import time
        if self.config.dry_run or not self._executor:
            return

        state = self.position_manager.state
        if not state:
            return

        current_price = float(self._current_state.close or 0)
        if current_price <= 0:
            return

        # 初始化锚点（如果旧状态没有该字段）
        if getattr(state, "anchor_price", 0.0) <= 0:
            state.anchor_price = current_price
            state.anchor_ts = int(time.time())
            self.position_manager._save_state()
            return

        anchor_price = float(state.anchor_price or 0)
        if anchor_price <= 0:
            return

        # 从配置读取重建参数
        grid_cfg = self.position_manager.grid_config
        if not grid_cfg.rebuild_enabled:
            return  # 重建功能已禁用
        
        threshold = grid_cfg.rebuild_threshold_pct
        cooldown_sec = grid_cfg.rebuild_cooldown_sec
        
        move_pct = abs(current_price - anchor_price) / anchor_price
        last_rebuild_at = getattr(self, "_last_rebuild_at", 0.0) or 0.0
        if last_rebuild_at and (time.time() - last_rebuild_at) < cooldown_sec:
            return

        if move_pct < threshold:
            return

        self.logger.warning(
            f"🔄 触发网格重建: current={current_price:.2f}, anchor={anchor_price:.2f}, "
            f"move={move_pct:.2%} > {threshold:.2%}"
        )

        gate_symbol = self._convert_to_gate_symbol(self.config.symbol)

        # 1) 先撤掉该 symbol 下所有挂单（包含普通单与计划委托）
        try:
            if hasattr(self._executor, "cancel_all_plan_orders"):
                await self._executor.cancel_all_plan_orders(gate_symbol)
            if hasattr(self._executor, "cancel_all_orders"):
                await self._executor.cancel_all_orders(gate_symbol)
        except Exception as e:
            self.logger.error(f"网格重建撤单失败: {e}", exc_info=True)

        # 2) 同步一次挂单缓存
        await self._update_gate_orders()

        # 3) 重新计算支撑/阻力位
        from key_level_grid.models import Timeframe
        klines_1d = None
        if Timeframe.D1 in self.config.kline_config.auxiliary_timeframes:
            klines_1d = self.kline_feed.get_cached_klines(Timeframe.D1)

        resistance_calc = self.position_manager.resistance_calc
        primary_tf = self.config.kline_config.primary_timeframe.value
        resistances = resistance_calc.calculate_resistance_levels(
            current_price, klines, "long", klines_1d=klines_1d, primary_timeframe=primary_tf
        )
        supports = resistance_calc.calculate_support_levels(
            current_price, klines, klines_1d=klines_1d, primary_timeframe=primary_tf
        )

        if not supports:
            self.logger.warning("网格重建：未找到有效支撑位，放弃重建")
            return

        # 4) 重建网格（会写入新锚点并持久化）
        new_grid = self.position_manager.create_grid(
            current_price=current_price,
            support_levels=supports,
            resistance_levels=resistances,
        )
        if not new_grid:
            self.logger.warning("网格重建失败，将在下次周期重试")
            return

        # 更新锚点（保险起见）
        new_grid.anchor_price = current_price
        new_grid.anchor_ts = int(time.time())
        self.position_manager._save_state()

        # 重建后允许重新提交 TP（但会被“已挂止盈覆盖”逻辑挡住重复）
        self._tp_orders_submitted = False
        self._stop_loss_order_id = None  # 重置止损单状态（已被全部撤销）
        self._stop_loss_contracts = 0

        # 5) 提交买单：重建模式跳过均价保护（方案A）
        await self._submit_grid_orders(new_grid, rebuild_mode=True)
        self._last_rebuild_at = time.time()
    
    async def force_rebuild_grid(self) -> bool:
        """
        强制重建网格（由 Telegram 命令触发）
        
        不检查阈值和冷却时间，立即执行重建
        
        Returns:
            bool: 是否成功
        """
        import time
        
        if self.config.dry_run or not self._executor:
            self.logger.warning("Dry Run 模式或无执行器，无法强制重建")
            return False
        
        if not self._current_state:
            self.logger.warning("无当前状态数据，无法强制重建")
            return False
        
        current_price = float(self._current_state.close or 0)
        if current_price <= 0:
            self.logger.warning("当前价格无效，无法强制重建")
            return False
        
        self.logger.info(f"🔄 强制重建网格: current_price={current_price:.2f}")
        
        gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
        
        try:
            # 1) 撤掉该 symbol 下所有挂单
            if hasattr(self._executor, "cancel_all_plan_orders"):
                await self._executor.cancel_all_plan_orders(gate_symbol)
            if hasattr(self._executor, "cancel_all_orders"):
                await self._executor.cancel_all_orders(gate_symbol)
            
            # 2) 同步挂单缓存
            await self._update_gate_orders()
            
            # 3) 获取最新K线
            klines = self.kline_feed.get_cached_klines(
                self.config.kline_config.primary_timeframe
            )
            if len(klines) < 50:
                self.logger.warning("K线数据不足，无法重建")
                return False
            
            # 4) 重新计算支撑/阻力位
            from key_level_grid.models import Timeframe
            klines_1d = None
            if Timeframe.D1 in self.config.kline_config.auxiliary_timeframes:
                klines_1d = self.kline_feed.get_cached_klines(Timeframe.D1)
            
            resistance_calc = self.position_manager.resistance_calc
            primary_tf = self.config.kline_config.primary_timeframe.value
            resistances = resistance_calc.calculate_resistance_levels(
                current_price, klines, "long", klines_1d=klines_1d, primary_timeframe=primary_tf
            )
            supports = resistance_calc.calculate_support_levels(
                current_price, klines, klines_1d=klines_1d, primary_timeframe=primary_tf
            )
            
            if not supports:
                self.logger.warning("未找到有效支撑位，放弃重建")
                return False
            
            # 5) 保存旧锚点用于通知
            old_anchor = 0
            if self.position_manager.state:
                old_anchor = getattr(self.position_manager.state, "anchor_price", 0) or 0
            
            # 6) 重建网格
            new_grid = self.position_manager.create_grid(
                current_price=current_price,
                support_levels=supports,
                resistance_levels=resistances,
            )
            if not new_grid:
                self.logger.warning("网格重建失败")
                return False
            
            # 更新锚点
            new_grid.anchor_price = current_price
            new_grid.anchor_ts = int(time.time())
            self.position_manager._save_state()
            
            # 重建后允许重新提交 TP 和 SL
            self._tp_orders_submitted = False
            self._stop_loss_order_id = None  # 重置止损单状态（已被全部撤销）
            self._stop_loss_contracts = 0
            
            # 7) 提交买单
            await self._submit_grid_orders(new_grid, rebuild_mode=True)
            self._last_rebuild_at = time.time()
            
            # 8) 发送通知
            await self._notify_grid_rebuild(
                reason="手动触发",
                old_anchor=old_anchor,
                new_anchor=current_price,
                new_orders=[{"side": "buy", "price": o.price, "amount": o.amount_usdt} 
                           for o in new_grid.buy_orders if not o.is_filled],
            )
            
            self.logger.info(f"✅ 网格强制重建完成: 新锚点={current_price:.2f}")
            return True
            
        except Exception as e:
            self.logger.error(f"强制重建网格失败: {e}", exc_info=True)
            await self._notify_error("RebuildError", str(e), "强制重建网格")
            return False
    
    async def _update_account_balance(self) -> None:
        """从交易所更新账户余额"""
        import time
        if not self._executor:
            return
        
        try:
            balance = await self._executor.get_balance("USDT")
            self._account_balance = {
                "total": balance.get("total", 0),
                "free": balance.get("free", 0),
                "used": balance.get("used", 0),
            }
            self._balance_updated_at = time.time()
            
            self.logger.debug(
                f"💰 账户余额更新: total={self._account_balance['total']:.2f}, "
                f"free={self._account_balance['free']:.2f}"
            )
        except Exception as e:
            self.logger.error(f"获取账户余额失败: {e}")

    async def _update_gate_orders(self) -> None:
        """从 Gate 交易所同步当前挂单，并计算 USDT 价值"""
        import time
        if not self._executor or self.config.dry_run:
            return
        
        try:
            gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
            orders = await self._executor.get_open_orders(gate_symbol)
            
            # 获取合约信息以计算 USDT 价值
            # Gate 合约的 amount 是张数，需要乘以 contractSize 得到币量
            contract_size = 1.0
            try:
                markets = self._executor._exchange.markets
                if not markets:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._executor._exchange.load_markets
                    )
                    markets = self._executor._exchange.markets
                market = markets.get(gate_symbol, {})
                contract_size = market.get('contractSize', 1.0) or 1.0
            except Exception as e:
                self.logger.warning(f"获取合约信息失败，使用默认 contractSize=1: {e}")
            
            self._gate_open_orders = []
            self._contract_size = contract_size  # 保存供其他方法使用
            
            for o in orders:
                price = float(o.get("price", 0) or 0)
                remaining_contracts = float(o.get("remaining", 0) or 0)  # 原始张数
                
                # 真实 BTC 数量 = 张数 × 每张合约币量
                real_btc = remaining_contracts * contract_size
                # USDT 价值 = 真实 BTC × 价格
                amount_usdt = real_btc * price
                
                self._gate_open_orders.append({
                    "id": o.get("id", ""),
                    "side": o.get("side", ""),
                    "price": price,
                    "amount": amount_usdt,  # USDT 价值
                    "contracts": real_btc,  # 真实 BTC 数量（用于显示）
                    "raw_contracts": remaining_contracts,  # 原始张数（用于调试）
                    "filled": float(o.get("filled", 0) or 0),
                    "remaining": remaining_contracts,
                    "status": o.get("status", ""),
                    "type": o.get("type", ""),
                    "timestamp": o.get("timestamp", 0),
                    "contract_size": contract_size,
                })
            
            self._orders_updated_at = time.time()
            
            self.logger.debug(
                f"📋 Gate 挂单同步: {len(self._gate_open_orders)} 个订单, "
                f"contractSize={contract_size}"
            )
        except Exception as e:
            self.logger.error(f"同步 Gate 挂单失败: {e}")
    
    async def _update_gate_position(self) -> None:
        """从 Gate 交易所同步当前持仓"""
        import time
        if not self._executor or self.config.dry_run:
            return
        
        try:
            gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
            positions = await self._executor.get_positions(gate_symbol)
            
            # 调试：打印原始持仓数据
            if positions:
                self.logger.debug(f"📊 Gate 原始持仓数据: {len(positions)} 条")
                for i, pos in enumerate(positions[:3]):
                    self.logger.debug(
                        f"  持仓 {i+1}: symbol={pos.get('symbol')}, "
                        f"contracts={pos.get('contracts')}, "
                        f"side={pos.get('side')}, "
                        f"notional={pos.get('notional')}, "
                        f"entryPrice={pos.get('entryPrice')}"
                    )
            
            # 获取 contractSize（可能在 _update_gate_orders 中已获取）
            contract_size = getattr(self, '_contract_size', None) or 0.0
            if contract_size <= 0:
                try:
                    markets = self._executor._exchange.markets
                    if not markets:
                        await asyncio.get_event_loop().run_in_executor(
                            None, self._executor._exchange.load_markets
                        )
                        markets = self._executor._exchange.markets
                    market = markets.get(gate_symbol, {})
                    contract_size = market.get('contractSize', 1.0) or 1.0
                    self._contract_size = contract_size
                except Exception as e:
                    contract_size = 0.0001  # BTC 合约默认值
                    self.logger.warning(f"获取 contractSize 失败，使用默认值 {contract_size}: {e}")
            
            # 找到当前标的的持仓
            self._gate_position = {}
            for pos in positions:
                pos_symbol = pos.get("symbol", "")
                # 放宽匹配：支持多种符号格式
                symbol_match = (
                    pos_symbol == gate_symbol or
                    pos_symbol.replace("/", "_").replace(":USDT", "") == gate_symbol.replace("/", "_").replace(":USDT", "") or
                    gate_symbol.split("/")[0] in pos_symbol  # BTC 在符号中
                )
                
                if symbol_match:
                    raw_contracts = float(pos.get("contracts", 0) or 0)  # 原始张数
                    notional = float(pos.get("notional", 0) or 0)
                    entry_price = float(pos.get("entryPrice", 0) or 0)
                    side = pos.get("side", "")
                    
                    # 真实 BTC 数量 = 张数 × contractSize
                    real_btc = raw_contracts * contract_size
                    
                    # 放宽判断：contracts > 0 即为多头（网格只做多，不会有空头）
                    if raw_contracts > 0:
                        self._gate_position = {
                            "symbol": pos_symbol,
                            "contracts": real_btc,  # 真实 BTC 数量
                            "raw_contracts": raw_contracts,  # 原始张数
                            "notional": abs(notional) if notional else real_btc * entry_price,
                            "entry_price": entry_price,
                            "side": "long",
                            "unrealized_pnl": float(pos.get("unrealizedPnl", 0) or 0),
                            "contract_size": contract_size,
                        }
                        self.logger.info(
                            f"📊 Gate 持仓同步: {real_btc:.6f} BTC ({raw_contracts:.0f}张) @ {entry_price:.2f}, "
                            f"价值={self._gate_position['notional']:.2f} USDT, contractSize={contract_size}"
                        )
                        break
            
            if not self._gate_position:
                self.logger.debug("📊 Gate 无持仓")
            
            self._position_updated_at = time.time()
            
        except Exception as e:
            self.logger.error(f"同步 Gate 持仓失败: {e}")
    
    async def _update_gate_trades(self) -> None:
        """从 Gate 交易所获取成交记录"""
        import time
        from datetime import datetime
        
        if not self._executor or self.config.dry_run:
            return
        
        try:
            gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
            
            # 获取策略启动后的成交记录
            # 如果没有启动时间，使用 24 小时前
            since = int(self._strategy_start_time) if self._strategy_start_time else int((time.time() - 86400) * 1000)
            
            trades = await self._executor.get_trade_history(
                symbol=gate_symbol,
                since=since,
                limit=50
            )
            
            # 解析成交记录
            self._gate_trades = []
            for trade in trades:
                trade_time = trade.get("timestamp", 0)
                trade_datetime = datetime.fromtimestamp(trade_time / 1000) if trade_time else None
                
                self._gate_trades.append({
                    "id": trade.get("id", ""),
                    "time": trade_datetime.strftime("%Y-%m-%d %H:%M:%S") if trade_datetime else "",
                    "timestamp": trade_time,
                    "side": trade.get("side", ""),
                    "price": float(trade.get("price", 0) or 0),
                    "amount": float(trade.get("amount", 0) or 0),
                    "cost": float(trade.get("cost", 0) or 0),  # USDT 金额
                    "fee": float(trade.get("fee", {}).get("cost", 0) or 0),
                    "fee_currency": trade.get("fee", {}).get("currency", ""),
                })
            
            # 按时间倒序排列（最新在前）
            self._gate_trades.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            
            self._trades_updated_at = time.time()
            
            if self._gate_trades:
                self.logger.debug(f"📜 Gate 成交记录同步: {len(self._gate_trades)} 条")
            
        except Exception as e:
            self.logger.error(f"同步 Gate 成交记录失败: {e}")
    
    async def _check_and_submit_take_profit_orders(self) -> None:
        """
        检测持仓变化，提交止盈挂单
        
        修正版逻辑：
        1. 检测持仓增加（买单成交）→ 重新计算并提交止盈单
        2. 检测持仓减少（止盈成交）→ 记录日志
        3. 止盈单数量 = 已成交买单数量（与买单对称）
        """
        if self.config.dry_run or not self._executor:
            return
        
        if not self.position_manager.state:
            return
        
        # 获取当前持仓张数（更精确）
        current_contracts = int(float(self._gate_position.get("raw_contracts", 0) or 0))
        current_position_usdt = self._gate_position.get("notional", 0)
        
        # 获取上次持仓张数
        last_contracts = getattr(self, "_last_position_contracts", 0)
        
        # 检测持仓增加（买单成交）
        if current_contracts > last_contracts:
            added_contracts = current_contracts - last_contracts
            self.logger.info(
                f"🎯 持仓增加: +{added_contracts}张, "
                f"当前持仓: {current_contracts}张 (≈{current_position_usdt:.0f} USDT)"
            )
            # 重新提交止盈单（会自动计算正确的数量）
            await self._submit_take_profit_orders(current_position_usdt)
        
        # 检测持仓减少（止盈成交）
        elif current_contracts < last_contracts and last_contracts > 0:
            reduced_contracts = last_contracts - current_contracts
            self.logger.info(
                f"✅ 持仓减少: -{reduced_contracts}张 (止盈成交), "
                f"当前持仓: {current_contracts}张 (≈{current_position_usdt:.0f} USDT)"
            )
            # 如果全部平仓，重置状态
            if current_contracts == 0:
                self._tp_orders_submitted = False
                self.logger.info("📭 持仓已清空，重置止盈单状态")
        
        # 有持仓但无止盈单（重启恢复场景）
        elif current_contracts > 0 and not self._has_existing_tp_orders():
            self.logger.info(
                f"🔄 检测到持仓但无止盈单，准备恢复: {current_contracts}张"
            )
            await self._submit_take_profit_orders(current_position_usdt)
        
        # 更新上次持仓记录
        self._last_position_contracts = current_contracts
        self._last_position_usdt = current_position_usdt
    
    def _has_existing_tp_orders(self) -> bool:
        """检查是否已有止盈卖单挂单"""
        for order in self._gate_open_orders:
            if order.get("side") == "sell":
                return True
        return False
    
    async def _check_and_update_stop_loss_order(self) -> None:
        """
        检查并更新止损单
        
        逻辑：
        1. 有持仓 → 需要止损单
        2. 持仓张数变化 → 更新止损单
        3. 无持仓 → 取消止损单
        """
        if self.config.dry_run or not self._executor:
            self.logger.debug("止损单检查: dry_run 或无执行器，跳过")
            return
        
        if not self.position_manager.state:
            self.logger.debug("止损单检查: 无 position_manager.state，跳过")
            return
        
        import time
        
        # 获取当前持仓张数
        current_contracts = int(float(self._gate_position.get("raw_contracts", 0) or 0))
        
        # 获取网格底线（止损价）
        grid_floor = self.position_manager.state.grid_floor if self.position_manager.state else 0
        
        self.logger.debug(
            f"止损单检查: current_contracts={current_contracts}, grid_floor={grid_floor}, "
            f"sl_order_id={self._stop_loss_order_id}, sl_contracts={self._stop_loss_contracts}"
        )
        
        if grid_floor <= 0:
            self.logger.warning(f"⚠️ 网格底线无效 (grid_floor={grid_floor})，跳过止损单更新")
            return
        
        # 情况1: 无持仓，但有止损单 → 取消止损单
        if current_contracts == 0 and self._stop_loss_order_id:
            self.logger.info("📭 持仓已清空，取消止损单")
            await self._cancel_stop_loss_order()
            return
        
        # 情况2: 无持仓，无止损单 → 无需操作
        if current_contracts == 0:
            return
        
        # 情况3: 有持仓，持仓张数未变化且已有止损单 → 无需更新
        if current_contracts == self._stop_loss_contracts and self._stop_loss_order_id:
            self.logger.debug(f"止损单无需更新: {current_contracts}张 @ {grid_floor:.2f}")
            return
        
        # 情况4: 有持仓，持仓变化或无止损单 → 创建/更新止损单
        self.logger.info(
            f"🛡️ 准备更新止损单: {self._stop_loss_contracts}张 → {current_contracts}张 @ {grid_floor:.2f}"
        )
        
        # 先取消旧止损单
        if self._stop_loss_order_id:
            self.logger.info(f"🔄 取消旧止损单: ID={self._stop_loss_order_id}")
            await self._cancel_stop_loss_order()
        
        # 提交新止损单
        self.logger.info(f"📤 开始提交新止损单: {current_contracts}张 @ {grid_floor:.2f}")
        success = await self._submit_stop_loss_order(current_contracts, grid_floor)
        if not success:
            self.logger.error(f"❌ 止损单提交失败，将在下次循环重试")
    
    async def _submit_stop_loss_order(self, contracts: int, trigger_price: float) -> bool:
        """
        提交止损单到 Gate.io
        
        Args:
            contracts: 止损张数
            trigger_price: 触发价格（网格底线）
        
        Returns:
            bool: 是否成功
        """
        from key_level_grid.executor.base import Order, OrderSide, OrderType
        
        if contracts <= 0 or trigger_price <= 0:
            return False
        
        gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
        
        try:
            import uuid
            
            # 创建止损订单（使用 Order.create 或手动提供 order_id）
            sl_order = Order(
                order_id=f"sl_{uuid.uuid4().hex[:8]}",  # 生成唯一订单ID
                symbol=gate_symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,  # 触发后市价卖出
                quantity=contracts,  # 修正: 使用 quantity 而非 amount
                price=0,  # 市价止损，价格为 0
                reduce_only=True,
            )
            
            # 设置触发参数（计划委托）
            sl_order.metadata['order_mode'] = 'trigger'  # 标记为计划委托
            sl_order.metadata['triggerPrice'] = trigger_price
            sl_order.metadata['rule'] = 2  # 2 = <= (价格跌破触发)
            sl_order.metadata['is_stop_loss'] = True
            
            self.logger.info(
                f"📤 提交止损单: {contracts}张, 触发价={trigger_price:.2f}, "
                f"symbol={gate_symbol}"
            )
            
            success = await self._executor.submit_order(sl_order)
            
            if success:
                # 获取订单 ID（从 executor 或 order 中获取）
                order_id = getattr(sl_order, 'exchange_order_id', None) or sl_order.metadata.get('order_id', '')
                self._stop_loss_order_id = str(order_id) if order_id else "pending"
                self._stop_loss_contracts = contracts
                self._sl_order_updated_at = time.time()
                self.logger.info(f"✅ 止损单提交成功: ID={self._stop_loss_order_id}")
                return True
            else:
                self.logger.error(f"❌ 止损单提交失败: {sl_order.reject_reason}")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ 提交止损单异常: {e}", exc_info=True)
            return False
    
    async def _cancel_stop_loss_order(self) -> bool:
        """取消当前止损单"""
        if not self._stop_loss_order_id:
            return True
        
        gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
        
        try:
            # 尝试取消计划委托
            if hasattr(self._executor, 'cancel_plan_order'):
                success = await self._executor.cancel_plan_order(gate_symbol, self._stop_loss_order_id)
            else:
                # 回退到普通取消
                success = await self._executor.cancel_order(gate_symbol, self._stop_loss_order_id)
            
            if success:
                self.logger.info(f"✅ 止损单已取消: ID={self._stop_loss_order_id}")
            else:
                self.logger.warning(f"⚠️ 取消止损单失败: ID={self._stop_loss_order_id}")
            
            # 无论成功与否，清除本地状态
            self._stop_loss_order_id = None
            self._stop_loss_contracts = 0
            
            return success
            
        except Exception as e:
            self.logger.error(f"❌ 取消止损单异常: {e}")
            self._stop_loss_order_id = None
            self._stop_loss_contracts = 0
            return False
    
    async def _submit_take_profit_orders(self, position_usdt: float) -> None:
        """
        提交止盈卖单到 Gate (修正版：止盈单数量 = 已成交买单数量)
        
        逻辑：
        1. 获取 Gate 真实持仓（张数、均价）
        2. 计算每格张数（从 buy_orders 获取，与买单对称）
        3. 计算已成交网格数 = ceil(持仓张数 / 每格张数)
        4. 获取有效阻力位（高于均价），只取前 N 个
        5. 逐档分配止盈，防重复检查
        
        Args:
            position_usdt: 当前持仓价值 (USDT) - 仅用于日志
        """
        import math
        from key_level_grid.executor.base import Order, OrderSide, OrderType
        
        # ===== 1. 获取 Gate 真实持仓 =====
        # 先同步最新持仓数据
        await self._update_gate_position()
        await self._update_gate_orders()
        
        if not self._gate_position:
            self.logger.warning("⚠️ 无 Gate 持仓数据，无法生成止盈挂单")
            return
        
        # 调试：打印持仓详情
        self.logger.info(
            f"🔍 止盈-持仓详情: raw_contracts={self._gate_position.get('raw_contracts')}, "
            f"entry_price={self._gate_position.get('entry_price')}, "
            f"contract_size={self._gate_position.get('contract_size')}"
        )
        
        position_raw_contracts = int(float(self._gate_position.get("raw_contracts", 0) or 0))
        avg_entry_price = float(self._gate_position.get("entry_price", 0) or 0)
        contract_size = float(self._gate_position.get("contract_size", getattr(self, "_contract_size", 0.0001)) or 0.0001)
        position_btc = position_raw_contracts * contract_size
        
        if position_raw_contracts <= 0:
            self.logger.warning("⚠️ 持仓张数为 0，无法生成止盈挂单")
            return
        
        if avg_entry_price <= 0:
            self.logger.warning("⚠️ 持仓均价异常，无法生成止盈挂单")
            return
        
        # ===== 2. 获取每格张数（优先从 GridState 恢复，否则重新计算） =====
        state = self.position_manager.state
        if not state:
            self.logger.warning("⚠️ 无 GridState，无法生成止盈挂单")
            return
        
        buy_orders = state.buy_orders
        if not buy_orders:
            self.logger.warning("⚠️ 无买单信息，无法计算每格张数")
            return
        
        # 总是基于当前的 max_position_usdt 计算（确保与账户余额同步）
        num_grids = state.num_grids if state.num_grids > 0 else len(buy_orders)
        max_position_usdt = self.position_manager.position_config.max_position_usdt
        total_contracts = int(max_position_usdt / (avg_entry_price * contract_size)) if contract_size > 0 else 0
        per_grid_contracts = max(1, int(total_contracts / num_grids)) if total_contracts > 0 else 1
        self.logger.info(
            f"📊 止盈网格配置: max_position={max_position_usdt:.0f}U, "
            f"每档={per_grid_contracts}张, 网格数={num_grids}"
        )
        
        # ===== 3. 计算已成交网格数 =====
        filled_grids = math.ceil(position_raw_contracts / per_grid_contracts)
        
        # 上限检查：不能超过网格总数
        filled_grids = min(filled_grids, num_grids)
        
        self.logger.info(
            f"📊 止盈分析: 持仓={position_raw_contracts}张 (≈{position_btc:.6f}BTC), "
            f"每格={per_grid_contracts}张, 已成交网格={filled_grids}/{num_grids}"
        )
        
        # ===== 4. 获取有效阻力位 =====
        sell_orders = self.position_manager.state.sell_orders if self.position_manager.state else []
        valid_resistances = [
            o for o in sell_orders 
            if not o.is_filled and o.price > avg_entry_price
        ]
        
        if not valid_resistances:
            self.logger.warning(f"无有效阻力位（均价={avg_entry_price:.2f}）")
            return
        
        # 按价格从低到高排序
        valid_resistances.sort(key=lambda x: x.price)
        
        # 只取前 filled_grids 个阻力位（止盈单数量 = 已成交网格数）
        selected_resistances = valid_resistances[:filled_grids]
        num_tp_levels = len(selected_resistances)
        
        self.logger.info(
            f"🎯 止盈计划: 已成交{filled_grids}格 → 挂{num_tp_levels}档止盈, "
            f"均价={avg_entry_price:.2f}, 每档≈{per_grid_contracts}张"
        )
        
        # ===== 5. 检查已有止盈单（防重复 + 计算剩余可挂量） =====
        existing_sell_prices = set()
        existing_sell_contracts = 0  # 已挂止盈单总张数
        
        for order in self._gate_open_orders:
            if order.get("side") == "sell":
                existing_sell_prices.add(round(order.get("price", 0), 2))
                # 累加已挂止盈单的张数
                existing_sell_contracts += int(float(order.get("raw_contracts", 0) or 0))
        
        # 可挂止盈单的张数 = 持仓张数 - 已挂止盈单张数
        available_to_sell = position_raw_contracts - existing_sell_contracts
        
        self.logger.info(
            f"📊 止盈挂单检查: 持仓={position_raw_contracts}张, "
            f"已挂止盈={existing_sell_contracts}张, 可挂={available_to_sell}张"
        )
        
        if available_to_sell <= 0:
            self.logger.info(
                f"✅ 已有足够止盈单覆盖持仓，无需新增 "
                f"(持仓={position_raw_contracts}张, 已挂={existing_sell_contracts}张)"
            )
            self._tp_orders_submitted = True
            return
        
        gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
        
        # ===== 6. 逐档分配止盈（只分配可挂的张数） =====
        remaining_contracts = available_to_sell  # 改为只分配可挂的部分
        submitted_count = 0
        skipped_count = 0
        failed_count = 0
        
        for i, resistance in enumerate(selected_resistances):
            if remaining_contracts <= 0:
                break
            
            # 检查是否已有相同价位的挂单
            if round(resistance.price, 2) in existing_sell_prices:
                self.logger.debug(f"⏭️ 跳过已存在的止盈单 @ {resistance.price:.2f}")
                skipped_count += 1
                continue
            
            # 分配张数：每档等量，最后一档用完剩余
            if i == num_tp_levels - 1:
                tp_contracts = remaining_contracts
            else:
                tp_contracts = min(per_grid_contracts, remaining_contracts)
            
            tp_btc = tp_contracts * contract_size
            tp_usdt = tp_btc * resistance.price
            profit_pct = ((resistance.price - avg_entry_price) / avg_entry_price) * 100
            
            try:
                # 创建限价卖单 (reduce_only=True, quantity=张数)
                tp_order = Order.create(
                    symbol=gate_symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    quantity=tp_contracts,  # 张数（整数）
                    price=resistance.price,
                    reduce_only=True,
                )
                tp_order.metadata['order_mode'] = 'limit'
                tp_order.metadata['grid_id'] = resistance.grid_id
                tp_order.metadata['is_take_profit'] = True
                tp_order.metadata['source'] = resistance.source
                tp_order.metadata['contract_size'] = contract_size
                tp_order.metadata['target_contracts'] = tp_contracts
                
                success = await self._executor.submit_order(tp_order)
                
                if success:
                    submitted_count += 1
                    remaining_contracts -= tp_contracts
                    # 添加到已存在列表，防止同一批次重复
                    existing_sell_prices.add(round(resistance.price, 2))
                    self.logger.info(
                        f"✅ 止盈卖单 #{i+1}: {tp_contracts}张 @ {resistance.price:.2f} "
                        f"(+{profit_pct:.1f}%, ≈{tp_usdt:.0f}U)"
                    )
                else:
                    failed_count += 1
                    self.logger.error(f"❌ 止盈卖单 #{i+1} 失败: {tp_order.reject_reason}")
                    
            except Exception as e:
                failed_count += 1
                self.logger.error(f"❌ 止盈卖单 #{i+1} 异常: {e}")
        
        if submitted_count > 0:
            self._tp_orders_submitted = True
        
        self.logger.info(
            f"📊 止盈挂单结果: 成功={submitted_count}, 跳过={skipped_count}, "
            f"失败={failed_count}, 剩余={remaining_contracts}张"
        )
    
    async def _create_initial_grid(self, klines: List[Kline]) -> None:
        """
        创建初始网格
        
        基于当前价格和支撑/阻力位生成网格挂单
        """
        if self._grid_created or not self._current_state:
            return
        
        # 确保账户余额已更新，并用真实余额覆盖配置
        if self._balance_updated_at == 0:
            await self._update_account_balance()
        
        # 用真实余额覆盖配置（确保网格计算基于实际资金）
        pos_config = self.position_manager.position_config
        real_balance = self._account_balance.get("total", 0)
        if real_balance > 0 and pos_config.total_capital != real_balance:
            pos_config.total_capital = real_balance
            self.logger.info(f"📊 更新 total_capital 为真实余额: {real_balance:.2f} USDT")
        
        max_position = pos_config.max_position_usdt
        
        self.logger.info(
            f"📊 网格配置: 真实余额={real_balance:.2f}, "
            f"杠杆={pos_config.max_leverage}x, "
            f"使用率={pos_config.max_capital_usage:.0%}, "
            f"最大仓位={max_position:.2f} USDT"
        )
        
        current_price = self._current_state.close
        
        # 获取 1D K线用于多周期融合
        from key_level_grid.models import Timeframe
        klines_1d = None
        if Timeframe.D1 in self.config.kline_config.auxiliary_timeframes:
            klines_1d = self.kline_feed.get_cached_klines(Timeframe.D1)
        
        # 计算支撑位和阻力位
        resistance_calc = self.position_manager.resistance_calc
        primary_tf = self.config.kline_config.primary_timeframe.value
        
        resistances = resistance_calc.calculate_resistance_levels(
            current_price, klines, "long", klines_1d=klines_1d, primary_timeframe=primary_tf
        )
        supports = resistance_calc.calculate_support_levels(
            current_price, klines, klines_1d=klines_1d, primary_timeframe=primary_tf
        )
        
        if not supports:
            self.logger.warning("没有找到有效支撑位，暂不创建网格")
            return
        
        # 创建网格
        grid_state = self.position_manager.create_grid(
            current_price=current_price,
            support_levels=supports,
            resistance_levels=resistances
        )
        
        if grid_state:
            self._grid_created = True
            self.logger.info(
                f"✅ 网格创建成功: {len(grid_state.buy_orders)} 档买单, "
                f"{len(grid_state.sell_orders)} 档卖单, "
                f"底线={grid_state.grid_floor:.2f}"
            )
            
            # 实盘模式：提交真实限价单到交易所
            if not self.config.dry_run and self._executor:
                await self._submit_grid_orders(grid_state)
        else:
            self.logger.warning("网格创建失败，将在下一周期重试")
    
    async def _submit_grid_orders(self, grid_state, rebuild_mode: bool = False) -> None:
        """
        提交网格订单到交易所
        
        过滤规则：
        - 规则 B：Gate 上已有的挂单（价格容差 0.1%）
        - 规则 C：跳过 price >= avg_entry_price * 0.995（均价保护）
          - 方案A：当 rebuild_mode=True（网格重建）时，跳过该规则
        """
        import math
        from key_level_grid.executor.base import Order, OrderSide, OrderType
        
        # 符号格式转换：Binance BTCUSDT → Gate BTC_USDT
        binance_symbol = self.config.symbol
        gate_symbol = self._convert_to_gate_symbol(binance_symbol)
        
        self.logger.info(f"🚀 开始提交网格挂单到 Gate.io: {gate_symbol}")
        
        # ============================================
        # 1. 同步 Gate 挂单、持仓和余额
        # ============================================
        await self._update_gate_orders()
        await self._update_gate_position()
        await self._update_account_balance()
        
        # 调试：打印持仓数据
        self.logger.info(
            f"🔍 Gate 持仓数据: raw_contracts={self._gate_position.get('raw_contracts', 0)}, "
            f"entry_price={self._gate_position.get('entry_price', 0)}, "
            f"notional={self._gate_position.get('notional', 0)}"
        )
        
        # 获取 Gate 已有的买单价格
        gate_buy_prices = [
            o.get("price", 0) for o in self._gate_open_orders 
            if o.get("side") == "buy"
        ]
        
        self.logger.info(
            f"📋 Gate 已有买单: {len(gate_buy_prices)} 个, "
            f"价格: {[f'{p:.2f}' for p in sorted(gate_buy_prices, reverse=True)[:5]]}"
        )
        
        # ============================================
        # 1.5 余额预检查
        # ============================================
        available_balance = float(self._account_balance.get("free", 0) or 0)
        self.logger.info(f"💰 可用余额: {available_balance:.2f} USDT")
        
        # ============================================
        # 2. 设置保证金模式和杠杆
        # ============================================
        try:
            margin_mode = self.config.margin_mode
            leverage = self.config.leverage
            
            await self._executor.set_margin_mode(gate_symbol, margin_mode)
            self.logger.info(f"✅ 保证金模式设置为: {margin_mode}")
            
            await self._executor.set_leverage(gate_symbol, leverage)
            self.logger.info(f"✅ 杠杆倍数设置为: {leverage}x")
            
        except Exception as e:
            self.logger.warning(f"⚠️ 设置杠杆/保证金模式失败 (可能已有持仓): {e}")
        
        # ============================================
        # 3. 获取每档张数（优先从 GridState 恢复，否则重新计算）
        # ============================================
        num_grids = len(grid_state.buy_orders)
        if num_grids <= 0:
            self.logger.warning("无买单网格，跳过提交")
            return
        
        contract_size = getattr(self, "_contract_size", 0.0001) or 0.0001
        current_price = self._current_state.close if self._current_state else 0
        if current_price <= 0:
            current_price = grid_state.buy_orders[0].price
        
        # 总是基于当前的 max_position_usdt 计算（确保与账户余额同步）
        max_position_usdt = self.position_manager.position_config.max_position_usdt
        total_contracts = int(max_position_usdt / (current_price * contract_size)) if contract_size > 0 else 0
        contracts_per_grid = max(1, int(total_contracts / num_grids)) if total_contracts > 0 else 1
        
        # 检查是否与保存的配置一致，如有变化则更新
        saved_contracts = grid_state.per_grid_contracts
        if saved_contracts > 0 and saved_contracts != contracts_per_grid:
            self.logger.warning(
                f"⚠️ 网格配置变化: 保存={saved_contracts}张 → 当前={contracts_per_grid}张 "
                f"(max_pos={max_position_usdt:.0f}U), 使用新配置"
            )
        
        # 更新并保存
        grid_state.per_grid_contracts = contracts_per_grid
        grid_state.contract_size = contract_size
        grid_state.num_grids = num_grids
        self.position_manager._save_state()
        
        self.logger.info(
            f"📊 网格配置: max_position={max_position_usdt:.0f}U, "
            f"总张数≈{total_contracts}, 每档={contracts_per_grid}张"
        )
        
        per_grid_btc = contracts_per_grid * contract_size
        
        # ============================================
        # 4. 三层过滤：计算已成交网格数 + 均价保护
        # ============================================
        position_contracts = int(float(self._gate_position.get("raw_contracts", 0) or 0))
        avg_entry_price = float(self._gate_position.get("entry_price", 0) or 0)
        
        # 规则 A：计算已成交网格数
        filled_grids = 0
        if position_contracts > 0 and contracts_per_grid > 0:
            filled_grids = math.ceil(position_contracts / contracts_per_grid)
        
        # 规则 C：均价保护阈值（网格重建模式跳过）
        price_threshold = avg_entry_price * 0.995 if (avg_entry_price > 0 and not rebuild_mode) else 0
        
        self.logger.info(
            f"📊 过滤参数: 持仓={position_contracts}张, 已成交网格={filled_grids}, "
            f"均价={avg_entry_price:.2f}, 均价保护阈值={price_threshold:.2f}"
        )
        
        # ============================================
        # 5. 买单排序（按价格从高到低）
        # ============================================
        sorted_orders = sorted(grid_state.buy_orders, key=lambda x: x.price, reverse=True)
        
        # ============================================
        # 5.5 余额检查：如果余额不足以支撑一格，跳过所有买单
        # ============================================
        # 计算单格所需保证金（考虑杠杆）
        leverage = self.config.leverage or 20
        single_grid_usdt = contracts_per_grid * contract_size * current_price
        single_grid_margin = single_grid_usdt / leverage
        
        if available_balance < single_grid_margin:
            self.logger.warning(
                f"⚠️ 余额不足，跳过所有买单: 可用={available_balance:.2f}U, "
                f"单格需={single_grid_margin:.2f}U (杠杆{leverage}x)"
            )
            # 不返回，继续执行止盈单逻辑（如果有持仓）
            return
        
        # ============================================
        # 6. 提交买单（双重过滤：均价保护 + Gate 去重）
        # ============================================
        # 注意：移除了"规则 A（跳过前 N 个）"，因为它与"规则 C（均价保护）"重复
        # 均价保护更精确：只跳过 price >= avg_entry * 0.995 的买单
        
        submitted_count = 0
        skipped_exists = 0
        skipped_threshold = 0
        failed_count = 0
        
        for idx, order in enumerate(sorted_orders):
            if order.is_filled:
                continue
            
            # 规则 B：跳过 Gate 上已有的挂单（价格容差 0.1%）
            already_exists = any(
                abs(order.price - gate_price) / order.price < 0.001
                for gate_price in gate_buy_prices
            )
            if already_exists:
                skipped_exists += 1
                self.logger.debug(f"⏭️ 跳过 Gate 已有挂单: @ {order.price:.2f}")
                continue
            
            # 规则 C：跳过 price >= avg_entry * 0.995（均价保护）
            # 方案A：网格重建时 rebuild_mode=True，会把 price_threshold 置 0，从而不触发该过滤
            if price_threshold > 0 and order.price >= price_threshold:
                skipped_threshold += 1
                self.logger.debug(f"⏭️ 跳过均价保护: @ {order.price:.2f} >= {price_threshold:.2f}")
                continue
            
            # 通过所有过滤，提交订单
            try:
                target_value_usd = float(contracts_per_grid * contract_size * order.price)
                
                gate_order = Order.create(
                    symbol=gate_symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    price=order.price,
                    quantity=0,
                    pricing_mode="usdt",
                    target_value_usd=target_value_usd,
                )
                gate_order.metadata['order_mode'] = 'limit'
                gate_order.metadata['grid_id'] = order.grid_id
                gate_order.metadata['source'] = order.source
                gate_order.metadata['target_contracts'] = contracts_per_grid
                gate_order.metadata['contract_size'] = contract_size
                
                success = await self._executor.submit_order(gate_order)
                
                if success:
                    submitted_count += 1
                    self.logger.info(
                        f"✅ 网格买单 #{order.grid_id}: "
                        f"{contracts_per_grid}张 @ {order.price:.2f} (≈{target_value_usd:.0f}U)"
                    )
                else:
                    failed_count += 1
                    self.logger.error(
                        f"❌ 网格买单 #{order.grid_id} 失败: {gate_order.reject_reason}"
                    )
                    # 如果是余额不足，停止继续提交
                    if "余额" in str(gate_order.reject_reason) or "insufficient" in str(gate_order.reject_reason).lower():
                        self.logger.warning("⚠️ 余额不足，停止提交剩余买单")
                        break
                    
            except Exception as e:
                failed_count += 1
                self.logger.error(f"❌ 提交网格买单 #{order.grid_id} 异常: {e}")
        
        self.logger.info(
            f"📊 网格挂单完成: 新提交={submitted_count}, "
            f"跳过(已挂单)={skipped_exists}, 跳过(均价保护)={skipped_threshold}, "
            f"失败={failed_count}"
        )
    
    def _convert_to_gate_symbol(self, binance_symbol: str) -> str:
        """
        将 Binance 符号格式转换为 Gate 格式
        
        Binance: BTCUSDT
        Gate: BTC_USDT (或 BTC/USDT:USDT 用于永续合约)
        """
        # 常见交易对的转换
        if binance_symbol.endswith("USDT"):
            base = binance_symbol[:-4]  # 去掉 USDT
            # Gate 永续合约格式
            return f"{base}/USDT:USDT"
        
        return binance_symbol
    
    async def _on_kline_close(self, kline: Kline) -> None:
        """K线收盘回调"""
        self.logger.debug(
            f"K线收盘: {self.config.symbol} "
            f"O={kline.open} H={kline.high} L={kline.low} C={kline.close}"
        )
        
        # 获取完整K线数据
        klines = self.kline_feed.get_cached_klines(
            self.config.kline_config.primary_timeframe
        )
        
        if len(klines) < 170:
            return
        
        # 计算通道状态
        self._current_state = self.indicator.calculate(klines)
        
        # 生成信号
        signal = self.signal_generator.generate(self._current_state, klines)
        
        if signal is None:
            return
        
        # 过滤信号
        signal = self.filter_chain.filter(signal, klines)
        
        if signal is None:
            return
        
        # 突破验证
        is_breakout = signal.signal_type in [
            SignalType.BREAKOUT_LONG, SignalType.BREAKOUT_SHORT
        ]
        if is_breakout:
            is_long = signal.signal_type == SignalType.BREAKOUT_LONG
            result = self.breakout_filter.validate_breakout(
                self._current_state, klines, is_long
            )
            if not result.is_valid:
                self.logger.info(
                    f"突破验证失败: 评分={result.score}, "
                    f"详情={result.details}"
                )
                return
            signal.score = result.score
        
        # 多周期共振检查
        if self.config.filter_config.mtf_enabled:
            direction = "long" if signal.signal_type in [
                SignalType.BREAKOUT_LONG, SignalType.PULLBACK_LONG
            ] else "short"
            aligned, trends = await self.mtf_manager.check_alignment(direction)
            
            if not aligned:
                self.logger.info(f"多周期不共振，忽略信号")
                return
        
        # 信号通过所有验证
        self.logger.info(
            f"✅ 有效信号: {signal.signal_type.value}, "
            f"评分={signal.score}, 等级={signal.grade.value}"
        )
        
        # 回调通知
        if self._on_signal_callback:
            await self._on_signal_callback(signal)
        
        # 自动交易或等待确认
        if self.config.auto_trade and not self.config.tg_confirmation:
            await self._execute_signal(signal)
        else:
            self._pending_signal = signal
            self.logger.info("等待 Telegram 确认...")
    
    async def _execute_signal(self, signal: KeyLevelSignal) -> None:
        """执行信号"""
        if self.position_manager.state and self.position_manager.state.direction != "none":
            self.logger.warning("已有仓位，跳过新信号")
            return
        
        direction = "long" if signal.signal_type in [
            SignalType.BREAKOUT_LONG, SignalType.PULLBACK_LONG
        ] else "short"
        
        # 获取K线用于计算阻力位
        klines = self.kline_feed.get_cached_klines(
            self.config.kline_config.primary_timeframe
        )
        
        # 开仓
        position = self.position_manager.open_position(
            entry_price=signal.entry_price,
            stop_loss_price=signal.stop_loss,
            direction=direction,
            market_state=signal.market_state,
            klines=klines
        )
        
        if position:
            self.logger.info(
                f"开仓成功: {direction.upper()} @ {signal.entry_price:.4f}, "
                f"止损={signal.stop_loss:.4f}"
            )
            
            if self._on_trade_callback:
                await self._on_trade_callback({
                    "action": "open",
                    "signal": signal.to_dict(),
                    "position": position.to_dict()
                })
    
    async def _handle_stop_loss(self, result: Dict) -> None:
        """处理止损触发"""
        close_result = self.position_manager.close_position(
            result['actions'][0]['price'],
            reason='stop_loss'
        )
        
        self.logger.warning(
            f"止损触发! 盈亏={close_result['pnl_usdt']:.2f} USDT"
        )
        
        if self._on_trade_callback:
            await self._on_trade_callback({
                "action": "stop_loss",
                "result": close_result
            })
    
    async def _handle_action(self, action: Dict) -> None:
        """处理交易动作"""
        action_type = action.get('action')
        
        if action_type == 'take_profit':
            self.logger.info(
                f"止盈触发: {action['rr_multiple']:.1f}R, "
                f"平仓 {action['close_usdt']:.2f} USDT"
            )
        
        elif action_type == 'add_position':
            self.logger.info(
                f"加仓触发: {action['trigger']}, "
                f"加仓 {action['add_usdt']:.2f} USDT"
            )
        
        if self._on_trade_callback:
            await self._on_trade_callback(action)
    
    def confirm_signal(self) -> bool:
        """确认待处理信号 (TG 调用)"""
        if self._pending_signal is None:
            return False
        
        asyncio.create_task(self._execute_signal(self._pending_signal))
        self._pending_signal = None
        return True
    
    def reject_signal(self) -> bool:
        """拒绝待处理信号 (TG 调用)"""
        if self._pending_signal is None:
            return False
        
        self.logger.info(f"信号被拒绝: {self._pending_signal.signal_type.value}")
        self._pending_signal = None
        return True
    
    def set_callbacks(
        self,
        on_signal=None,
        on_trade=None
    ) -> None:
        """设置回调函数"""
        self._on_signal_callback = on_signal
        self._on_trade_callback = on_trade
    
    def get_status(self) -> Dict[str, Any]:
        """获取策略状态"""
        position_summary = self.position_manager.get_position_summary(
            self._current_state.close if self._current_state else 0
        )
        
        return {
            "running": self._running,
            "symbol": self.config.symbol,
            "current_price": self._current_state.close if self._current_state else None,
            "indicators": {
                "macd": self._current_state.macd if self._current_state else None,
                "rsi": self._current_state.rsi if self._current_state else None,
                "atr": self._current_state.atr if self._current_state else None,
                "adx": self._current_state.adx if self._current_state else None,
            },
            "position": position_summary,
            "pending_signal": self._pending_signal.to_dict() if self._pending_signal else None,
            "kline_stats": self.kline_feed.get_stats(),
        }
    
    def get_display_data(self) -> Dict[str, Any]:
        """获取显示面板数据"""
        state = self._current_state
        pos = self.position_manager.state
        
        # 周期信息
        kline_config = self.config.kline_config
        primary_tf = kline_config.primary_timeframe.value
        aux_tfs = [tf.value for tf in kline_config.auxiliary_timeframes]
        
        data = {
            "symbol": self.config.symbol,
            "timestamp": state.timestamp if state else None,
            "timeframe": {
                "primary": primary_tf,
                "auxiliary": aux_tfs,
                "display": f"{primary_tf} + {' + '.join(aux_tfs)}" if aux_tfs else primary_tf,
            },
        }
        
        # 价格数据
        if state:
            data["price"] = {
                "current": state.close,
                "open": state.open,
                "high": state.high,
                "low": state.low,
            }
            
            # 技术指标
            data["indicators"] = {
                "macd": state.macd,
                "macd_signal": state.macd_signal,
                "macd_histogram": state.macd_histogram,
                "rsi": state.rsi,
                "atr": state.atr,
                "adx": state.adx,
                "volume_ratio": state.volume_ratio,
            }
            
            # 实时计算阻力位和支撑位 (多周期融合)
            klines = self.kline_feed.get_cached_klines(
                self.config.kline_config.primary_timeframe
            )
            # 获取 1D K线用于多周期融合
            klines_1d = None
            from key_level_grid.models import Timeframe
            if Timeframe.D1 in self.config.kline_config.auxiliary_timeframes:
                klines_1d = self.kline_feed.get_cached_klines(Timeframe.D1)
            
            if len(klines) >= 50:
                resistance_calc = self.position_manager.resistance_calc
                primary_tf = self.config.kline_config.primary_timeframe.value
                
                # 阻力位始终是当前价格上方，支撑位始终是当前价格下方
                # 不管趋势方向如何
                resistances = resistance_calc.calculate_resistance_levels(
                    state.close, klines, "long", klines_1d=klines_1d, primary_timeframe=primary_tf
                )
                supports = resistance_calc.calculate_support_levels(
                    state.close, klines, klines_1d=klines_1d, primary_timeframe=primary_tf
                )
                
                data["resistance_levels"] = [
                    {
                        "price": r.price, 
                        "type": r.level_type.value, 
                        "strength": r.strength, 
                        "timeframe": getattr(r, 'timeframe', '4h'),
                        "source": getattr(r, 'source', ''),
                        "description": getattr(r, 'description', ''),
                    }
                    for r in resistances[:10]
                ]
                data["support_levels"] = [
                    {
                        "price": s.price, 
                        "type": s.level_type.value, 
                        "strength": s.strength, 
                        "timeframe": getattr(s, 'timeframe', '4h'),
                        "source": getattr(s, 'source', ''),
                        "description": getattr(s, 'description', ''),
                    }
                    for s in supports[:10]
                ]
        
        # 仓位信息
        if pos:
            data["position"] = {
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "size_usdt": pos.position_usdt,
                "unrealized_pnl": pos.unrealized_pnl,
            }
            if pos.stop_loss:
                data["stop_loss"] = {
                    "price": pos.stop_loss.stop_price,
                    "type": pos.stop_loss.stop_type.value,
                }
            if pos.take_profit_plan:
                data["take_profit"] = [
                    {"price": tp.price, "pct": tp.close_pct, "rr": tp.rr_multiple}
                    for tp in pos.take_profit_plan.levels if tp.close_pct > 0
                ]
            
            # 使用仓位中的阻力/支撑位覆盖 (GridState 中存储的是字典列表)
            data["resistance_levels"] = [
                {
                    "price": r.get("price", 0) if isinstance(r, dict) else r.price, 
                    "type": r.get("type", "resistance") if isinstance(r, dict) else getattr(r, 'level_type', 'resistance'), 
                    "strength": r.get("strength", 0) if isinstance(r, dict) else r.strength, 
                    "timeframe": r.get("timeframe", "4h") if isinstance(r, dict) else getattr(r, 'timeframe', '4h'),
                    "source": r.get("source", "") if isinstance(r, dict) else getattr(r, 'source', ''),
                    "description": r.get("description", "") if isinstance(r, dict) else getattr(r, 'description', ''),
                }
                for r in pos.resistance_levels[:10]
            ]
            data["support_levels"] = [
                {
                    "price": s.get("price", 0) if isinstance(s, dict) else s.price, 
                    "type": s.get("type", "support") if isinstance(s, dict) else getattr(s, 'level_type', 'support'), 
                    "strength": s.get("strength", 0) if isinstance(s, dict) else s.strength, 
                    "timeframe": s.get("timeframe", "4h") if isinstance(s, dict) else getattr(s, 'timeframe', '4h'),
                    "source": s.get("source", "") if isinstance(s, dict) else getattr(s, 'source', ''),
                    "description": s.get("description", "") if isinstance(s, dict) else getattr(s, 'description', ''),
                }
                for s in pos.support_levels[:10]
            ]
        
        # 交易历史 - 使用 Gate 真实成交记录
        data["trade_history"] = self._gate_trades[:10] if self._gate_trades else []
        
        # 账户信息 (V1.0: 模拟数据 / V1.1: 真实数据)
        data["account"] = self._get_account_display_data()
        
        # 持仓信息 (整合到新的结构)
        data["position"] = self._get_position_display_data(state)
        
        # 当前挂单 - 传入已计算的支撑/阻力位数据
        data["pending_orders"] = self._get_pending_orders_display(
            state, 
            data.get("support_levels", []),
            data.get("resistance_levels", [])
        )
        
        return data
    
    def _get_account_display_data(self) -> Dict[str, Any]:
        """获取账户信息显示数据"""
        pos_config = self.position_manager.position_config
        grid_config = self.position_manager.grid_config
        
        # 从仓位管理器获取网格状态
        grid_state = self.position_manager.state
        total_invested = grid_state.position_usdt if grid_state else 0
        
        # 账户余额: 优先使用真实余额，否则使用配置
        if self._account_balance.get("total", 0) > 0:
            # 使用从交易所获取的真实余额
            total_balance = self._account_balance["total"]
            available = self._account_balance["free"]
            frozen = self._account_balance["used"]
        else:
            # 回退到配置值
            total_balance = pos_config.total_capital
            available = pos_config.total_capital - total_invested
            frozen = total_invested
        
        # 计算最大仓位 (基于真实余额计算)
        max_position = total_balance * pos_config.max_leverage * pos_config.max_capital_usage
        
        # 计算网格底线和止损价格
        grid_floor = 0
        stop_loss_price = 0
        avg_entry_price = 0
        expected_avg_price = 0  # 预期/实际平均买入价格
        
        if grid_state and grid_state.grid_floor > 0:
            grid_floor = grid_state.grid_floor
            stop_loss_price = grid_floor
            avg_entry_price = grid_state.avg_entry_price
            
            # 若已有持仓，优先使用实际均价
            if grid_state.total_position_usdt > 0 and avg_entry_price > 0:
                expected_avg_price = avg_entry_price
            # 否则基于挂单价格估算均价
            elif grid_state.buy_orders:
                prices = [o.price for o in grid_state.buy_orders if o.price > 0]
                expected_avg_price = sum(prices) / len(prices) if prices else 0
        
        # 预计最大亏损 = 最大仓位 × (预期均价 - 止损价) / 预期均价
        max_loss = 0.0
        max_loss_pct = 0.0
        if expected_avg_price > 0 and stop_loss_price > 0:
            max_loss_pct = ((expected_avg_price - stop_loss_price) / expected_avg_price) * 100
            max_loss = max_position * (max_loss_pct / 100)
        
        return {
            "total_balance": total_balance,
            "available": available,
            "frozen": frozen,
            "grid_config": {
                "max_position": max_position,
                "max_leverage": pos_config.max_leverage,
                "max_capital_usage": pos_config.max_capital_usage,
                "grid_floor": grid_floor,
                "stop_loss_price": stop_loss_price,
                "expected_avg_price": expected_avg_price,  # 预期/实际均价
                "max_loss": max_loss,
                "max_loss_pct": max_loss_pct,
                "floor_buffer": grid_config.floor_buffer,
            },
            "grid_status": {
                "total_invested": total_invested,
                "pending_orders": 0,
                "filled_orders": 0,
            }
        }
    
    def _get_position_display_data(self, state: Optional[KeyLevelGridState]) -> Dict[str, Any]:
        """获取持仓信息显示数据 - 优先使用 Gate 真实持仓"""
        current_price = state.close if state else 0
        
        # 优先使用 Gate 真实持仓数据
        if self._gate_position and self._gate_position.get("contracts", 0) > 0:
            gate_pos = self._gate_position
            notional = gate_pos.get("notional", 0)
            entry_price = gate_pos.get("entry_price", 0)
            contracts = gate_pos.get("contracts", 0)
            unrealized_pnl = gate_pos.get("unrealized_pnl", 0)
            
            # 如果 notional 为 0，尝试从 contracts 和 entry_price 计算
            if notional == 0 and entry_price > 0:
                notional = contracts * entry_price
            
            # 网格底线 (从本地状态获取)
            grid_floor = 0
            pos = self.position_manager.state
            if pos and pos.support_levels:
                prices = [s.get('price', 0) if isinstance(s, dict) else s.price 
                          for s in pos.support_levels if (s.get('price', 0) if isinstance(s, dict) else s.price) > 0]
                if prices:
                    min_support = min(prices)
                    grid_floor = min_support * 0.995
            
            return {
                "side": "long",
                "qty": contracts,
                "avg_entry_price": entry_price,
                "value": notional,
                "unrealized_pnl": unrealized_pnl,
                "grid_floor": grid_floor,
            }
        
        # 回退：使用本地状态
        pos = self.position_manager.state
        if not pos or pos.position_usdt <= 0:
            return {}
        
        # 计算盈亏
        if pos.entry_price > 0 and current_price > 0:
            if pos.direction == "long":
                pnl = (current_price - pos.entry_price) * (pos.position_usdt / pos.entry_price)
            else:
                pnl = (pos.entry_price - current_price) * (pos.position_usdt / pos.entry_price)
        else:
            pnl = 0
        
        # 网格底线 (最低支撑位 × 0.995)
        grid_floor = 0
        if pos.support_levels:
            prices = [s.get('price', 0) if isinstance(s, dict) else s.price 
                      for s in pos.support_levels if (s.get('price', 0) if isinstance(s, dict) else s.price) > 0]
            if prices:
                min_support = min(prices)
                grid_floor = min_support * 0.995
        
        return {
            "side": pos.direction,
            "qty": pos.position_usdt / pos.entry_price if pos.entry_price > 0 else 0,
            "avg_entry_price": pos.entry_price,
            "value": pos.position_usdt,
            "unrealized_pnl": pnl,
            "grid_floor": grid_floor,
        }
    
    def _get_pending_orders_display(
        self, 
        state: Optional[KeyLevelGridState],
        support_levels: List[Dict] = None,
        resistance_levels: List[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        获取当前挂单显示数据 (V2.3 简化版)
        
        优先使用 Gate 真实挂单；若无真实挂单，则使用网格状态/计划挂单
        """
        if not state:
            return []
        
        # 1) 实盘模式且有同步到 Gate 挂单时，优先展示真实挂单
        # Gate 挂单的 amount 已在 _update_gate_orders 中正确计算为 USDT 价值
        if not self.config.dry_run and self._gate_open_orders:
            orders = []
            for o in self._gate_open_orders:
                orders.append({
                    "side": o.get("side", ""),
                    "price": o.get("price", 0),
                    "amount": o.get("amount", 0),  # 已计算为 USDT 价值
                    "contracts": o.get("contracts", 0),  # 原始张数
                    "status": o.get("status", "pending"),
                    "source": "Gate",
                    "strength": 0,
                    "order_id": o.get("id", ""),
                })
            buy_orders = sorted([o for o in orders if o.get("side") == "buy"], key=lambda x: x["price"], reverse=True)
            sell_orders = sorted([o for o in orders if o.get("side") == "sell"], key=lambda x: x["price"], reverse=True)
            return sell_orders + buy_orders
        
        # 2) 回退：使用本地网格状态，保证挂单与显示一致且不随实时支撑数量跳变
        orders = []
        pos_state = self.position_manager.state
        if pos_state:
            buy_orders = [
                {
                    "side": "buy",
                    "price": o.price,
                    "amount": o.amount_usdt,
                    "status": "filled" if o.is_filled else "pending",
                    "source": o.source,
                    "strength": o.strength,
                }
                for o in sorted(pos_state.buy_orders, key=lambda x: x.price, reverse=True)
            ]
            sell_orders = [
                {
                    "side": "sell",
                    "price": o.price,
                    "amount": o.amount_usdt,
                    "status": "filled" if o.is_filled else "pending",
                    "source": o.source,
                    "strength": o.strength,
                }
                for o in sorted(pos_state.sell_orders, key=lambda x: x.price, reverse=True)
            ]
            return buy_orders + sell_orders
        
        # 3) 若尚未建网格，则回退使用当前计算的支撑/阻力位生成初始挂单
        config = self.position_manager.position_config
        support_levels = support_levels or []
        resistance_levels = resistance_levels or []
        
        min_strength = getattr(self.position_manager.resistance_config, 'min_strength', 80)
        strong_supports = [
            s for s in support_levels 
            if s.get("strength", 0) >= min_strength and s.get("price", 0) < state.close
        ]
        strong_resistances = [
            r for r in resistance_levels 
            if r.get("strength", 0) >= min_strength and r.get("price", 0) > state.close
        ]
        
        strong_supports.sort(key=lambda x: -x.get("price", 0))
        strong_resistances.sort(key=lambda x: x.get("price", 0))
        
        max_grids = getattr(self.position_manager.grid_config, 'max_grids', 10)
        strong_supports = strong_supports[:max_grids]
        strong_resistances = strong_resistances[:max_grids]
        
        max_position = config.total_capital * config.max_leverage * config.max_capital_usage
        if not strong_supports:
            return []
        
        per_grid_usdt = max_position / len(strong_supports)
        for support in strong_supports:
            orders.append({
                "side": "buy",
                "price": support.get("price", 0),
                "amount": per_grid_usdt,
                "status": "pending",
                "source": support.get("source", "support"),
                "strength": support.get("strength", 0),
            })
        
        if strong_resistances:
            per_tp_usdt = max_position / len(strong_resistances)
            for resistance in strong_resistances:
                orders.append({
                    "side": "sell",
                    "price": resistance.get("price", 0),
                    "amount": per_tp_usdt,
                    "status": "pending",
                    "source": resistance.get("source", "resistance"),
                    "strength": resistance.get("strength", 0),
                })
        
        return orders
    
    def _generate_trade_plan_display(self, state: Optional[KeyLevelGridState]) -> Dict[str, Any]:
        """生成交易执行计划显示数据"""
        if state is None:
            return {}
        
        # 如果有待处理信号，显示该信号的计划
        if self._pending_signal:
            signal = self._pending_signal
            
            # 计算风险
            entry = signal.entry_price
            stop = signal.stop_loss
            risk_pct = abs(entry - stop) / entry
            risk_usdt = self.config.position_config.total_capital * self.config.position_config.risk_per_trade
            position_usdt = risk_usdt / risk_pct if risk_pct > 0 else 0
            
            return {
                "signal_type": signal.signal_type.value,
                "score": signal.score,
                "grade": signal.grade.value,
                "entry_plan": [
                    {"price": entry, "pct": 0.30, "filled": False},
                    {"price": entry * 0.95, "pct": 0.40, "filled": False},
                    {"price": entry * 1.08, "pct": 0.30, "filled": False},
                ],
                "stop_plan": {
                    "initial": stop,
                    "type": "通道止损",
                    "risk_usdt": risk_usdt,
                },
                "tp_plan": [
                    {"price": tp, "pct": 0.40 if i == 0 else 0.30 if i == 1 else 0.20, "rr": (tp - entry) / (entry - stop) if entry != stop else 0}
                    for i, tp in enumerate(signal.take_profits[:3])
                ],
                "expected_rr": 2.5,
            }
        
        # 如果有持仓，显示当前仓位的计划
        if self.position_manager.state:
            pos = self.position_manager.state
            return {
                "signal_type": f"持仓中 ({pos.direction.upper()})",
                "score": 0,
                "grade": "-",
                "entry_plan": [
                    {
                        "price": b.fill_price or pos.entry_price * (1 + b.price_offset),
                        "pct": b.size_pct,
                        "filled": b.is_filled
                    }
                    for b in pos.batches
                ],
                "stop_plan": {
                    "initial": pos.stop_loss.stop_price if pos.stop_loss else 0,
                    "type": pos.stop_loss.stop_type.value if pos.stop_loss else "N/A",
                    "risk_usdt": pos.position_usdt * 0.10,
                },
                "tp_plan": [
                    {"price": tp.price, "pct": tp.close_pct, "rr": tp.rr_multiple}
                    for tp in (pos.take_profit_plan.levels if pos.take_profit_plan else [])[:3]
                ],
                "expected_rr": 2.0,
            }
        
        # 无信号无仓位，返回空
        return {}
    
    # ===== Telegram 通知方法 =====
    
    async def _send_startup_notification(self) -> None:
        """发送启动通知"""
        if not self._notifier:
            return
        
        try:
            # 获取显示数据
            data = self.get_display_data()
            
            # 当前价格
            price_obj = data.get("price", {})
            current_price = price_obj.get("current", 0) if isinstance(price_obj, dict) else 0
            
            # 账户信息
            account_data = data.get("account", {})
            account = {
                "total_balance": account_data.get("total_balance", 0),
                "available": account_data.get("available", 0),
                "frozen": account_data.get("frozen", 0),
            }
            
            # 持仓信息
            pos_data = data.get("position", {})
            position = {
                "value": pos_data.get("value", pos_data.get("notional", 0)),
                "avg_price": pos_data.get("avg_entry_price", pos_data.get("avg_price", 0)),
                "unrealized_pnl": pos_data.get("unrealized_pnl", 0),
                "pnl_pct": 0,
            }
            if position["value"] > 0 and position["unrealized_pnl"] != 0:
                position["pnl_pct"] = position["unrealized_pnl"] / position["value"]
            
            # 挂单信息
            pending_orders = data.get("pending_orders", [])
            orders = []
            for o in pending_orders:
                orders.append({
                    "side": o.get("side", ""),
                    "price": o.get("price", 0),
                    "amount": o.get("amount", 0),
                })
            
            # 网格配置
            grid_cfg = account_data.get("grid_config", {})
            grid_config = {
                "max_position": grid_cfg.get("max_position", 0),
                "leverage": self.config.leverage,
                "num_grids": self.position_manager.grid_config.max_grids,
            }
            
            # 关键价位
            resistance_levels = data.get("resistance_levels", [])
            support_levels = data.get("support_levels", [])
            
            await self._notifier.notify_startup(
                symbol=self.config.symbol,
                exchange=self.config.exchange,
                current_price=current_price,
                account=account,
                position=position,
                pending_orders=orders,
                grid_config=grid_config,
                resistance_levels=resistance_levels,
                support_levels=support_levels,
            )
        except Exception as e:
            self.logger.error(f"发送启动通知失败: {e}")
    
    async def _send_shutdown_notification(self, reason: str = "手动停止") -> None:
        """发送停止通知"""
        if not self._notifier:
            return
        
        try:
            # 获取持仓信息
            position = None
            if self._gate_position and self._gate_position.get("contracts", 0) > 0:
                position = {
                    "value": self._gate_position.get("notional", 0),
                }
            
            await self._notifier.notify_shutdown(
                reason=reason,
                position=position,
                total_pnl=self._notifier._stats.get("realized_pnl", 0) if self._notifier else 0,
            )
        except Exception as e:
            self.logger.error(f"发送停止通知失败: {e}")
    
    async def _notify_order_filled(
        self,
        side: str,
        fill_price: float,
        fill_amount: float,
        grid_index: int = 0,
        total_grids: int = 0,
        realized_pnl: float = 0,
    ) -> None:
        """发送成交通知"""
        if not self._notifier:
            return
        
        try:
            # 获取成交后持仓
            position_after = None
            if self._gate_position and self._gate_position.get("contracts", 0) > 0:
                gate_pos = self._gate_position
                value = gate_pos.get("notional", 0)
                unrealized_pnl = gate_pos.get("unrealized_pnl", 0)
                position_after = {
                    "value": value,
                    "avg_price": gate_pos.get("entry_price", 0),
                    "unrealized_pnl": unrealized_pnl,
                    "pnl_pct": unrealized_pnl / value if value > 0 else 0,
                }
            
            await self._notifier.notify_order_filled(
                side=side,
                symbol=self.config.symbol,
                fill_price=fill_price,
                fill_amount=fill_amount,
                grid_index=grid_index,
                total_grids=total_grids,
                position_after=position_after,
                realized_pnl=realized_pnl,
            )
        except Exception as e:
            self.logger.error(f"发送成交通知失败: {e}")
    
    async def _notify_grid_rebuild(
        self,
        reason: str,
        old_anchor: float,
        new_anchor: float,
        new_orders: list,
    ) -> None:
        """发送网格重建通知"""
        if not self._notifier:
            return
        
        try:
            orders = []
            for o in new_orders:
                orders.append({
                    "side": o.get("side", "buy"),
                    "price": o.get("price", 0),
                    "amount": o.get("amount", 0),
                })
            
            await self._notifier.notify_grid_rebuild(
                symbol=self.config.symbol,
                reason=reason,
                old_anchor=old_anchor,
                new_anchor=new_anchor,
                new_orders=orders,
            )
        except Exception as e:
            self.logger.error(f"发送网格重建通知失败: {e}")
    
    async def _check_telegram_bot(self) -> None:
        """定期检查 Telegram Bot 状态，如果断开则重连"""
        import time
        
        # 每 5 分钟检查一次
        if time.time() - self._tg_bot_checked_at < 300:
            return
        
        self._tg_bot_checked_at = time.time()
        
        if not self._tg_bot:
            return
        
        try:
            if not self._tg_bot.is_running():
                self.logger.warning("⚠️ Telegram Bot 已断开，正在重连...")
                await self._tg_bot.restart()
                self.logger.info("✅ Telegram Bot 重连成功")
        except Exception as e:
            self.logger.error(f"Telegram Bot 重连失败: {e}")
    
    async def _notify_error(
        self,
        error_type: str,
        error_msg: str,
        context: str = "",
        suggestion: str = "",
    ) -> None:
        """发送错误通知"""
        if not self._notifier:
            return
        
        try:
            await self._notifier.notify_error(
                error_type=error_type,
                error_msg=error_msg,
                context=context,
                suggestion=suggestion,
            )
        except Exception as e:
            self.logger.error(f"发送错误通知失败: {e}")

