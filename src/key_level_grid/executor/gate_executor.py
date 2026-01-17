"""
Gate.io 订单执行器

处理 Gate.io 交易所的订单提交和管理（包含模拟模式）。
"""

import asyncio
import time
from typing import Dict, Optional

from key_level_grid.executor.base import ExecutorBase, Order, OrderStatus, OrderType
from key_level_grid.executor.exchange_executor import ExchangeExecutor
from key_level_grid.utils.config import SafetyConfig
from key_level_grid.executor.usdt_pricing import compute_usdt_quantity


class GateExecutor(ExchangeExecutor):
    """
    Gate.io 执行器
    
    支持真实交易和纸交易模式。
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        paper_trading: bool = True,  # 默认纸交易
        safety_config: Optional[SafetyConfig] = None,
        max_retries: int = 3,
        retry_delay_ms: int = 100,
        ioc_timeout_sec: float = 2.0,
    ):
        """
        初始化 Gate 执行器
        
        Args:
            api_key: API 密钥
            api_secret: API 密钥
            paper_trading: 是否为纸交易模式
            safety_config: 安全配置（实盘交易保护）
            max_retries: 最大重试次数
            retry_delay_ms: 重试延迟（毫秒）
            ioc_timeout_sec: IOC 订单超时时间（秒）
        """
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            paper_trading=paper_trading,
            safety_config=safety_config,
            max_retries=max_retries,
            retry_delay_ms=retry_delay_ms,
            ioc_timeout_sec=ioc_timeout_sec,
            default_paper_balances={"USDT": 10000.0},
            logger_name=__name__,
        )

        # === Phase 5.1: 真实交易所连接（T072）===
        self._exchange = None           # ccxt 交易所实例
        
        if not paper_trading:
            self._init_live_exchange()
    
    async def _pre_trade_safety_check(self, order: Order) -> tuple[bool, str]:
        """
        Gate 合约的安全检查（重写基类逻辑）
        
        关键修正：
        - Gate 永续合约下单 quantity 是“张数”，真实名义价值应为:
          notional_usdt = contracts * contractSize * price
        - 若使用 USDT 计价（pricing_mode='usdt'），优先使用 target_value_usd 作为订单金额
        
        备注：原基类使用 quantity * price，会把“张数”当成“币数量”，导致金额被放大 10^3~10^5。
        """
        # 复用基类的日切逻辑与交易次数限制
        self._reset_daily_stats_if_needed()

        if self.daily_trades >= self.safety.max_daily_trades:
            reason = f"每日交易次数上限 {self.daily_trades}/{self.safety.max_daily_trades}"
            self.logger.warning(reason)
            return False, reason

        # 订单金额估算（USD/USDT）
        order_value = 0.0

        # ✅ USDT 计价：直接用目标金额（更可靠）
        if getattr(order, "pricing_mode", None) == "usdt" and getattr(order, "target_value_usd", None):
            try:
                order_value = float(order.target_value_usd or 0)
            except Exception:
                order_value = 0.0
        else:
            price = float(order.price or 0)
            qty = float(order.quantity or 0)

            # 合约：contracts * contractSize * price
            contract_size = None
            try:
                markets = self._exchange.markets or self._exchange.load_markets()
                market = markets.get(order.symbol) if markets else None
                if market and (market.get("swap") or market.get("future") or market.get("contract")):
                    contract_size = market.get("contractSize", 1.0)
            except Exception:
                contract_size = None

            if contract_size is not None and contract_size > 0:
                order_value = qty * float(contract_size) * price
            else:
                # 回退：按现货逻辑估算
                order_value = qty * price

        if order_value > self.safety.max_position_value:
            reason = f"订单金额超限 ${order_value:.2f} > ${self.safety.max_position_value:.2f}"
            self.logger.warning(reason)
            return False, reason

        if self.daily_pnl < -abs(self.safety.emergency_stop_loss):
            reason = f"触发紧急止损 (PnL={self.daily_pnl:.2f})"
            self.logger.error(reason)
            return False, reason

        return True, ""
    
    def _init_live_exchange(self) -> None:
        """
        初始化真实交易所连接（T072）
        
        使用 ccxt 库连接到 Gate.io 交易所。
        """
        try:
            import ccxt
            
            self._exchange = ccxt.gate({
                'apiKey': self.api_key,
                'secret': self.api_secret,
                'enableRateLimit': True,
                'timeout': 30000,  # 30秒超时（默认10秒太短）
                'rateLimit': 100,  # 请求间隔100ms
                'options': {
                    'defaultType': 'swap',  # USDT本位永续合约
                    'adjustForTimeDifference': True,  # 自动调整时间差
                    'recvWindow': 60000,  # API接收窗口60秒
                }
            })
            
            # 加载市场信息（必须！否则 markets 为 None）
            self._exchange.load_markets()
            
            self.logger.info(
                "✅ Gate.io 交易所连接已初始化 (USDT永续合约)",
                extra={
                    'exchange': 'gate',
                    'type': 'swap',
                    'rate_limit': True,
                    'markets_loaded': len(self._exchange.markets) if self._exchange.markets else 0
                }
            )
            
        except ImportError:
            self.logger.error("❌ 无法导入 ccxt 库，请安装：pip install ccxt")
            raise
        except Exception as e:
            self.logger.error(f"❌ 初始化 Gate.io 连接失败: {e}", exc_info=True)
            raise
    
    async def _submit_order_with_reduce_only_fallback(
        self,
        loop,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float],
        params: dict
    ) -> dict:
        """
        提交订单，支持reduceOnly参数的多层fallback机制
        
        学习自gate-version项目的最佳实践：
        1. 尝试标准参数 reduceOnly: True
        2. 如果失败，尝试 Gate.io 格式 reduce_only: True
        3. 如果仍然失败，使用无参数模式（并记录警告）
        
        Args:
            loop: asyncio事件循环
            symbol: 交易对
            order_type: 订单类型
            side: 买卖方向
            amount: 数量
            price: 价格（可选）
            params: 额外参数
            
        Returns:
            交易所响应
        """
        has_reduce_only = params.get('reduceOnly', False)
        
        if not has_reduce_only:
            # 没有设置reduceOnly，直接提交
            return await loop.run_in_executor(
                None,
                lambda: self._exchange.create_order(
                    symbol=symbol,
                    type=order_type,
                    side=side,
                    amount=amount,
                    price=price,
                    params=params
                )
            )
        
        # 尝试1：标准格式 reduceOnly
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self._exchange.create_order(
                    symbol=symbol,
                    type=order_type,
                    side=side,
                    amount=amount,
                    price=price,
                    params=params
                )
            )
            
            # ✅ 验证reduceOnly是否生效
            reduce_only_effective = self._verify_reduce_only(response)
            
            if reduce_only_effective:
                self.logger.info(
                    f"✅ 订单已创建（仅减仓）: {response.get('id')}",
                    extra={
                        'order_id': response.get('id'),
                        'reduce_only': True,
                        'method': 'reduceOnly'
                    }
                )
            else:
                self.logger.warning(
                    f"⚠️ reduceOnly参数未生效，订单ID: {response.get('id')}",
                    extra={'order_id': response.get('id'), 'response': response}
                )
            
            return response
            
        except Exception as e1:
            error_msg = str(e1).lower()
            
            # 如果不是参数错误，直接抛出
            if 'invalid' not in error_msg and 'parameter' not in error_msg:
                raise
            
            self.logger.warning(
                f"⚠️ reduceOnly参数格式错误，尝试Gate.io格式: {e1}",
                extra={'error': str(e1)[:200]}
            )
        
        # 尝试2：Gate.io格式 reduce_only
        try:
            gate_params = params.copy()
            gate_params.pop('reduceOnly', None)
            gate_params['reduce_only'] = True
            
            response = await loop.run_in_executor(
                None,
                lambda: self._exchange.create_order(
                    symbol=symbol,
                    type=order_type,
                    side=side,
                    amount=amount,
                    price=price,
                    params=gate_params
                )
            )
            
            reduce_only_effective = self._verify_reduce_only(response)
            
            if reduce_only_effective:
                self.logger.info(
                    f"✅ 订单已创建（仅减仓）: {response.get('id')}",
                    extra={
                        'order_id': response.get('id'),
                        'reduce_only': True,
                        'method': 'reduce_only'
                    }
                )
            
            return response
            
        except Exception as e2:
            self.logger.error(
                f"❌ reduce_only参数也失败，使用普通市价单: {e2}",
                extra={'error': str(e2)[:200]}
            )
        
        # 尝试3：无reduceOnly参数（最后fallback）
        fallback_params = params.copy()
        fallback_params.pop('reduceOnly', None)
        fallback_params.pop('reduce_only', None)
        
        self.logger.warning(
            f"⚠️ 无法使用reduceOnly参数，使用普通订单（风险：可能意外开仓）",
            extra={'symbol': symbol, 'side': side, 'amount': amount}
        )
        
        response = await loop.run_in_executor(
            None,
            lambda: self._exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=fallback_params
            )
        )
        
        return response
    
    def _verify_reduce_only(self, response: dict) -> bool:
        """
        验证响应中reduceOnly是否生效
        
        Args:
            response: 交易所响应
            
        Returns:
            True如果reduceOnly生效
        """
        # 检查顶层字段
        if response.get('reduceOnly'):
            return True
        
        # 检查info字段（Gate.io原始响应）
        if 'info' in response:
            info = response.get('info', {})
            
            # 检查多个可能的字段名
            if info.get('reduce_only'):
                return True
            if info.get('is_reduce_only'):
                return True
            
            # 检查initial字段（触发订单）
            initial = info.get('initial', {})
            if initial.get('is_reduce_only'):
                return True
            if initial.get('reduce_only'):
                return True
        
        return False
    
    async def get_ticker(self, symbol: str) -> dict:
        """
        获取最新行情
        
        Args:
            symbol: 交易对
            
        Returns:
            {'bid': float, 'ask': float, 'last': float}
        """
        return await self._fetch_ticker_with_retry(symbol)

    async def get_candles(self, symbol: str, timeframe: str = '1h', limit: int = 24) -> list:
        """
        获取 K 线数据用于计算指标
        
        Args:
            symbol: 交易对
            timeframe: K线周期 (默认 '1h')
            limit: 获取数量 (默认 24)
            
        Returns:
            K 线列表 [[timestamp, open, high, low, close, volume], ...]
        """
        if self.paper_trading:
             # 模拟返回空，或者可以考虑生成一些模拟数据
             return []
        
        try:
            import ccxt
            loop = asyncio.get_event_loop()
            
            # 调用 ccxt 的 fetch_ohlcv
            # 注意: Gate.io 的 timeframe 格式通常是标准的 (1m, 1h, 1d)
            candles = await loop.run_in_executor(
                None,
                lambda: self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            )
            return candles
            
        except Exception as e:
            self.logger.error(f"获取K线失败: {e}", exc_info=True)
            return []

    async def submit_order(self, order: Order) -> bool:
        """
        提交订单
        
        Args:
            order: 订单对象
            
        Returns:
            True 如果提交成功
        """
        # 标记订单类型
        order.is_paper_trade = self.paper_trading
        
        # === Phase 5.1: 真实交易安全检查（T073）===
        if not self.paper_trading:
            passed, reason = await self._pre_trade_safety_check(order)
            if not passed:
                self.logger.error(f"❌ 订单未通过安全检查，已拒绝: {reason}")
                order.status = OrderStatus.REJECTED
                order.reject_reason = f"安全检查失败: {reason}"
                return False
        
        contract_size = None
        try:
            market = self._exchange.markets.get(order.symbol, {}) if self._exchange else {}
            contract_size = market.get("contractSize")
        except Exception:
            contract_size = None
        qty_btc = None
        if contract_size:
            try:
                qty_btc = float(order.quantity or 0) * float(contract_size)
            except Exception:
                qty_btc = None
        price_display = order.price if order.price is not None else 0
        qty_display = f"{order.quantity}"
        if qty_btc is not None:
            qty_display = f"{order.quantity}张 ({qty_btc:.6f} BTC)"

        self.logger.info(
            f"提交订单: {order.symbol} {order.side.value.upper()} "
            f"{qty_display} @ ${price_display}",
            extra={
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side.value,
                "type": order.order_type.value,
                "quantity": order.quantity,
                "price": order.price,
                "is_paper_trade": order.is_paper_trade,
            }
        )
        
        # 执行提交（带重试）
        for attempt in range(self.max_retries):
            try:
                if self.paper_trading:
                    success = await self._submit_paper_order(order)
                else:
                    success = await self._submit_real_order(order)
                
                if success:
                    order.status = OrderStatus.SUBMITTED
                    order.submitted_at = int(time.time() * 1000)
                    self._stats["orders_submitted"] += 1
                    
                    # 对于 IOC 订单，立即检查执行
                    if order.order_type == OrderType.IOC:
                        await self._handle_ioc_order(order)
                    
                    await self._notify_order_sync(order, "新增")
                    
                    return True
                else:
                    # ✅ 检查是否为不可重试错误（余额不足、参数错误等）
                    reject_reason = getattr(order, 'reject_reason', '') or ''
                    is_non_retryable = any(keyword in reject_reason.lower() for keyword in [
                        'insufficient', 'balance', 'margin', 'invalid', 'permission', 'whitelist'
                    ])
                    
                    if is_non_retryable:
                        # 不可重试错误，直接返回
                        order.status = OrderStatus.FAILED
                        self._stats["orders_failed"] += 1
                        self.logger.warning(
                            f"⚠️ 订单因不可重试错误失败: {order.reject_reason}"
                        )
                        return False
                    
                    if attempt < self.max_retries - 1:
                        self._stats["retries"] += 1
                        delay = self.retry_delay_ms * (2 ** attempt) / 1000  # 指数退避
                        self.logger.warning(
                            f"订单提交失败，{delay:.2f}秒后重试 "
                            f"({attempt + 1}/{self.max_retries}) - 原因: {order.reject_reason or '未知'}"
                        )
                        await asyncio.sleep(delay)
                    else:
                        order.status = OrderStatus.FAILED
                        # 保留具体的失败原因，如果没有则使用通用消息
                        if not order.reject_reason:
                            order.reject_reason = "Max retries exceeded - 未知错误"
                        else:
                            order.reject_reason = f"Max retries exceeded - {order.reject_reason}"
                        self._stats["orders_failed"] += 1
                        self.logger.error(f"❌ 订单最终提交失败: {order.reject_reason}")
                        return False
            
            except Exception as e:
                self.logger.error(f"订单提交异常: {e}", exc_info=True)
                if attempt < self.max_retries - 1:
                    self._stats["retries"] += 1
                    await asyncio.sleep(self.retry_delay_ms / 1000)
                else:
                    order.status = OrderStatus.FAILED
                    order.reject_reason = str(e)
                    self._stats["orders_failed"] += 1
                    return False
        
        return False
    
    async def _submit_paper_order(self, order: Order) -> bool:
        """提交纸交易订单（模拟）"""
        # 模拟网络延迟
        await asyncio.sleep(0.05)
        
        # 检查余额
        if order.side.value == "buy":
            required = order.quantity * (order.price or 0)
            if self._paper_balances.get("USDT", 0) < required:
                order.status = OrderStatus.REJECTED
                order.reject_reason = "Insufficient balance"
                self._stats["orders_rejected"] += 1
                return False
        
        # 模拟订单ID
        order.exchange_order_id = f"paper_{int(time.time() * 1000)}"
        
        # 限价单提交成功
        if order.order_type == OrderType.LIMIT:
            # 模拟部分成交（90%概率立即成交）
            import random
            if random.random() < 0.9:
                await asyncio.sleep(0.1)
                order.filled_quantity = order.quantity
                order.avg_fill_price = order.price or 0
                order.fees = order.filled_quantity * order.avg_fill_price * 0.002
                order.status = OrderStatus.FILLED
                order.filled_at = int(time.time() * 1000)
                self._update_paper_balance(order)
                self._stats["orders_filled"] += 1
        
        return True

    async def _notify_order_sync(self, order: Order, status: str) -> None:
        notifier = getattr(self, "_notifier", None)
        if not notifier:
            return
        try:
            side = order.metadata.get("side") or order.side.value
            order_type = order.metadata.get("order_type")
            if not order_type:
                order_type = "支撑位买单" if side == "buy" else "阻力位卖单"
            reason = order.metadata.get("reason", "executor")
            price = float(order.metadata.get("price", 0) or (order.price or 0))
            qty_btc = float(order.metadata.get("qty_btc", 0) or 0)
            if qty_btc <= 0:
                contract_size = None
                try:
                    market = self._exchange.markets.get(order.symbol, {}) if self._exchange else {}
                    contract_size = market.get("contractSize")
                except Exception:
                    contract_size = None
                if contract_size:
                    qty_btc = float(order.quantity or 0) * float(contract_size)
            await notifier.notify_order_sync(
                symbol=order.symbol,
                order_type=order_type,
                status=status,
                price=price,
                new_qty=qty_btc,
                reason=reason,
            )
        except Exception as e:
            self.logger.error(f"发送挂单同步提醒失败: {e}")
    
    async def _prepare_order_params(
        self,
        order: Order,
        symbol: str,
        side: str,
        amount: float
    ) -> tuple[str, float, dict]:
        """
        准备订单参数（支持多种订单类型）
        
        支持的订单类型：
        - market: 市价单（快速成交，价格不可控）
        - ioc_limit: IOC限价单（快速成交 + 价格保护，推荐）⭐
        - limit: 普通限价单（价格可控，可能延迟成交）
        
        Args:
            order: 订单对象
            symbol: 交易对
            side: 买卖方向
            amount: 数量
            
        Returns:
            (order_type, price, params)
        """
        params = {}
        
        # ✅ Gate.io 强制要求：市价单必须是 IOC
        if order.order_type == OrderType.MARKET:
            params['timeInForce'] = 'IOC'
        
        # 1. 如果订单已指定价格和类型，直接使用
        if order.order_type == OrderType.LIMIT and order.price:
            return 'limit', order.price, params
        
        # 2. 从订单元数据或全局配置获取订单模式
        order_mode = order.metadata.get('order_mode', 'ioc_limit')  # 默认IOC限价单
        
        # 3. 根据订单模式准备参数
        if order_mode == 'market':
            # 市价单：无价格保护，立即成交
            return 'market', None, params
            
        elif order_mode == 'ioc_limit':
            # IOC限价单：快速成交 + 价格保护（推荐）
            params['timeInForce'] = 'IOC'
            params['postOnly'] = False
            
            # ✅ 获取滑点保护配置（默认0.1%）
            slippage_buffer = order.metadata.get('slippage_buffer', 0.1)
            slippage_multiplier_sell = 1 - (slippage_buffer / 100)  # 例：0.999 for 0.1%
            slippage_multiplier_buy = 1 + (slippage_buffer / 100)   # 例：1.001 for 0.1%
            
            # ✅ 优先使用信号触发时的价格，如果没有则重新获取
            signal_gate_bid = order.metadata.get('signal_gate_bid')
            signal_gate_ask = order.metadata.get('signal_gate_ask')
            price_source = 'signal'  # 价格来源
            
            if signal_gate_bid and signal_gate_ask:
                # 使用信号触发时的价格（推荐）
                ticker_bid = signal_gate_bid
                ticker_ask = signal_gate_ask
                ticker_last = None  # 信号中可能没有last
                self.logger.info(
                    f"✅ 使用信号触发时的盘口价格: BID=${ticker_bid:.2f}, ASK=${ticker_ask:.2f}"
                )
            else:
                # 回退：重新获取当前价格
                price_source = 'realtime'
                ticker = await self._fetch_ticker_with_retry(symbol)
                ticker_bid = ticker['bid']
                ticker_ask = ticker['ask']
                ticker_last = ticker.get('last')
                self.logger.warning(
                    f"⚠️ 信号中无盘口价格，使用实时价格: BID=${ticker_bid:.2f}, ASK=${ticker_ask:.2f}"
                )
            
            # 根据方向选择参考价格和计算限价
            if side == 'sell':
                # 卖出：使用买一价(BID)，略降保证成交
                reference_price = ticker_bid
                limit_price = reference_price * slippage_multiplier_sell
                price_type = 'BID'
            else:
                # 买入：使用卖一价(ASK)，略增保证成交
                reference_price = ticker_ask
                limit_price = reference_price * slippage_multiplier_buy
                price_type = 'ASK'
            
            self.logger.info(
                f"📊 IOC限价单定价: {symbol} {side}",
                extra={
                    'order_mode': 'ioc_limit',
                    'price_source': price_source,  # ✅ 记录价格来源
                    'reference_price_type': price_type,
                    'reference_price': reference_price,
                    'limit_price': limit_price,
                    'slippage_buffer_pct': slippage_buffer,  # ✅ 显示配置的滑点保护
                    'ticker_bid': ticker_bid,
                    'ticker_ask': ticker_ask
                }
            )
            
            return 'limit', limit_price, params
            
        elif order_mode == 'limit':
            # 普通限价单：需要指定价格
            if not order.price:
                # 如果未指定价格，使用盘口价
                ticker = await self._fetch_ticker_with_retry(symbol)
                price = ticker['ask'] if side == 'buy' else ticker['bid']
                self.logger.warning(
                    f"⚠️ 限价单未指定价格，使用盘口价: ${price:.2f}"
                )
            else:
                price = order.price
            
            return 'limit', price, params

        elif order_mode in ['trigger', 'trigger_stop', 'stop_loss']:
            # 计划委托 (Trigger Order)
            # Gate.io Futures: type='trigger'
            # 参数: triggerPrice, rule (1=up, 2=down)
            
            trigger_price = order.metadata.get('triggerPrice')
            if trigger_price:
                params['triggerPrice'] = trigger_price
                params['stopPrice'] = trigger_price # CCXT 通用
            
            rule = order.metadata.get('rule') # 1: >=, 2: <=
            if rule:
                params['rule'] = rule
            
            # 价格逻辑
            price = order.price
            if price is None or price == 0:
                # 触发后市价单
                price = 0.0
            
            # 确保 reduceOnly
            if order.metadata.get('reduce_only') or order.reduce_only:
                params['reduceOnly'] = True

            return 'trigger', price, params

        else:
            # 未知模式，回退到市价单
            self.logger.warning(f"⚠️ 未知订单模式 '{order_mode}'，回退到市价单")
            return 'market', None, params
    
    async def _fetch_ticker_with_retry(
        self,
        symbol: str,
        max_retries: int = 3
    ) -> dict:
        """
        获取ticker数据（带重试）
        
        Args:
            symbol: 交易对
            max_retries: 最大重试次数
            
        Returns:
            ticker数据字典
        """
        # 延迟导入以避免模块缺失时的顶层报错
        import ccxt

        for retry in range(max_retries):
            try:
                loop = asyncio.get_event_loop()
                ticker = await loop.run_in_executor(
                    None,
                    lambda: self._exchange.fetch_ticker(symbol)
                )
                
                # 验证必要字段
                if not ticker.get('bid') or not ticker.get('ask'):
                    raise ValueError(f"Ticker缺少bid/ask数据: {ticker}")
                
                return ticker
                
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                if retry < max_retries - 1:
                    self.logger.warning(
                        f"⚠️ 获取ticker失败（尝试 {retry + 1}/{max_retries}），重试..."
                    )
                    await asyncio.sleep(1 * (retry + 1))
                else:
                    raise ValueError(f"无法获取ticker数据（已重试{max_retries}次）: {e}")
        
        raise ValueError("获取ticker失败")
    
    async def _apply_usdt_pricing(self, order: Order, side: str) -> float:
        """
        应用 USDT 计价逻辑，将目标 USDT 金额转换为合约数量。
        """
        symbol = order.symbol
        
        # 获取市场信息
        markets = self._exchange.markets
        
        if markets is None:
            self.logger.warning("市场信息未加载，尝试重新加载...")
            self._exchange.load_markets()
            markets = self._exchange.markets
        
        if markets is None:
            raise ValueError("无法加载市场信息")
        
        market = markets.get(symbol)
        if not market:
            # 尝试通过符号变体查找（例如 TRUST_USDT vs TRUST/USDT:USDT）
            # Gate 的符号格式比较多样，需要兼容
            
            # 优先查找 swap 类型的市场（因为我们主要做合约交易）
            # 如果在现货市场找到了匹配 ID，但没有 type='swap'，会得到错误的 contractSize (None/1.0)
            # 而合约市场的 contractSize 是 10.0
            
            candidate_market = None
            
            for m_symbol, m_info in markets.items():
                # 检查 id (TRUST_USDT) 或 symbol (TRUST/USDT:USDT)
                is_match = (m_info.get('id') == symbol or m_symbol == symbol)
                
                if is_match:
                    # 检查是否为 swap 类型
                    # CCXT 通常标记为 'swap' 或 'future'，或者 info.type='direct'/'inverse'
                    # 这里简化检查：如果 type 是 swap，优先采用
                    if m_info.get('type') == 'swap' or m_info.get('swap'):
                        market = m_info
                        # 更新 symbol 为 CCXT 标准格式
                        symbol = m_symbol
                        break
                    
                    # 如果是第一次匹配（且不是 swap），先暂存，继续找有没有 swap
                    if candidate_market is None:
                        candidate_market = m_info
                        # 如果 symbol 还没更新，也暂存
                        if m_symbol != symbol:
                            # 注意：这里我们不能轻易改 symbol，除非最终确定
                            pass

            # 如果没找到 swap，但找到了其他匹配（如 spot），回退使用
            if not market and candidate_market:
                self.logger.warning(
                    f"未找到 {symbol} 的 swap 市场，回退使用 {candidate_market.get('type')} 市场 (ID: {candidate_market.get('id')})"
                )
                market = candidate_market
                if market.get('symbol') and market.get('symbol') != symbol:
                    symbol = market.get('symbol')
            
            if not market:
                raise ValueError(f"找不到市场信息: {symbol}")
        
        # 优先使用信号触发时的价格
        signal_gate_bid = order.metadata.get('signal_gate_bid')
        signal_gate_ask = order.metadata.get('signal_gate_ask')
        
        if signal_gate_bid and signal_gate_ask:
            # 使用信号触发时的价格
            # 根据方向选择合适的参考价格：买入用ask，卖出用bid（保守估算）
            reference_price = signal_gate_ask if side == 'buy' else signal_gate_bid
            price_source = 'signal'
            ticker_bid, ticker_ask = signal_gate_bid, signal_gate_ask
            ticker_last = None
        else:
            # 回退：重新获取当前价格
            price_source = 'realtime'
            ticker = await self._fetch_ticker_with_retry(symbol)
            ticker_bid = ticker['bid']
            ticker_ask = ticker['ask']
            ticker_last = ticker.get('last')
            reference_price = ticker_ask if side == 'buy' else ticker_bid
            self.logger.warning("⚠️ USDT计价：信号中无盘口价格，使用实时价格")
        
        if not reference_price:
            raise ValueError(f"无法获取参考价格进行 USDT 计价: {symbol}")
            
        # 获取 quanto_multiplier（contractSize）
        contract_size = market.get('contractSize', 1.0)
        # Gate有时返回None或0，默认为1
        if contract_size is None or contract_size <= 0:
            contract_size = 1.0
            
        order.metadata['contract_size'] = contract_size
        
        # 调用通用工具计算数量
        quantity, raw_qty = compute_usdt_quantity(
            value_usd=order.target_value_usd,
            price=reference_price,
            contract_size=contract_size,
            min_qty=market.get('limits', {}).get('amount', {}).get('min', 0.0),
            precision=market.get('precision', {}).get('amount'),
            step_size=market.get('limits', {}).get('amount', {}).get('step') # Gate 可能用 step
        )

        if quantity <= 0:
             raise ValueError("USDT计价计算结果无效")

        # 更新订单数量
        order.quantity = quantity
        
        self.logger.info(
            f"💵 USDT计价转换: {order.target_value_usd} USDT → {quantity} 张合约",
            extra={
                'pricing_mode': 'usdt',
                'target_value_usd': order.target_value_usd,
                'symbol': symbol,
                'price_source': price_source,
                'reference_price': reference_price,
                'ticker_last': ticker_last,
                'ticker_bid': ticker_bid,
                'ticker_ask': ticker_ask,
                'contract_size': contract_size,
                'raw_quantity': raw_qty,
                'final_amount': quantity,
                'actual_value': quantity * reference_price * contract_size
            }
        )
        return quantity

    async def _submit_real_order(self, order: Order) -> bool:
        """
        提交真实订单到 Gate.io（T074）
        
        使用 ccxt 库调用 Gate.io API。
        
        Args:
            order: 订单对象
            
        Returns:
            True 如果提交成功
        """
        try:
            # 构建订单参数
            symbol = order.symbol
            side = order.side.value
            
            # === Phase 6.1: USDT计价支持 ===
            if order.pricing_mode == 'usdt' and order.target_value_usd:
                amount = await self._apply_usdt_pricing(order, side)
            else:
                amount = order.quantity
            
            # ✅ 改进：支持可配置的订单类型和价格策略
            order_type, price, params = await self._prepare_order_params(
                order, symbol, side, amount
            )
            
            # ✅ 添加 clientOrderId 防止重试导致重复下单
            # CCXT Gate 实现会将 clientOrderId 映射到 text 字段 (gate v4)
            # Gate 限制 clientOrderId/text 长度为 28 字符
            if order.order_id:
                cid = order.order_id
                if len(cid) > 28:
                    # 如果太长，截取前 28 位，或者使用更短的格式
                    # uuid4 是 36 位，所以必须截取或重新生成
                    cid = f"t-{int(time.time())}-{cid[:8]}"
                    if len(cid) > 28:
                        cid = cid[:28]
                params['clientOrderId'] = cid
            
            # === reduceOnly保护：仅减仓模式 ===
            # 防止平仓订单意外变成开仓订单
            if order.reduce_only:
                params['reduceOnly'] = True
                self.logger.info(
                    f"🛡️ 启用仅减仓保护: {symbol}",
                    extra={'reduce_only': True}
                )
            
            self.logger.info(
                f"🔴 提交真实订单到 Gate.io: {symbol} {side} {amount} @ {price}",
                extra={
                    'symbol': symbol,
                    'side': side,
                    'type': order_type,
                    'amount': amount,
                    'price': price,
                    'params': params,
                    'reduce_only': order.reduce_only # DEBUG
                }
            )
            print(f"🔴 [Executor] Submit Order: {side} {amount} @ {price}, reduce_only={order.reduce_only}")
            
            # 调用 ccxt 下单（同步方法，在 asyncio 中运行）
            # 添加重试逻辑处理网络错误
            import asyncio
            import ccxt
            
            loop = asyncio.get_event_loop()
            max_retries = 3
            retry_delay = 2  # 秒
            
            response = None
            last_error = None
            
            # 如果使用了 reduce_only，使用带有 fallback 的提交逻辑
            if order.reduce_only and order_type != 'trigger':
                 print(f"⚠️ [Executor] 使用 Reduce-Only Fallback 逻辑")
                 response = await self._submit_order_with_reduce_only_fallback(
                    loop, symbol, order_type, side, amount, price, params
                 )
            elif order_type == 'trigger':
                 # ✅ 处理 Gate Futures 触发订单 (Plan Order / Stop Loss)
                 # 使用专门的 private_futures_post_settle_price_orders
                 print(f"⚠️ [Executor] 提交 Trigger Order (Stop Loss/Take Profit)")
                 
                 # 构造 Trigger Order Payload
                 # Initial: 触发后实际下的单
                 initial_order = {
                     'contract': symbol.replace('/', '_').replace(':USDT', ''),
                     'size': int(amount) if side == 'buy' else int(-amount), # Gate API: 正买负卖
                     'price': str(price) if price else "0", # 0 for market
                     'tif': 'ioc' if (price is None or price == 0) else 'gtc',
                     'reduce_only': True if order.reduce_only else False
                 }
                 if params.get('reduceOnly') or params.get('reduce_only'):
                     initial_order['reduce_only'] = True
                 
                 # Trigger: 触发条件
                 raw_trigger_price = float(params.get('triggerPrice', 0))
                 # ✅ 修正: 必须使用 price_to_precision 格式化价格，否则报错 invalid argument
                 formatted_trigger_price = self._exchange.price_to_precision(symbol, raw_trigger_price)
                 
                 trigger_cond = {
                     'strategy_type': 0, # 0: price trigger
                     'price_type': 1,    # 1: mark price (usually safer for SL)
                     'price': formatted_trigger_price,
                     'rule': int(params.get('rule', 1)), # 1: >=, 2: <=
                     'expiration': 2592000 # ✅ 修正: 使用 30 天有效期 (86400 * 30)，必须是 86400 的整数倍
                 }
                 
                 trigger_params = {
                     'settle': 'usdt',
                     'initial': initial_order,
                     'trigger': trigger_cond
                 }
                 
                 self.logger.info(f"Trigger Params: {trigger_params}")
                 
                 method_name = 'private_futures_post_settle_price_orders'
                 if hasattr(self._exchange, method_name):
                     func = getattr(self._exchange, method_name)
                     response = await loop.run_in_executor(None, lambda: func(trigger_params))
                 else:
                     raise ValueError(f"CCXT method {method_name} not found")

            else:
                 # 普通提交
                 for attempt in range(max_retries):
                    try:
                        # 使用 loop.run_in_executor 调用同步的 create_order
                        response = await loop.run_in_executor(
                            None,
                            lambda: self._exchange.create_order(
                                symbol, order_type, side, amount, price, params
                            )
                        )
                        break  # 成功则退出重试循环
                        
                    except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                        last_error = e
                        error_msg = str(e)
                        if attempt < max_retries - 1:
                            self.logger.warning(
                                f"⚠️ 网络错误（尝试 {attempt + 1}/{max_retries}）: {error_msg[:100]}，{retry_delay}秒后重试...",
                                extra={
                                    "attempt": attempt + 1,
                                    "max_retries": max_retries,
                                    "error": error_msg[:100]
                                }
                            )
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2  # 指数退避
                        else:
                            # 最后一次尝试失败 - 显示详细错误
                            detailed_error = f"网络错误: {error_msg[:200]}"
                            self.logger.error(f"❌ 订单提交失败（已重试{max_retries}次）: {detailed_error}")
                            raise ccxt.NetworkError(f"Max retries exceeded - {detailed_error}")
                    
                    except Exception as e:
                        # 其他类型的错误直接抛出，不重试
                        raise
            
            # 检查response是否为None
            if response is None:
                self.logger.error(f"Gate.io 返回 None 响应（可能余额不足或API错误）")
                order.reject_reason = "Gate.io返回空响应（可能余额不足）"
                return False
            
            # 保存交易所响应
            order.exchange_order_id = response.get('id')
            order.exchange_response = response
            
            # 更新实际成交信息（如果已成交）
            if response.get('status') == 'closed' or response.get('filled'):
                order.actual_fill_quantity = response.get('filled', 0)
                # ✅ 同时更新基础字段 filled_quantity，确保 DualExecutor 能读取到
                order.filled_quantity = float(response.get('filled', 0))
                
                order.actual_fill_price = response.get('average')
                # 安全获取fee信息
                fee_info = response.get('fee')
                if fee_info and isinstance(fee_info, dict):
                    order.actual_fees = fee_info.get('cost', 0)
                else:
                    order.actual_fees = 0
                order.status = OrderStatus.FILLED if response.get('status') == 'closed' else OrderStatus.PARTIAL
            
            # ✅ Phase 优化: 对于市价单，确认是否成交
            if order_type == 'market' and response.get('status') != 'closed':
                self.logger.debug("市价单未立即成交，开始确认...")
                confirmed = await self._confirm_order_fill(order, timeout_sec=5)
                if not confirmed:
                    self.logger.error("市价单未能在超时时间内成交")
                    return False
            
            # === Phase 5.1: 更新每日统计（T075）===
            self.daily_trades += 1
            
            self.logger.info(
                f"✅ 真实订单提交成功: {order.exchange_order_id}",
                extra={
                    'exchange_order_id': order.exchange_order_id,
                    'status': response.get('status'),
                    'filled': response.get('filled'),
                    'daily_trades': self.daily_trades
                }
            )
            
            return True
            
        except Exception as e:
            # ✅ 简单错误分类
            error_msg = str(e)
            
            # 判断是否为余额不足等不可重试错误
            is_retryable = not any(keyword in error_msg.lower() for keyword in [
                'insufficient', 'balance', 'margin', 'invalid', 'permission', 'whitelist'
            ])
            
            # 保存错误信息到订单对象
            order.reject_reason = error_msg[:200]
            order.is_retryable = is_retryable
            
            self.logger.error(
                f"❌ 真实订单提交失败: {error_msg[:200]}",
                exc_info=True,
                extra={
                    'symbol': order.symbol,
                    'side': order.side.value,
                    'quantity': order.quantity,
                    'is_retryable': is_retryable,
                    'original_error': error_msg[:200]
                }
            )
            return False
    
    async def _handle_ioc_order(self, order: Order) -> None:
        """处理 IOC 订单逻辑"""
        # 等待 IOC 超时时间
        await asyncio.sleep(self.ioc_timeout_sec)
        
        # 检查订单状态
        status = await self.get_order_status(order)
        
        if status == OrderStatus.SUBMITTED:
            # 未成交，取消订单并回退到市价单
            self.logger.warning(
                f"IOC 订单未成交，回退到市价单: {order.order_id}"
            )
            await self.cancel_order(order)
            
            # 创建市价单
            order.order_type = OrderType.MARKET
            order.price = None
            await self._submit_paper_order(order)
    
    async def _confirm_order_fill(
        self,
        order: Order,
        timeout_sec: int = 10,
        check_interval: float = 0.5
    ) -> bool:
        """
        确认订单是否成交
        
        对于市价单，理论上应该立即成交。
        但在极端情况下（如流动性不足），可能需要等待。
        
        Args:
            order: 订单对象
            timeout_sec: 超时时间（秒）
            check_interval: 查询间隔（秒）
        
        Returns:
            是否成交
        """
        if not order.exchange_order_id:
            self.logger.error("订单没有 exchange_order_id，无法确认")
            return False
        
        elapsed = 0
        
        self.logger.debug(
            f"开始确认订单成交状态: {order.exchange_order_id}",
            extra={'timeout': timeout_sec}
        )
        
        while elapsed < timeout_sec:
            try:
                # 查询订单状态
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._exchange.fetch_order(
                        id=order.exchange_order_id,
                        symbol=order.symbol
                    )
                )
                
                status = response.get('status')
                
                if status == 'closed':
                    # 已成交
                    order.actual_fill_quantity = response.get('filled', 0)
                    order.actual_fill_price = response.get('average')
                    
                    fee_info = response.get('fee')
                    if fee_info and isinstance(fee_info, dict):
                        order.actual_fees = fee_info.get('cost', 0)
                    else:
                        order.actual_fees = 0
                    
                    order.status = OrderStatus.FILLED
                    
                    self.logger.info(
                        f"✅ 订单已确认成交: {order.exchange_order_id}",
                        extra={
                            'filled': order.actual_fill_quantity,
                            'price': order.actual_fill_price,
                            'elapsed': elapsed
                        }
                    )
                    return True
                
                elif status == 'cancelled':
                    # 已取消
                    order.status = OrderStatus.CANCELLED
                    self.logger.warning(f"⚠️ 订单已被取消: {order.exchange_order_id}")
                    return False
                
                # 等待下次查询
                await asyncio.sleep(check_interval)
                elapsed += check_interval
            
            except Exception as e:
                self.logger.error(f"查询订单状态失败: {e}", exc_info=True)
                return False
        
        # 超时
        self.logger.warning(
            f"⏰ 订单确认超时 ({timeout_sec}秒): {order.exchange_order_id}",
            extra={'elapsed': elapsed}
        )
        return False
    
    async def cancel_order(self, order: Order) -> bool:
        """
        取消订单
        
        Args:
            order: 订单对象
            
        Returns:
            True 如果取消成功
        """
        self.logger.info(f"取消订单: {order.order_id}")
        
        if self.paper_trading:
            # 纸交易模式：直接标记为已取消
            await asyncio.sleep(0.05)
            order.status = OrderStatus.CANCELLED
            self._stats["orders_cancelled"] += 1
            await self._notify_order_sync(order, "撤销")
            return True
        else:
            # ✅ 真实交易：调用 Gate.io API
            try:
                if not order.exchange_order_id:
                    self.logger.error("订单没有 exchange_order_id，无法取消")
                    return False
                
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._exchange.cancel_order(
                        id=order.exchange_order_id,
                        symbol=order.symbol
                    )
                )
                
                if response:
                    order.status = OrderStatus.CANCELLED
                    self._stats["orders_cancelled"] += 1
                    
                    self.logger.info(
                        f"✅ 订单已取消: {order.exchange_order_id}",
                        extra={'response': response}
                    )
                    await self._notify_order_sync(order, "撤销")
                    return True
                else:
                    self.logger.error("取消订单返回空响应")
                    return False
            
            except Exception as e:
                self.logger.error(
                    f"❌ 取消订单失败: {e}",
                    exc_info=True,
                    extra={'order_id': order.exchange_order_id}
                )
                return False
    
    async def get_order_status(self, order: Order) -> OrderStatus:
        """
        查询订单状态
        
        Args:
            order: 订单对象
            
        Returns:
            当前订单状态
        """
        if self.paper_trading:
            # 纸交易模式：返回当前状态
            return order.status
        else:
            # TODO: 实现真实的状态查询
            return order.status
    
    async def get_balance(self, asset: str = "USDT") -> Dict[str, float]:
        """
        查询余额
        
        Args:
            asset: 资产符号（默认 USDT）
            
        Returns:
            {
                'total': 总余额,
                'free': 可用余额,
                'used': 冻结余额
            }
        """
        if self.paper_trading:
            balance = self._paper_balances.get(asset, 0.0)
            return {
                'total': balance,
                'free': balance,
                'used': 0.0
            }
        else:
            # ✅ 真实交易：查询 Gate.io 余额
            try:
                loop = asyncio.get_event_loop()
                balance_data = await loop.run_in_executor(
                    None,
                    lambda: self._exchange.fetch_balance()
                )
                
                # 优先从 info 获取 equity（总权益，含未实现盈亏）
                info = balance_data.get('info', {})
                if isinstance(info, list) and len(info) > 0:
                    info = info[0]
                elif not isinstance(info, dict):
                    info = {}
                
                # Gate.io 合约账户: equity = 总权益(含未实现盈亏), available = 可用
                equity = float(info.get('equity', 0) or 0)
                available = float(info.get('available', 0) or 0)
                
                if equity > 0:
                    # 使用 equity 作为总余额（包含未实现盈亏）
                    return {
                        'total': equity,
                        'free': available,
                        'used': equity - available
                    }
                elif asset in balance_data:
                    # 回退到 CCXT 标准字段
                    asset_balance = balance_data[asset]
                    return {
                        'total': float(asset_balance.get('total', 0) or 0),
                        'free': float(asset_balance.get('free', 0) or 0),
                        'used': float(asset_balance.get('used', 0) or 0)
                    }
                else:
                    self.logger.warning(f"未找到 {asset} 余额")
                    return {'total': 0.0, 'free': 0.0, 'used': 0.0}
            
            except Exception as e:
                self.logger.error(f"查询余额失败: {e}", exc_info=True)
                return {'total': 0.0, 'free': 0.0, 'used': 0.0}
    
    async def get_positions(self, symbol: str = None) -> list:
        """
        查询持仓
        
        Args:
            symbol: 交易对（可选，None表示查询所有）
        
        Returns:
            持仓列表
        """
        if self.paper_trading:
            return []
        
        # ✅ 防御性检查：如果 symbol 异常短，可能是错误数据，直接返回空
        if symbol and isinstance(symbol, str) and len(symbol) < 2:
            self.logger.warning(f"忽略异常的持仓查询 symbol: '{symbol}'")
            return []
        
        try:
            loop = asyncio.get_event_loop()
            # CCXT fetch_positions expects a list of symbols or None
            # If a single string is passed, wrap it in a list to prevent it from being iterated as characters
            symbols_arg = [symbol] if symbol else None
            
            positions = await loop.run_in_executor(
                None,
                lambda: self._exchange.fetch_positions(symbols_arg)
            )
            
            # 记录原始持仓数据
            if positions:
                self.logger.info(f"📊 Gate.io 返回 {len(positions)} 个持仓原始数据")
                for i, pos in enumerate(positions[:3]):  # 只记录前3个
                    self.logger.debug(
                        f"持仓 {i+1}: symbol={pos.get('symbol')}, "
                        f"contracts={pos.get('contracts')}, "
                        f"side={pos.get('side')}, "
                        f"notional={pos.get('notional')}, "
                        f"info.size={pos.get('info', {}).get('size')}"
                    )
            
            result = []
            for pos in positions:
                # 获取合约数量（可能在不同字段）
                contracts = float(pos.get('contracts', 0) or 0)
                
                # ✅ 修复：如果 contracts 为 0，尝试从其他字段获取
                if contracts == 0:
                    # Gate.io 可能在 info 字段中返回实际数据
                    info = pos.get('info', {})
                    contracts = float(info.get('size', 0) or 0)
                
                # ✅ 修复：检查多个可能表示持仓的字段
                notional = float(pos.get('notional', 0) or 0)
                margin = float(pos.get('initialMargin', 0) or pos.get('margin', 0) or 0)
                
                # 只有当所有持仓相关字段都为0时，才跳过
                if contracts == 0 and notional == 0 and margin == 0:
                    continue
                
                # 解析持仓数据
                side = pos.get('side', '')
                
                # ✅ 修复：size 应该反映正负（多=正，空=负）
                if side == 'long':
                    size_value = abs(contracts)
                elif side == 'short':
                    size_value = -abs(contracts)
                else:
                    # 如果没有 side，根据 contracts 符号判断
                    size_value = contracts
                
                result.append({
                    'symbol': pos.get('symbol'),
                    'side': side,  # 'long' or 'short'
                    'size': size_value,
                    'contracts': abs(contracts),  # 合约数始终为正
                    'entryPrice': float(pos.get('entryPrice', 0) or 0),
                    'markPrice': float(pos.get('markPrice', 0) or 0),
                    'unrealizedPnl': float(pos.get('unrealizedPnl', 0) or 0),
                    'notional': notional,
                    'liquidationPrice': float(pos.get('liquidationPrice', 0) or 0),
                    'leverage': float(pos.get('leverage', 0) or 0),
                    'initialMargin': margin,
                    'marginRatio': float(pos.get('marginRatio', 0) or 0),
                    'percentage': float(pos.get('percentage', 0) or 0)
                })
            
            self.logger.debug(
                f"查询到 {len(result)} 个持仓",
                extra={'symbol': symbol, 'count': len(result)}
            )
            
            return result
        
        except Exception as e:
            self.logger.error(f"查询持仓失败: {e}", exc_info=True)
            return []

    async def _run_ccxt_method(self, method_name: str, params: Optional[dict] = None):
        """
        在后台线程执行指定的 ccxt 私有方法。
        用于 Watchdog/StopManager 等场景直接访问底层 API。
        """
        if not self._exchange:
            raise RuntimeError("Exchange not initialized")
        if not hasattr(self._exchange, method_name):
            raise AttributeError(f"ccxt exchange has no method '{method_name}'")
        
        loop = asyncio.get_event_loop()
        func = getattr(self._exchange, method_name)
        return await loop.run_in_executor(None, lambda: func(params or {}))
    
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        设置杠杆倍数
        
        Gate.io 期货杠杆设置:
        - leverage=0: 全仓模式 (cross)
        - leverage>0: 逐仓模式 (isolated) + 指定杠杆倍数
        
        Args:
            symbol: 交易对
            leverage: 杠杆倍数 (0 表示全仓)
            
        Returns:
            True 如果成功
        """
        if self.paper_trading:
            mode_str = "全仓" if leverage == 0 else f"逐仓 {leverage}x"
            self.logger.info(f"[纸交易] 设置 {symbol} 杠杆为 {mode_str}")
            return True
            
        try:
            mode_str = "全仓" if leverage == 0 else f"逐仓 {leverage}x"
            self.logger.info(f"🔧 设置 {symbol} 杠杆为 {mode_str}")
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._exchange.set_leverage(leverage, symbol)
            )
            self.logger.info(f"✅ 杠杆设置成功: {mode_str}")
            return True
            
        except Exception as e:
            # 处理持仓/挂单锁定错误
            err_str = str(e).lower()
            if "position_holding" in err_str or "can not switch" in err_str or "order" in err_str:
                self.logger.warning(f"⚠️ 无法切换杠杆（已有持仓或挂单）: {e}")
                return True
                
            self.logger.error(f"❌ 设置杠杆失败: {e}", exc_info=True)
            return False

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> bool:
        """
        设置保证金模式
        
        Gate.io 通过 leverage 值来控制保证金模式:
        - cross (全仓): leverage = 0
        - isolated (逐仓): leverage > 0
        
        注意: 有挂单或持仓时无法切换模式！
        
        Args:
            symbol: 交易对
            margin_mode: 'cross' (全仓) 或 'isolated' (逐仓)
            
        Returns:
            True 如果成功
        """
        if self.paper_trading:
            self.logger.info(f"[纸交易] 设置 {symbol} 保证金模式为 {margin_mode}")
            return True
            
        try:
            self.logger.info(f"🔧 设置 {symbol} 保证金模式为 {margin_mode}")
            
            loop = asyncio.get_event_loop()
            
            # Gate.io 逻辑：
            # margin_mode='cross' -> leverage=0
            # margin_mode='isolated' -> 依赖后续 set_leverage 设置具体值
            
            if margin_mode == 'cross':
                self.logger.info(f"Gate.io 全仓模式：设置杠杆为 0")
                await loop.run_in_executor(
                    None,
                    lambda: self._exchange.set_leverage(0, symbol)
                )
                self.logger.info(f"✅ 全仓模式设置成功 (leverage=0)")
            else:
                # 逐仓模式，依赖后续的 set_leverage 调用
                self.logger.info(f"Gate.io 逐仓模式：等待 set_leverage 设置具体倍数")
                
            return True
            
        except Exception as e:
            # 处理持仓/挂单锁定错误
            err_str = str(e).lower()
            if "position_holding" in err_str or "can not switch" in err_str or "order" in err_str:
                self.logger.warning(f"⚠️ 无法切换保证金模式（已有持仓或挂单）: {e}")
                return True
                
            self.logger.error(f"❌ 设置保证金模式失败: {e}", exc_info=True)
            return False

    async def get_account_info(self) -> Dict:
        """
        查询账户信息
        
        Returns:
            {
                'margin_mode': 保证金模式 (cross/isolated),
                'total_equity': 总权益,
                'available_margin': 可用保证金,
                'used_margin': 已用保证金,
                'margin_ratio': 保证金率,
                'maintenance_margin': 维持保证金,
                'unrealized_pnl': 未实现盈亏,
                'wallet_balance': 钱包余额,
                'total_position_margin': 持仓保证金,
                'total_order_margin': 委托保证金
            }
        """
        if self.paper_trading:
            # 纸交易模式：返回模拟数据
            return {
                'margin_mode': 'isolated',
                'total_equity': self._paper_balances.get('USDT', 0),
                'available_margin': self._paper_balances.get('USDT', 0),
                'used_margin': 0.0,
                'margin_ratio': 0.0,
                'maintenance_margin': 0.0,
                'unrealized_pnl': 0.0,
                'wallet_balance': self._paper_balances.get('USDT', 0),
                'total_position_margin': 0.0,
                'total_order_margin': 0.0
            }
        
        try:
            loop = asyncio.get_event_loop()
            
            # 查询账户信息
            balance_data = await loop.run_in_executor(
                None,
                lambda: self._exchange.fetch_balance()
            )
            
            # 从账户信息中提取关键数据
            info = balance_data.get('info', {})
            
            # Gate.io 返回的 info 可能是列表（多个账户）或字典
            if isinstance(info, list) and len(info) > 0:
                info = info[0]  # 使用第一个账户
            elif not isinstance(info, dict):
                info = {}
            
            # 不同交易所返回的字段可能不同，这里做通用处理
            account_info = {
                'margin_mode': info.get('mode', info.get('marginMode', 'unknown')),
                'total_equity': float(info.get('equity', info.get('totalEquity', 0)) or 0),
                'available_margin': float(info.get('available', info.get('availableMargin', 0)) or 0),
                'used_margin': float(info.get('margin', info.get('usedMargin', 0)) or 0),
                'margin_ratio': float(info.get('margin_ratio', info.get('marginRatio', 0)) or 0),
                'maintenance_margin': float(info.get('maintenance_margin', info.get('maintenanceMargin', 0)) or 0),
                'unrealized_pnl': float(info.get('unrealized_pnl', info.get('unrealizedPnl', 0)) or 0),
                'wallet_balance': float(info.get('wallet_balance', info.get('walletBalance', 0)) or 0),
                'total_position_margin': float(info.get('total_position_margin', info.get('positionMargin', 0)) or 0),
                'total_order_margin': float(info.get('total_order_margin', info.get('orderMargin', 0)) or 0)
            }
            
            self.logger.debug(
                "账户信息查询成功",
                extra={
                    'total_equity': account_info['total_equity'],
                    'available_margin': account_info['available_margin'],
                    'margin_ratio': account_info['margin_ratio']
                }
            )
            
            return account_info
        
        except Exception as e:
            self.logger.error(f"查询账户信息失败: {e}", exc_info=True)
            return {}
    
    async def get_open_orders(self, symbol: str = None) -> list:
        """
        获取当前挂单
        
        Args:
            symbol: 交易对（可选）
            
        Returns:
            挂单列表
        """
        if self.paper_trading:
            return []
            
        try:
            loop = asyncio.get_event_loop()
            orders = await loop.run_in_executor(
                None,
                lambda: self._exchange.fetch_open_orders(symbol=symbol)
            )
            return orders
        except Exception as e:
            self.logger.error(f"获取挂单失败: {e}", exc_info=True)
            return []

    async def get_open_orders(self, symbol: str = None) -> list:
        """
        获取当前挂单
        
        Args:
            symbol: 交易对（可选）
            
        Returns:
            挂单列表
        """
        if self.paper_trading:
            return []
            
        try:
            loop = asyncio.get_event_loop()
            orders = await loop.run_in_executor(
                None,
                lambda: self._exchange.fetch_open_orders(symbol=symbol)
            )
            return orders
        except Exception as e:
            self.logger.error(f"获取挂单失败: {e}", exc_info=True)
            return []

    async def get_trade_history(
        self,
        symbol: str = None,
        since: int = None,
        limit: int = 100
    ) -> list:
        """
        查询成交历史
        
        Args:
            symbol: 交易对（可选）
            since: 起始时间戳（毫秒）
            limit: 返回记录数（默认100）
        
        Returns:
            成交记录列表
        """
        if self.paper_trading:
            # 纸交易模式：返回空列表
            self.logger.debug("纸交易模式不支持成交历史查询")
            return []
        
        try:
            loop = asyncio.get_event_loop()
            
            # 查询成交历史
            trades = await loop.run_in_executor(
                None,
                lambda: self._exchange.fetch_my_trades(
                    symbol=symbol,
                    since=since,
                    limit=limit
                )
            )
            
            self.logger.debug(
                f"查询到 {len(trades)} 条成交记录",
                extra={'symbol': symbol, 'count': len(trades)}
            )
            
            return trades
        
        except Exception as e:
            self.logger.error(f"查询成交历史失败: {e}", exc_info=True)
            return []
    
    async def cancel_all_orders(self, symbol: str) -> bool:
        """
        取消所有订单 (普通订单)
        """
        if self.paper_trading:
            self.logger.info(f"[纸交易] 已取消 {symbol} 所有订单")
            return True
            
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._exchange.cancel_all_orders(symbol)
            )
            self.logger.info(f"已取消 {symbol} 所有普通订单")
            return True
        except Exception as e:
            self.logger.error(f"取消所有订单失败: {e}")
            return False
    
    async def get_plan_orders(self, symbol: str, status: str = 'open', limit: int = 100) -> list:
        """获取计划委托"""
        if self.paper_trading:
            return []
            
        try:
            settle = 'usdt'
            params = {
                'contract': symbol.replace('/', '_').replace(':USDT', ''),
                'limit': limit,
                'status': status,
                'settle': settle
            }
            
            # 尝试正确的方法名
            # 路径: /futures/{settle}/price_orders
            # CCXT Python: private_futures_get_settle_price_orders
            
            method_name = 'private_futures_get_settle_price_orders'
            
            loop = asyncio.get_event_loop()
            
            if hasattr(self._exchange, method_name):
                func = getattr(self._exchange, method_name)
                # 注意：settle 参数需要包含在 params 中
                orders = await loop.run_in_executor(None, lambda: func(params))
                return orders if isinstance(orders, list) else []
            else:
                # 调试信息：打印所有方法
                # self.logger.info(f"Available methods: {[m for m in dir(self._exchange) if 'price_orders' in m]}")
                self.logger.error(f"无法找到获取计划委托的 CCXT 方法: {method_name}")
                return []
                
        except Exception as e:
            self.logger.error(f"获取计划委托失败: {e}", exc_info=True)
            return []

    async def cancel_all_plan_orders(self, symbol: str) -> bool:
        """取消所有计划委托"""
        if self.paper_trading:
            return True
        try:
            # Gate API 可能不支持一次性取消所有 plan orders，通常需要先查后删
            # 或者尝试 delete /futures/{settle}/price_orders
            
            # 策略：先获取所有 open plan orders，然后逐个取消
            plan_orders = await self.get_plan_orders(symbol, status='open')
            
            if not plan_orders:
                self.logger.info(f"没有需要取消的计划委托: {symbol}")
                return True
                
            tasks = []
            for order in plan_orders:
                order_id = str(order.get('id'))
                if order_id:
                    tasks.append(self.cancel_plan_order(symbol, order_id))
            
            if tasks:
                self.logger.info(f"正在取消 {len(tasks)} 个计划委托...")
                results = await asyncio.gather(*tasks, return_exceptions=True)
                success_count = sum(1 for r in results if r is True)
                self.logger.info(f"成功取消 {success_count}/{len(tasks)} 个计划委托")
                
            return True
            
        except Exception as e:
            self.logger.error(f"取消所有计划委托失败: {e}")
            return False

    async def cancel_plan_order(self, symbol: str, order_id: str) -> bool:
        """取消计划委托"""
        if self.paper_trading:
            return True
            
        try:
            settle = 'usdt'
            contract = symbol.replace('/', '_').replace(':USDT', '')
            
            loop = asyncio.get_event_loop()
            
            # 尝试正确的方法名
            # 路径: DELETE /futures/{settle}/price_orders/{order_id}
            method_name = 'private_futures_delete_settle_price_orders_order_id'
            
            if hasattr(self._exchange, method_name):
                func = getattr(self._exchange, method_name)
                # settle 参数通常作为路径参数传入
                await loop.run_in_executor(
                    None,
                    lambda: func({'contract': contract, 'order_id': order_id, 'settle': settle})
                )
                return True
            else:
                self.logger.error(f"未找到 CCXT 方法: {method_name}")
                return False
                
        except Exception as e:
            # 如果是 "Order not found" 也可以视为成功
            if "not found" in str(e).lower():
                return True
                
            self.logger.error(f"取消计划委托失败: {e}")
            return False

    async def get_order_history(
        self,
        symbol: str = None,
        since: int = None,
        limit: int = 100
    ) -> list:
        """
        查询订单历史
        
        Args:
            symbol: 交易对（可选）
            since: 起始时间戳（毫秒）
            limit: 返回记录数（默认100）
        
        Returns:
            订单列表
        """
        if self.paper_trading:
            # 纸交易模式：返回空列表
            self.logger.debug("纸交易模式不支持订单历史查询")
            return []
        
        try:
            loop = asyncio.get_event_loop()
            
            # 查询订单历史
            orders = await loop.run_in_executor(
                None,
                lambda: self._exchange.fetch_orders(
                    symbol=symbol,
                    since=since,
                    limit=limit
                )
            )
            
            self.logger.debug(
                f"查询到 {len(orders)} 条订单记录",
                extra={'symbol': symbol, 'count': len(orders)}
            )
            
            return orders
        
        except Exception as e:
            self.logger.error(f"查询订单历史失败: {e}", exc_info=True)
            return []
    
    async def transfer_funds(
        self,
        asset: str,
        amount: float,
        from_account: str,
        to_account: str
    ) -> Dict:
        """
        资金划转
        
        Args:
            asset: 资产符号（如 "USDT"）
            amount: 划转金额
            from_account: 源账户类型（'spot'=现货, 'swap'=合约）
            to_account: 目标账户类型（'spot'=现货, 'swap'=合约）
        
        Returns:
            {
                'success': 是否成功,
                'transfer_id': 划转ID（如果成功）,
                'error': 错误信息（如果失败）
            }
        """
        if self.paper_trading:
            # 纸交易模式：模拟划转成功
            self.logger.info(
                f"[纸交易] 模拟资金划转: {amount} {asset} "
                f"从 {from_account} 到 {to_account}"
            )
            return {
                'success': True,
                'transfer_id': f"paper_{int(time.time())}",
                'error': None
            }
        
        try:
            loop = asyncio.get_event_loop()
            
            # 执行资金划转
            # Gate.io API: transfer(code, amount, from_account, to_account)
            result = await loop.run_in_executor(
                None,
                lambda: self._exchange.transfer(
                    asset,
                    amount,
                    from_account,
                    to_account
                )
            )
            
            self.logger.info(
                f"资金划转成功: {amount} {asset} "
                f"从 {from_account} 到 {to_account}",
                extra={
                    'asset': asset,
                    'amount': amount,
                    'from': from_account,
                    'to': to_account,
                    'transfer_id': result.get('id')
                }
            )
            
            return {
                'success': True,
                'transfer_id': result.get('id'),
                'error': None
            }
        
        except Exception as e:
            self.logger.error(
                f"资金划转失败: {e}",
                exc_info=True,
                extra={
                    'asset': asset,
                    'amount': amount,
                    'from': from_account,
                    'to': to_account
                }
            )
            return {
                'success': False,
                'transfer_id': None,
                'error': str(e)
            }
