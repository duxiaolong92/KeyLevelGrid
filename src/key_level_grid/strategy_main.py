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
from key_level_grid.signal import SignalConfig, KeyLevelSignal, KeyLevelSignalGenerator
from key_level_grid.gate_kline_feed import GateKlineFeed
from key_level_grid.models import Kline, KlineFeedConfig, Timeframe, KeyLevelGridState
from key_level_grid.mtf_manager import MultiTimeframeManager
from key_level_grid.utils.trade_store import TradeStore
from key_level_grid.position import (
    GridConfig, StopLossConfig, TakeProfitConfig, ResistanceConfig, ActiveFill,
    PositionConfig, KeyLevelPositionManager
)
from key_level_grid.strategy.display import DisplayDataGenerator
from key_level_grid.strategy.notifications import NotificationHelper
from key_level_grid.strategy.exchange_sync import ExchangeSyncManager
from key_level_grid.strategy.risk import RiskManager
from key_level_grid.strategy.recon import ReconEventManager


@dataclass
class KeyLevelGridConfig:
    """关键位网格策略完整配置"""
    # 交易配置
    symbol: str = "XPLUSDT"
    exchange: str = "binance"
    market_type: str = "futures"  # futures / spot
    margin_mode: str = "cross"    # cross (全仓) / isolated (逐仓)
    leverage: int = 3             # 杠杆倍数
    default_contract_size: float = 1.0  # 合约大小后备值（仅当 API 获取失败时使用）
    
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
    resistance_config: ResistanceConfig = None  # 支撑/阻力配置
    
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
        if self.resistance_config is None:
            self.resistance_config = ResistanceConfig()


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
        self.kline_feed = GateKlineFeed(config.kline_config)
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
            resistance_config=config.resistance_config if config.resistance_config else ResistanceConfig(),
            symbol=config.symbol,
            exchange=config.exchange,
        )
        
        # 🆕 V3.0: LevelCalculator (MTF 水位生成)
        self._level_calculator = None
        self._v3_config: Dict = {}  # 存储原始配置用于 V3.0
        
        # Telegram 通知（先初始化，供执行器挂钩使用）
        self._notifier: Optional["NotificationManager"] = None
        self._tg_bot = None  # Telegram Bot 实例
        self._tg_bot_checked_at: float = 0  # Bot 健康检查时间戳
        self._config_path: Optional[str] = None
        
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
        self._last_position_contracts: Optional[int] = None  # 上次持仓张数（None 表示未初始化）
        self._tp_orders_submitted: bool = False  # 止盈单是否已提交
        self._need_rebuild_after_fill: bool = False  # 兼容保留
        self._last_fill_at: float = 0  # 上次成交时间（用于成交后延迟重建）
        
        # 止损单状态
        self._stop_loss_order_id: Optional[str] = None  # 当前止损单 ID
        self._stop_loss_contracts: float = 0  # 止损单覆盖的张数
        self._stop_loss_trigger_price: float = 0  # 止损单实际触发价（从交易所同步）
        self._sl_order_updated_at: float = 0  # 止损单更新时间
        self._sl_synced_from_exchange: bool = False  # 是否已从交易所同步止损单
        self._sl_last_entry_price: float = 0  # 止损前的入场价（用于计算亏损）
        
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
        self._last_rebuild_at = 0.0  # 兼容保留
        self._recon_last_run_at: float = 0.0
        self._grid_lock_until: float = 0.0
        self._grid_lock = asyncio.Lock()
        self._last_trade_ids: set = set()
        self._last_position_btc: Optional[float] = None
        self._last_position_avg_price: float = 0.0
        self._last_position_unrealized_pnl: float = 0.0
        
        # 回调
        self._on_signal_callback = None
        self._on_trade_callback = None
        
        # 初始化成交账本
        trade_store_dir = os.path.join("state", "key_level_grid", config.exchange)
        trade_store_file = os.path.join(trade_store_dir, f"{config.symbol.lower()}_trades.jsonl")
        self.trade_store = TradeStore(trade_store_file)
        
        # 初始化展示数据生成器
        self._display_generator = DisplayDataGenerator(
            position_manager=self.position_manager,
            config=self.config,
        )
        
        # 初始化 Telegram 通知
        self._init_notifier()
        
        # 初始化通知助手
        self._notification_helper = NotificationHelper(
            notifier=self._notifier,
            config=self.config,
            position_manager=self.position_manager,
            get_display_data_func=self.get_display_data,
        )
        if self._tg_bot:
            self._notification_helper.set_tg_bot(self._tg_bot)
        
        # 初始化交易所数据同步管理器
        self._exchange_sync = ExchangeSyncManager(
            executor=self._executor,
            config=self.config,
            position_manager=self.position_manager,
            notifier=self._notifier,
        )
        
        # 初始化风控管理器
        self._risk_manager = RiskManager(
            executor=self._executor,
            config=self.config,
            position_manager=self.position_manager,
            notifier=self._notifier,
        )
        
        # 初始化 Recon/Event 双轨道管理器
        self._recon_manager = ReconEventManager(
            position_manager=self.position_manager,
            executor=self._executor,
            config=self.config,
            trade_store=self.trade_store,
            notifier=self._notifier,
        )
        # 设置回调
        self._recon_manager.set_callbacks(
            notify_order_filled=self._on_order_filled_callback,
            mark_level_filled=self._mark_level_filled,
            mark_level_idle=self._mark_level_idle,
        )
    
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
        
        if self._executor and self._notifier:
            self._executor.set_notifier(self._notifier)
    
    # ============================================
    # 🆕 V3.0 LevelCalculator 集成
    # ============================================
    
    def _is_v3_enabled(self) -> bool:
        """检查是否启用 V3.0 水位生成"""
        return self._v3_config.get("level_generation", {}).get("enabled", False)
    
    @property
    def level_calculator(self):
        """
        V3.0: 延迟初始化 LevelCalculator
        
        Returns:
            LevelCalculator 实例
        """
        if self._level_calculator is None and self._is_v3_enabled():
            from key_level_grid.level_calculator import LevelCalculator
            self._level_calculator = LevelCalculator(self._v3_config)
            self.logger.info("🆕 [V3.0] LevelCalculator 已初始化")
        return self._level_calculator
    
    def _calculate_levels_v3(
        self,
        klines_dict: Dict[str, List],
        current_price: float,
    ) -> tuple:
        """
        使用 V3.0 LevelCalculator 计算支撑/阻力位
        
        Args:
            klines_dict: 多周期 K 线数据
            current_price: 当前价格
        
        Returns:
            (supports, resistances) 元组
        """
        from key_level_grid.analysis.resistance import PriceLevel
        from key_level_grid.core.types import LevelType
        
        calculator = self.level_calculator
        if calculator is None:
            self.logger.warning("[V3.0] LevelCalculator 未初始化，回退到 V2.0")
            return None, None
        
        # 转换 K 线格式
        klines_by_tf = {}
        for tf, klines in klines_dict.items():
            klines_by_tf[tf] = [
                {
                    "timestamp": getattr(k, "timestamp", 0),
                    "open": getattr(k, "open", 0),
                    "high": getattr(k, "high", 0),
                    "low": getattr(k, "low", 0),
                    "close": getattr(k, "close", 0),
                    "volume": getattr(k, "volume", 0),
                }
                for k in klines
            ]
        
        # 生成支撑位
        support_levels = calculator.generate_target_levels(
            klines_by_tf=klines_by_tf,
            current_price=current_price,
            role="support",
            max_levels=20,
        )
        
        # 生成阻力位
        resistance_levels = calculator.generate_target_levels(
            klines_by_tf=klines_by_tf,
            current_price=current_price,
            role="resistance",
            max_levels=20,
        )
        
        # 转换为 PriceLevel 格式
        supports = []
        if support_levels:
            for price, score in support_levels:
                supports.append(PriceLevel(
                    price=price,
                    level_type=LevelType.SWING_LOW,  # 支撑位
                    strength=score.final_score,
                    source="+".join(score.source_timeframes) if score.source_timeframes else "v3",
                    timeframe="multi" if len(score.source_timeframes) > 1 else (score.source_timeframes[0] if score.source_timeframes else "4h"),
                ))
        
        resistances = []
        if resistance_levels:
            for price, score in resistance_levels:
                resistances.append(PriceLevel(
                    price=price,
                    level_type=LevelType.SWING_HIGH,  # 阻力位
                    strength=score.final_score,
                    source="+".join(score.source_timeframes) if score.source_timeframes else "v3",
                    timeframe="multi" if len(score.source_timeframes) > 1 else (score.source_timeframes[0] if score.source_timeframes else "4h"),
                ))
        
        self.logger.info(f"[V3.2.5] 生成水位: {len(supports)} 支撑, {len(resistances)} 阻力")
        
        # 输出详细水位表
        if supports:
            self.logger.info("=" * 60)
            self.logger.info("📉 支撑位列表:")
            self.logger.info(f"{'价格':>12} | {'评分':>6} | {'来源':>15} | 距当前")
            self.logger.info("-" * 60)
            for lvl in supports[:10]:  # 只显示前10个
                dist_pct = (lvl.price - current_price) / current_price * 100
                self.logger.info(f"{lvl.price:>12.2f} | {lvl.strength:>6.1f} | {lvl.source:>15} | {dist_pct:>+.2f}%")
        
        if resistances:
            self.logger.info("=" * 60)
            self.logger.info("📈 阻力位列表:")
            self.logger.info(f"{'价格':>12} | {'评分':>6} | {'来源':>15} | 距当前")
            self.logger.info("-" * 60)
            for lvl in resistances[:10]:  # 只显示前10个
                dist_pct = (lvl.price - current_price) / current_price * 100
                self.logger.info(f"{lvl.price:>12.2f} | {lvl.strength:>6.1f} | {lvl.source:>15} | {dist_pct:>+.2f}%")
        else:
            self.logger.warning("⚠️ 阻力位为空!")
        
        self.logger.info("=" * 60)
        
        return supports, resistances
    
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
                quota_event=notify_raw.get('quota_event', True),
                risk_warning=notify_raw.get('risk_warning', True),
                near_stop_loss_pct=notify_raw.get('near_stop_loss_pct', 0.02),
                daily_summary=notify_raw.get('daily_summary', True),
                daily_summary_time=notify_raw.get('daily_summary_time', '20:00'),
                heartbeat=notify_raw.get('heartbeat', False),
                heartbeat_interval_hours=notify_raw.get('heartbeat_interval_hours', 4),
                heartbeat_idle_sec=notify_raw.get('heartbeat_idle_sec', 3600),
                position_flux=notify_raw.get('position_flux', True),
                order_sync=notify_raw.get('order_sync', True),
                system_info=notify_raw.get('system_info', True),
                system_alert=notify_raw.get('system_alert', True),
                silent_mode=notify_raw.get('silent_mode', True),
                merge_fill_window_sec=notify_raw.get('merge_fill_window_sec', 5),
                min_notify_interval_sec=notify_raw.get('min_notify_interval_sec', 5),
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
            if self._executor:
                self._executor.set_notifier(self._notifier)
            
            self.logger.info("📱 Telegram 通知已启用")
        except ImportError as e:
            self.logger.warning(f"⚠️ Telegram 模块导入失败: {e}")
        except Exception as e:
            self.logger.error(f"❌ 初始化 Telegram 通知失败: {e}")
    
    @classmethod
    def from_yaml(cls, config_path: str) -> "KeyLevelGridStrategy":
        """从 YAML 文件加载配置 (V2.3 简化版)"""
        logger = get_logger(__name__)
        with open(config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
        
        # 读取 config.json 覆盖（若存在）
        import json
        from pathlib import Path
        config_json_path = Path(config_path).with_suffix(".json")
        if config_json_path.exists():
            try:
                with open(config_json_path, "r", encoding="utf-8") as jf:
                    json_config = json.load(jf)
                if isinstance(json_config, dict):
                    def _deep_update(base: dict, updates: dict) -> dict:
                        for k, v in updates.items():
                            if isinstance(v, dict) and isinstance(base.get(k), dict):
                                base[k] = _deep_update(base.get(k, {}), v)
                            else:
                                base[k] = v
                        return base
                    raw_config = _deep_update(raw_config, json_config)
                    logger.info(f"[Config] 已加载 config.json 覆盖: {config_json_path}")
            except Exception as e:
                logger.warning(f"[Config] 读取 config.json 失败: {e}")
        
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
        # 支撑/阻力配置
        resistance_config = ResistanceConfig(
            min_strength=resistance_raw.get('min_strength', 80),
            swing_lookbacks=resistance_raw.get('swing_lookbacks', [5, 13, 34]),
            fib_ratios=resistance_raw.get('fib_ratios', [0.382, 0.5, 0.618, 1.0, 1.618]),
            merge_tolerance=resistance_raw.get('merge_tolerance', 0.005),
            min_distance_pct=resistance_raw.get('min_distance_pct', 0.005),
            max_distance_pct=resistance_raw.get('max_distance_pct', 0.30),
        )
        logger.info(
            "[Config] 支撑/阻力配置: min_strength=%s, min_distance_pct=%s, max_distance_pct=%s, merge_tolerance=%s",
            resistance_config.min_strength,
            resistance_config.min_distance_pct,
            resistance_config.max_distance_pct,
            resistance_config.merge_tolerance,
        )
        
        # V2.3: 仓位配置 (网格模式)
        pos_raw = raw_config.get('position', {})
        # 杠杆优先使用 trading.leverage，确保两者一致
        trading_leverage = trading.get('leverage', 3)
        position_leverage = pos_raw.get('max_leverage', trading_leverage)
        # 如果 position.max_leverage 未设置或与 trading.leverage 不同，使用 trading.leverage
        if position_leverage != trading_leverage:
            logger.warning(
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
        logger.info(
            "[Config] 仓位配置: max_leverage=%sx, max_capital_usage=%s (total_capital 将在启动后从交易所读取)",
            position_config.max_leverage,
            position_config.max_capital_usage,
        )
        
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
            sell_quota_ratio=grid_raw.get('sell_quota_ratio', 0.7),
            min_profit_pct=grid_raw.get('min_profit_pct', 0.005),
            buy_price_buffer_pct=grid_raw.get('buy_price_buffer_pct', 0.002),
            sell_price_buffer_pct=grid_raw.get('sell_price_buffer_pct', 0.002),
            base_amount_per_grid=grid_raw.get('base_amount_per_grid', 1.0),
            base_position_locked=grid_raw.get('base_position_locked', 0.0),
            max_fill_per_level=grid_raw.get('max_fill_per_level', 1),
            recon_interval_sec=grid_raw.get('recon_interval_sec', 30),
            order_action_timeout_sec=grid_raw.get('order_action_timeout_sec', 10),
            restore_state_enabled=grid_raw.get('restore_state_enabled', True),
        )
        
        logger.info(
            "[Config] 缓冲参数: buy_price_buffer_pct=%s, sell_price_buffer_pct=%s",
            grid_config.buy_price_buffer_pct,
            grid_config.sell_price_buffer_pct,
        )
        
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
            default_contract_size=trading.get('default_contract_size', 1.0),
            api_key_env=api_config.get('key_env', ''),
            api_secret_env=api_config.get('secret_env', ''),
            kline_config=kline_config,
            indicator_config=indicator_config,
            signal_config=signal_config,
            position_config=position_config,
            grid_config=grid_config,
            resistance_config=resistance_config,
            dry_run=raw_config.get('dry_run', True),
            tg_enabled=tg_enabled,
            tg_bot_token=tg_bot_token,
            tg_chat_id=tg_chat_id,
            tg_notify_config=tg_notify_config,
        )
        
        instance = cls(config)
        instance._config_path = config_path
        
        # 🆕 保存完整原始配置，供显示面板等使用
        instance._raw_config = raw_config
        
        # 🆕 V3.0: 存储原始配置用于 LevelCalculator
        level_gen_config = grid_raw.get("level_generation", {})
        instance._v3_config = {
            "level_generation": level_gen_config,
            "resistance": resistance_raw,
            "grid": grid_raw,
        }
        
        # 检查是否启用 V3.0
        v3_enabled = level_gen_config.get("enabled", False)
        logger.info(f"[V3.0] level_generation 配置: enabled={v3_enabled}")
        if v3_enabled:
            logger.info("🆕 [V3.0] LevelCalculator 已启用")
            # 打印关键配置
            scoring = level_gen_config.get("scoring", {})
            manual_boundary = level_gen_config.get("manual_boundary", {})
            logger.info(f"[V3.0] min_score_threshold={scoring.get('min_score_threshold', 'N/A')}")
            logger.info(f"[V3.0] manual_boundary: enabled={manual_boundary.get('enabled')}, upper={manual_boundary.get('upper_price')}, lower={manual_boundary.get('lower_price')}")
        else:
            logger.info("[V3.0] LevelCalculator 未启用，使用旧版 ResistanceCalculator")
        
        return instance
    
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
                    await self._notification_helper.send_startup_notification(gate_position=self._gate_position)
                    self._startup_notified = True
                
                await asyncio.sleep(self.config.kline_config.update_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"策略更新异常: {e}", exc_info=True)
                # 发送错误通知
                await self._notification_helper.notify_error("StrategyError", str(e), "主循环更新")
                import traceback
                await self._notification_helper.notify_alert(
                    error_type="StrategyError",
                    error_msg=str(e),
                    impact="主循环更新异常，可能影响挂单与止损维护",
                    traceback_text="".join(traceback.format_exc(limit=4)),
                )
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
        await self._notification_helper.send_shutdown_notification(reason=reason, gate_position=self._gate_position)
    
    def _build_klines_by_timeframe(self, primary_klines: list = None) -> dict:
        """
        构建多周期 K 线字典（用于支撑/阻力位计算）
        
        Args:
            primary_klines: 主周期 K 线（可选，如果不传则从缓存获取）
            
        Returns:
            {"4h": [...], "1d": [...]} 格式的字典
        """
        kline_config = self.config.kline_config
        primary_tf = kline_config.primary_timeframe
        
        # 主周期
        if primary_klines is None:
            primary_klines = self.kline_feed.get_cached_klines(primary_tf)
        
        klines_dict = {primary_tf.value: primary_klines}
        
        # 辅助周期（最多支持 2 个辅助周期，总共 3 个）
        for aux_tf in kline_config.auxiliary_timeframes[:2]:
            aux_klines = self.kline_feed.get_cached_klines(aux_tf)
            if aux_klines:
                klines_dict[aux_tf.value] = aux_klines
        
        return klines_dict
    
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
            if not self.position_manager.grid_config.restore_state_enabled:
                self.logger.info("🧹 已禁用持久化恢复，跳过恢复网格状态")
            else:
                current_price = klines[-1].close if klines else 0
                if current_price > 0:
                    restored = self.position_manager.restore_state(current_price)
                    if restored:
                        self.logger.info("已从持久化恢复网格状态")
                        self._grid_created = True  # 恢复成功，标记网格已创建
            self._restored_state = True
        
        # T004: 启动时同步交易所现有止损单（仅一次）
        if not self._sl_synced_from_exchange and self._executor:
            await self._sync_stop_loss_from_exchange()
            self._sl_synced_from_exchange = True
        
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
        await self._notification_helper.check_telegram_bot()
        
        # 首次创建网格 (需要价格数据和支撑/阻力位计算完成)
        if not self._grid_created and self._current_state:
            await self._create_initial_grid(klines)

        # 价格偏离 / 成交触发：自动重建网格（已废弃，保留接口但不触发）
        # if self._grid_created and self._current_state and self.position_manager.state:
        #     await self._maybe_rebuild_grid(klines)
        
        # Recon 对账 + Event 增量更新
        await self._run_recon_track()
        await self._run_event_track()

        # 检测持仓变化，更新止损单（保留全仓止损）
        await self._check_and_update_stop_loss_order()

        # T005: 检测止损单是否被触发
        await self._check_stop_loss_triggered()

        if self._notifier and self._current_state:
            uptime_hours = (time.time() - (self._strategy_start_time / 1000)) / 3600
            pos_value = float(self._gate_position.get("notional", 0) or 0)
            unrealized = float(self._gate_position.get("unrealized_pnl", 0) or 0)
            await self._notifier.notify_idle_heartbeat(
                symbol=self.config.symbol,
                current_price=float(self._current_state.close or 0),
                position_value=pos_value,
                unrealized_pnl=unrealized,
                uptime_hours=uptime_hours,
            )

    async def _maybe_rebuild_grid(self, klines: List[Kline]) -> None:
        """
        旧版自动重建网格逻辑（Spec2.0 已废弃，保留但不使用）。
        """
        return
    
    async def force_rebuild_grid(self) -> bool:
        """
        强制重置网格（TG 触发）。
        
        逻辑：
        - 先同步持仓/挂单
        - 撤销全部挂单
        - 重新计算支撑/阻力位并重建网格状态
        - 无持仓：按最新支撑位全量挂买单
        - 有持仓：计算 N，从 N+1 支撑位开始挂买单；卖单按 Recon 规则分配
        """
        import time
        start_ts = time.time()

        if not self._executor:
            self.logger.warning("无执行器，无法强制重置网格")
            return False

        # 补齐当前状态
        if not self._current_state:
            klines = self.kline_feed.get_cached_klines(
                self.config.kline_config.primary_timeframe
            )
            if len(klines) >= 50:
                self._current_state = self.indicator.calculate(klines)
            else:
                self.logger.warning("无当前状态数据，无法强制重置")
                return False

        current_price = float(self._current_state.close or 0)
        if current_price <= 0:
            self.logger.warning("当前价格无效，无法强制重置")
            return False

        self.logger.info(f"🔄 强制重置网格: current_price={current_price:.2f}")

        gate_symbol = self._convert_to_gate_symbol(self.config.symbol)

        try:
            # 1) 同步账户/挂单/持仓
            await self._update_account_balance()
            await self._update_gate_orders()
            await self._update_gate_position()
            await self._update_gate_trades()

            # 2) 撤掉该 symbol 下所有挂单
            if hasattr(self._executor, "cancel_all_plan_orders"):
                await self._executor.cancel_all_plan_orders(gate_symbol)
            if hasattr(self._executor, "cancel_all_orders"):
                await self._executor.cancel_all_orders(gate_symbol)

            # 2.1) 等待挂单完全撤销
            await asyncio.sleep(1)

            # 2.2) 重新设置保证金模式（在撤单后才能切换）
            try:
                margin_mode = self.config.margin_mode
                leverage = self.config.leverage
                self.logger.info(f"🔧 重新设置保证金模式: {margin_mode}, 杠杆: {leverage}x")
                await self._executor.set_margin_mode(gate_symbol, margin_mode)
                # 全仓/逐仓模式都使用配置的杠杆值
                await self._executor.set_leverage(gate_symbol, leverage)
                self.logger.info(f"✅ 保证金模式设置完成: {margin_mode}, {leverage}x")
            except Exception as e:
                self.logger.warning(f"⚠️ 设置保证金模式失败: {e}")

            # 3) 重新计算支撑/阻力位（多周期融合）
            klines = self.kline_feed.get_cached_klines(
                self.config.kline_config.primary_timeframe
            )
            if len(klines) < 50:
                self.logger.warning("K线数据不足，无法重置")
                return False

            klines_dict = self._build_klines_by_timeframe(klines)
            
            # 🆕 V3.0: 检查是否启用新版水位生成
            if self._is_v3_enabled():
                self.logger.info("🆕 [V3.0] 使用 LevelCalculator 生成水位")
                supports, resistances = self._calculate_levels_v3(klines_dict, current_price)
                if not supports:
                    self.logger.warning("[V3.0] 未生成有效支撑位，回退到 V2.0")
                    supports, resistances = None, None
            else:
                supports, resistances = None, None
            
            # V2.0 回退
            if supports is None:
                resistance_calc = self.position_manager.resistance_calc
                resistances = resistance_calc.calculate_resistance_levels(
                    current_price, klines, "long", klines_by_timeframe=klines_dict
                )
                supports = resistance_calc.calculate_support_levels(
                    current_price, klines, klines_by_timeframe=klines_dict
                )

            if not supports:
                self.logger.warning("未找到有效支撑位，放弃重置")
                return False

            # 4) 保存旧锚点用于通知
            old_anchor = 0
            if self.position_manager.state:
                old_anchor = getattr(self.position_manager.state, "anchor_price", 0) or 0

            # 5) 重建网格状态
            new_grid = self.position_manager.create_grid(
                current_price=current_price,
                support_levels=supports,
                resistance_levels=resistances,
            )
            if not new_grid:
                self.logger.warning("网格重置失败")
                return False

            new_grid.anchor_price = current_price
            new_grid.anchor_ts = int(time.time())
            self.position_manager._save_state()

            # 6) 同步 Recon 执行冷却
            self._recon_last_run_at = time.time()

            # 7) 直接调用 build_recon_actions 确保与 Recon 逻辑完全一致
            exchange_min_qty = self._get_exchange_min_contracts()
            contract_size = float(getattr(self, "_contract_size", 0) or self.config.default_contract_size)
            exchange_min_qty_btc = exchange_min_qty * contract_size
            
            # 这里的 open_orders 传空，因为上面已经 cancel_all 了
            actions = self.position_manager.build_recon_actions(
                current_price=current_price,
                open_orders=[], 
                exchange_min_qty_btc=exchange_min_qty_btc,
            )

            await self._execute_recon_actions(actions)

            # 8) 重置止损状态，等待后续同步
            self._tp_orders_submitted = False
            self._stop_loss_order_id = None
            self._stop_loss_contracts = 0

            self._last_rebuild_at = time.time()
            self._need_rebuild_after_fill = False

            # 9) 通知
            buy_actions = [a for a in actions if a.get("side") == "buy"]
            sell_actions = [a for a in actions if a.get("side") == "sell"]
            
            await self._notification_helper.notify_grid_rebuild(
                reason="手动触发",
                old_anchor=old_anchor,
                new_anchor=current_price,
                new_orders=[
                    {"side": a.get("side"), "price": a.get("price"), "amount": 0}
                    for a in buy_actions
                ],
            )
            if self._notifier:
                await self._notifier.notify_system_info(
                    event="网格坐标重构完成",
                    result=f"更新 {len(buy_actions)} 个支撑位，{len(sell_actions)} 个阻力位",
                    duration_sec=time.time() - start_ts,
                )

            self.logger.info(
                f"✅ 网格强制重置完成: 新锚点={current_price:.2f}, "
                f"买单={len(buy_actions)}档, 卖单={len(sell_actions)}档"
            )
            return True

        except Exception as e:
            self.logger.error(f"强制重置网格失败: {e}", exc_info=True)
            await self._notification_helper.notify_error("RebuildError", str(e), "强制重置网格")
            return False
    
    async def _update_account_balance(self) -> None:
        """从交易所更新账户余额 - 委托给 ExchangeSyncManager"""
        await self._exchange_sync.update_account_balance()
        # 同步数据到策略实例变量（向后兼容）
        self._account_balance = self._exchange_sync.account_balance
        self._balance_updated_at = self._exchange_sync.balance_updated_at

    async def _update_gate_orders(self) -> None:
        """从 Gate 交易所同步当前挂单 - 委托给 ExchangeSyncManager"""
        await self._exchange_sync.update_open_orders()
        # 同步数据到策略实例变量（向后兼容）
        self._gate_open_orders = self._exchange_sync.open_orders
        self._orders_updated_at = self._exchange_sync.orders_updated_at
        self._contract_size = self._exchange_sync.contract_size
    
    async def _update_gate_position(self) -> None:
        """从 Gate 交易所同步当前持仓 - 委托给 ExchangeSyncManager"""
        # 设置当前市场状态供持仓变动通知使用
        self._exchange_sync.set_current_state(self._current_state)
        await self._exchange_sync.update_position()
        # 同步数据到策略实例变量（向后兼容）
        self._gate_position = self._exchange_sync.position
        self._position_updated_at = self._exchange_sync.position_updated_at
        self._contract_size = self._exchange_sync.contract_size
        self._last_position_btc = self._exchange_sync._last_position_btc
        self._last_position_avg_price = self._exchange_sync._last_position_avg_price
        self._last_position_unrealized_pnl = self._exchange_sync._last_position_unrealized_pnl
        # 首次同步时对齐基准
        if self._last_position_contracts is None and self._gate_position:
            self._last_position_contracts = int(self._gate_position.get("raw_contracts", 0) or 0)
            self._last_position_usdt = float(self._gate_position.get("notional", 0) or 0)
    
    async def _update_gate_trades(self) -> None:
        """从 Gate 交易所获取成交记录 - 委托给 ExchangeSyncManager"""
        await self._exchange_sync.update_trades()
        # 同步数据到策略实例变量（向后兼容）
        self._gate_trades = self._exchange_sync.trades
        self._trades_updated_at = self._exchange_sync.trades_updated_at

    def _get_exchange_min_contracts(self) -> float:
        """获取交易所最小下单张数 - 委托给 ExchangeSyncManager"""
        try:
            return self._exchange_sync.get_exchange_min_contracts()
        except Exception:
            return 1.0

    async def _on_order_filled_callback(
        self, side: str, fill_price: float, fill_amount: float,
        grid_index: int = 0, realized_pnl: float = 0
    ) -> None:
        """订单成交回调 - 供 ReconEventManager 使用"""
        await self._notification_helper.notify_order_filled(
            side=side,
            fill_price=fill_price,
            fill_amount=fill_amount,
            grid_index=grid_index,
            realized_pnl=realized_pnl,
            gate_position=self._gate_position,
        )

    async def _run_recon_track(self) -> None:
        """运行 Recon 轨道 - 委托给 ReconEventManager"""
        await self._recon_manager.run_recon_track(
            current_state=self._current_state,
            gate_position=self._gate_position,
            gate_open_orders=self._gate_open_orders,
            gate_trades=self._gate_trades,
            contract_size=self._contract_size,
            grid_created=self._grid_created,
        )
        # 同步状态
        self._recon_last_run_at = self._recon_manager.recon_last_run_at
        self._grid_lock_until = self._recon_manager._grid_lock_until

    async def _run_event_track(self) -> None:
        """运行 Event 轨道 - 委托给 ReconEventManager"""
        await self._recon_manager.run_event_track(
            current_state=self._current_state,
            gate_trades=self._gate_trades,
            contract_size=self._contract_size,
        )
        # 同步状态
        self._last_trade_ids = self._recon_manager._last_trade_ids

    async def reset_fill_counters(self, reason: str = "manual") -> bool:
        """重置持仓计数器 - 委托给 ReconEventManager"""
        return await self._recon_manager.reset_fill_counters(reason=reason)

    async def _execute_recon_actions(self, actions: List[Dict[str, Any]]) -> None:
        """执行订单动作 - 委托给 ReconEventManager"""
        await self._recon_manager._execute_actions(actions)

    def _find_level_state(self, side: str, price: float):
        if not self.position_manager.state:
            return None
        price = float(price or 0)
        levels = (
            self.position_manager.state.support_levels_state +
            self.position_manager.state.resistance_levels_state
        )
        for lvl in levels:
            if abs(lvl.price - price) <= lvl.price * 0.001:
                return lvl
        return None

    def _mark_level_filled(self, side: str, price: float) -> None:
        from key_level_grid.position import LevelStatus
        lvl = self._find_level_state(side, price)
        if lvl:
            lvl.status = LevelStatus.FILLED
            lvl.last_action_ts = int(time.time())

    def _mark_level_idle(self, side: str, price: float) -> None:
        from key_level_grid.position import LevelStatus
        lvl = self._find_level_state(side, price)
        if lvl:
            lvl.status = LevelStatus.IDLE
            lvl.last_action_ts = int(time.time())
    
    async def _check_and_submit_take_profit_orders(self) -> None:
        """
        旧版止盈挂单逻辑（Spec2.0 已废弃，保留但不使用）。
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
        if last_contracts is None:
            # 首次初始化基准，不发送通知
            self._last_position_contracts = current_contracts
            self._last_position_usdt = current_position_usdt
            return

        if current_contracts > last_contracts:
            added_contracts = current_contracts - last_contracts
            self.logger.info(
                f"🎯 持仓增加: +{added_contracts}张, "
                f"当前持仓: {current_contracts}张 (≈{current_position_usdt:.0f} USDT)"
            )
            # 标记需要重建（成交驱动），记录成交时间
            self._need_rebuild_after_fill = True
            self._last_fill_at = time.time()
            
            # 发送买入成交通知（使用真实 contract_size）
            fill_price = float(self._gate_position.get("entry_price", 0) or 0)
            contract_size = float(self._gate_position.get("contract_size", getattr(self, "_contract_size", 0.0001)) or 0.0001)
            fill_amount = added_contracts * contract_size * fill_price  # USDT
            # 避免 contract_size 异常导致巨额金额
            if fill_amount > 0:
                await self._notification_helper.notify_order_filled(
                    side="buy",
                    fill_price=fill_price,
                    fill_amount=fill_amount,
                    grid_index=0,
                    realized_pnl=0,
                    gate_position=self._gate_position,
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
            # 标记需要重建（成交驱动），记录成交时间
            self._need_rebuild_after_fill = True
            self._last_fill_at = time.time()
            
            # 发送卖出成交通知（使用真实 contract_size）
            fill_price = float(self._gate_position.get("mark_price", 0) or 0)
            contract_size = float(self._gate_position.get("contract_size", getattr(self, "_contract_size", 0.0001)) or 0.0001)
            fill_amount = reduced_contracts * contract_size * fill_price  # USDT
            # 计算实现盈亏（简化估算）
            entry_price = float(self._gate_position.get("entry_price", 0) or 0)
            realized_pnl = (fill_price - entry_price) * reduced_contracts * contract_size if entry_price > 0 else 0
            if fill_amount > 0:
                await self._notification_helper.notify_order_filled(
                    side="sell",
                    fill_price=fill_price,
                    fill_amount=fill_amount,
                    grid_index=0,
                    realized_pnl=realized_pnl,
                    gate_position=self._gate_position,
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
        """检查并更新止损单 - 委托给 RiskManager"""
        await self._risk_manager.check_and_update_stop_loss(
            gate_position=self._gate_position,
            contract_size=self._contract_size,
        )
        # 同步状态到策略实例变量（向后兼容）
        self._stop_loss_order_id = self._risk_manager.stop_loss_order_id
        self._stop_loss_contracts = self._risk_manager.stop_loss_contracts
        self._stop_loss_trigger_price = self._risk_manager.stop_loss_trigger_price
        self._sl_order_updated_at = self._risk_manager.sl_order_updated_at
        self._sl_last_entry_price = self._risk_manager.sl_last_entry_price
    
    async def _submit_stop_loss_order(self, contracts: int, trigger_price: float) -> bool:
        """提交止损单 - 委托给 RiskManager"""
        success = await self._risk_manager._submit_stop_loss_order(
            contracts=contracts,
            trigger_price=trigger_price,
            gate_position=self._gate_position,
            contract_size=self._contract_size,
        )
        # 同步状态
        self._stop_loss_order_id = self._risk_manager.stop_loss_order_id
        self._stop_loss_contracts = self._risk_manager.stop_loss_contracts
        self._stop_loss_trigger_price = self._risk_manager.stop_loss_trigger_price
        self._sl_order_updated_at = self._risk_manager.sl_order_updated_at
        self._sl_last_entry_price = self._risk_manager.sl_last_entry_price
        return success
    
    async def _cancel_stop_loss_order_on_exchange(self, order_id: str) -> bool:
        """取消交易所止损单 - 委托给 RiskManager"""
        return await self._risk_manager._cancel_stop_loss_order_on_exchange(order_id)
    
    async def _cancel_stop_loss_order(self) -> bool:
        """取消止损单 - 委托给 RiskManager"""
        success = await self._risk_manager._cancel_stop_loss_order()
        # 同步状态
        self._stop_loss_order_id = self._risk_manager.stop_loss_order_id
        self._stop_loss_contracts = self._risk_manager.stop_loss_contracts
        return success
    
    async def _sync_stop_loss_from_exchange(self) -> None:
        """同步止损单 - 委托给 RiskManager"""
        await self._risk_manager._sync_stop_loss_from_exchange()
        # 同步状态
        self._stop_loss_order_id = self._risk_manager.stop_loss_order_id
        self._stop_loss_contracts = self._risk_manager.stop_loss_contracts
        self._stop_loss_trigger_price = self._risk_manager.stop_loss_trigger_price
    
    async def _check_stop_loss_triggered(self) -> None:
        """检测止损触发 - 委托给 RiskManager"""
        triggered_info = await self._risk_manager.check_stop_loss_triggered(
            gate_position=self._gate_position,
        )
        # 同步状态
        self._stop_loss_order_id = self._risk_manager.stop_loss_order_id
        self._stop_loss_contracts = self._risk_manager.stop_loss_contracts
        self._sl_last_entry_price = self._risk_manager.sl_last_entry_price
        
        # 如果触发了止损，发送通知
        if triggered_info:
            await self._notification_helper.notify_stop_loss_triggered(
                trigger_price=triggered_info.get("trigger_price", 0),
                loss_usdt=triggered_info.get("loss_usdt", 0),
                loss_pct=triggered_info.get("loss_pct", 0),
                fill_contracts=triggered_info.get("fill_contracts", 0),
                entry_price=triggered_info.get("entry_price", 0),
                gate_position=self._gate_position,
            )
    
    async def _submit_take_profit_orders(self, position_usdt: float) -> None:
        """
        旧版止盈卖单逻辑（Spec2.0 已废弃，保留但不使用）。
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
        
        # 计算支撑位和阻力位（使用多周期融合）
        klines_dict = self._build_klines_by_timeframe(klines)
        
        # 🆕 V3.0: 检查是否启用新版水位生成
        if self._is_v3_enabled():
            self.logger.info("🆕 [V3.0] 使用 LevelCalculator 生成水位")
            supports, resistances = self._calculate_levels_v3(klines_dict, current_price)
            if not supports:
                self.logger.warning("[V3.0] 未生成有效支撑位，回退到 V2.0")
                supports, resistances = None, None
        else:
            supports, resistances = None, None
        
        # V2.0 回退
        if supports is None:
            resistance_calc = self.position_manager.resistance_calc
            resistances = resistance_calc.calculate_resistance_levels(
                current_price, klines, "long", klines_by_timeframe=klines_dict
            )
            supports = resistance_calc.calculate_support_levels(
                current_price, klines, klines_by_timeframe=klines_dict
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
        else:
            self.logger.warning("网格创建失败，将在下一周期重试")
    
    async def _submit_grid_orders(self, grid_state, rebuild_mode: bool = False) -> None:
        """
        旧版网格挂单逻辑（Spec2.0 已废弃，保留但不使用）。
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
            
            self.logger.info(f"🔧 配置保证金模式: {margin_mode}, 杠杆: {leverage}x")

            # 先设置保证金模式，再设置杠杆
            await self._executor.set_margin_mode(gate_symbol, margin_mode)
            
            # 全仓/逐仓模式都使用配置的杠杆值
            await self._executor.set_leverage(gate_symbol, leverage)
            self.logger.info(f"✅ 保证金模式设置完成: {margin_mode}, {leverage}x")
            
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
        
        # 记录合同规模用于后续转换
        grid_state.contract_size = contract_size
        grid_state.num_grids = num_grids
        self.position_manager._save_state()
        
        # ============================================
        # 4. 三层过滤：计算已成交网格数 + 均价保护
        position_contracts = int(float(self._gate_position.get("raw_contracts", 0) or 0))
        avg_entry_price = float(self._gate_position.get("entry_price", 0) or 0)
        price_threshold = avg_entry_price * 0.995 if (avg_entry_price > 0 and not rebuild_mode) else 0

        # 5. 买单排序（按价格从高到低）
        leverage = self.config.leverage or 20
        sorted_orders = sorted(grid_state.buy_orders, key=lambda x: x.price, reverse=True)

        # 粗略估计每格张数（用于日志）：取首档金额
        ref_contracts_per_grid = 0
        if sorted_orders:
            ref_contracts_per_grid = int(sorted_orders[0].amount_usdt / (sorted_orders[0].price * contract_size)) or 1

        filled_grids = 0
        if position_contracts > 0 and ref_contracts_per_grid > 0:
            filled_grids = math.ceil(position_contracts / ref_contracts_per_grid)

        self.logger.info(
            f"📊 过滤参数: 持仓={position_contracts}张, 已成交网格≈{filled_grids}, "
            f"均价={avg_entry_price:.2f}, 均价保护阈值={price_threshold:.2f}"
        )

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
            if price_threshold > 0 and order.price >= price_threshold:
                skipped_threshold += 1
                self.logger.debug(f"⏭️ 跳过均价保护: @ {order.price:.2f} >= {price_threshold:.2f}")
                continue

            # 计算张数与保证金
            qty = max(1, int(order.amount_usdt / (order.price * contract_size)))
            required_margin = order.amount_usdt / leverage

            if available_balance < required_margin:
                self.logger.warning(
                    f"⚠️ 余额不足，跳过买单: 价格={order.price:.2f}, 金额={order.amount_usdt:.2f}U, "
                    f"需保证金≈{required_margin:.2f}U, 可用={available_balance:.2f}U"
                )
                continue

            # 提交订单
            try:
                gate_order = Order.create(
                    symbol=gate_symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    price=order.price,
                    quantity=qty,
                    pricing_mode="usdt",
                    target_value_usd=order.amount_usdt,
                )
                gate_order.metadata['order_mode'] = 'limit'
                gate_order.metadata['grid_id'] = order.grid_id
                gate_order.metadata['source'] = order.source
                gate_order.metadata['target_contracts'] = qty
                gate_order.metadata['contract_size'] = contract_size

                success = await self._executor.submit_order(gate_order)

                if success:
                    submitted_count += 1
                    available_balance -= required_margin
                    self.logger.info(
                        f"✅ 网格买单 #{order.grid_id}: {qty}张 @ {order.price:.2f} (≈{order.amount_usdt:.0f}U)"
                    )
                else:
                    failed_count += 1
                    self.logger.error(
                        f"❌ 网格买单 #{order.grid_id} 失败: {gate_order.reject_reason}"
                    )
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
        try:
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
            elif self._tg_bot:
                await self._send_signal_for_confirmation(signal)
            else:
                self._pending_signal = signal
        except Exception as e:
            self.logger.error(f"K线回调异常: {e}", exc_info=True)
            import traceback
            await self._notification_helper.notify_alert(
                error_type="WebSocketError",
                error_msg=str(e),
                impact="K线回调异常，信号生成可能延迟",
                traceback_text="".join(traceback.format_exc(limit=4)),
            )
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
        # 委托给 DisplayDataGenerator
        return self._display_generator.get_status(
            current_state=self._current_state,
            running=self._running,
            pending_signal=self._pending_signal,
            kline_feed=self.kline_feed,
        )
    
    def get_display_data(self) -> Dict[str, Any]:
        """获取显示面板数据 - 委托给 DisplayDataGenerator"""
        # 更新展示数据生成器的上下文
        self._display_generator.update_context(
            account_balance=self._account_balance,
            gate_position=self._gate_position,
            gate_open_orders=self._gate_open_orders,
            contract_size=self._contract_size,
        )
        
        # 委托给 DisplayDataGenerator
        return self._display_generator.get_display_data(
            current_state=self._current_state,
            kline_feed=self.kline_feed,
            build_klines_by_timeframe_func=self._build_klines_by_timeframe,
            dry_run=self.config.dry_run,
        )
    
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
    
    async def tg_update_grid_range(self, lower: float, upper: float) -> bool:
        if not self.position_manager or lower <= 0 or upper <= 0 or upper <= lower:
            return False
        async with self._grid_lock:
            grid_cfg = self.position_manager.grid_config
            grid_cfg.range_mode = "manual"
            grid_cfg.manual_lower = float(lower)
            grid_cfg.manual_upper = float(upper)
            if self.config.grid_config:
                self.config.grid_config.range_mode = "manual"
                self.config.grid_config.manual_lower = float(lower)
                self.config.grid_config.manual_upper = float(upper)
            if self.position_manager.state:
                self.position_manager.state.grid_floor = lower * (1 - grid_cfg.floor_buffer)
                self.position_manager._save_state()
        return True

    async def tg_update_base_position_locked(self, locked_btc: float) -> bool:
        async with self._grid_lock:
            grid_cfg = self.position_manager.grid_config
            grid_cfg.base_position_locked = max(float(locked_btc or 0), 0.0)
            if self.config.grid_config:
                self.config.grid_config.base_position_locked = grid_cfg.base_position_locked
            if self.position_manager.state:
                self.position_manager.state.base_position_locked = grid_cfg.base_position_locked
                self.position_manager._save_state()
        return True

    async def tg_update_stop_loss_pct(self, pct: float) -> bool:
        if pct <= 0 or pct >= 1:
            return False
        async with self._grid_lock:
            sl_cfg = getattr(self.position_manager, "stop_loss_config", None)
            if sl_cfg:
                sl_cfg.trigger = "fixed_pct"
                sl_cfg.fixed_pct = float(pct)
            self._stop_loss_order_id = None
            self._stop_loss_contracts = 0
            await self._check_and_update_stop_loss_order()
        return True

    async def tg_update_margin_leverage(self, margin_mode: str, leverage: int) -> bool:
        async with self._grid_lock:
            if float(self._gate_position.get("contracts", 0) or 0) > 0:
                return False
            self.config.margin_mode = margin_mode
            self.config.leverage = int(leverage)
            if self._executor:
                gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
                # 先保证金模式，再杠杆
                await self._executor.set_margin_mode(gate_symbol, margin_mode)
                # 全仓/逐仓模式都使用配置的杠杆值
                await self._executor.set_leverage(gate_symbol, int(leverage))
        return True

    async def tg_deep_recon(self) -> bool:
        async with self._grid_lock:
            self._recon_last_run_at = 0
            await self._run_recon_track()
        return True

    async def tg_force_rebuild(self) -> bool:
        async with self._grid_lock:
            return await self.force_rebuild_grid()

    async def tg_emergency_close(self) -> bool:
        if not self._executor:
            return False
        async with self._grid_lock:
            gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
            try:
                await self._executor.cancel_all_orders(gate_symbol)
                plan_orders = await self._executor.get_plan_orders(gate_symbol, status="open")
                for order in plan_orders:
                    order_id = str(order.get("id", ""))
                    if order_id:
                        await self._executor.cancel_plan_order(gate_symbol, order_id)
            except Exception as e:
                self.logger.error(f"紧急全平撤单失败: {e}")
            raw_contracts = float(self._gate_position.get("raw_contracts", 0) or 0)
            if raw_contracts > 0:
                from key_level_grid.executor.base import Order, OrderSide, OrderType
                order = Order.create(
                    symbol=gate_symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=raw_contracts,
                    price=0,
                )
                order.reduce_only = True
                order.metadata["reason"] = "emergency_close"
                order.metadata["order_type"] = "紧急全平"
                await self._executor.submit_order(order)
            await self.stop(reason="tg_emergency_close")
        return True

