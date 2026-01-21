"""
Recon/Event 双轨道模块

负责网格订单的周期性对账 (Recon) 和实时成交响应 (Event)
"""

import asyncio
import time
from typing import Any, Dict, List, Optional, Set

from key_level_grid.core.types import LevelStatus
from key_level_grid.utils.logger import get_logger


class ReconEventManager:
    """
    Recon/Event 双轨道管理器
    
    - Recon 轨道: 周期性扫描，对齐预期挂单与实盘挂单
    - Event 轨道: 实时响应成交事件，增量更新订单
    """
    
    def __init__(
        self,
        position_manager,
        executor,
        config,
        trade_store,
        notifier=None,
        logger=None,
    ):
        """
        初始化双轨道管理器
        
        Args:
            position_manager: GridPositionManager 实例
            executor: GateExecutor 实例
            config: 策略配置 (KeyLevelGridConfig)
            trade_store: TradeStore 实例
            notifier: NotificationManager 实例 (可选)
            logger: 日志实例 (可选)
        """
        self.position_manager = position_manager
        self.executor = executor
        self.config = config
        self.trade_store = trade_store
        self.notifier = notifier
        self.logger = logger or get_logger(__name__)
        
        # Recon 状态
        self.recon_last_run_at: float = 0.0
        
        # Event 状态
        self._last_trade_ids: Set[str] = set()
        
        # 网格锁
        self._grid_lock = asyncio.Lock()
        self._grid_lock_until: float = 0.0
        
        # 回调
        self._notify_order_filled_callback = None
        self._mark_level_filled_callback = None
        self._mark_level_idle_callback = None
    
    def set_callbacks(
        self,
        notify_order_filled=None,
        mark_level_filled=None,
        mark_level_idle=None,
    ):
        """设置回调函数"""
        self._notify_order_filled_callback = notify_order_filled
        self._mark_level_filled_callback = mark_level_filled
        self._mark_level_idle_callback = mark_level_idle
    
    def _convert_to_gate_symbol(self, binance_symbol: str) -> str:
        """将 Binance 符号转换为 Gate 格式"""
        symbol = binance_symbol.upper()
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT:USDT"
        return symbol
    
    def get_exchange_min_qty_btc(self, contract_size: float) -> float:
        """获取交易所最小下单 BTC 数量"""
        min_contracts = self._get_exchange_min_contracts()
        return min_contracts * contract_size
    
    def _get_exchange_min_contracts(self) -> float:
        """获取交易所最小下单张数"""
        try:
            gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
            markets = self.executor._exchange.markets if self.executor else {}
            if not markets:
                return 1.0
            market = markets.get(gate_symbol, {})
            min_amount = market.get("limits", {}).get("amount", {}).get("min")
            return float(min_amount) if min_amount else 1.0
        except Exception:
            return 1.0
    
    async def run_recon_track(
        self,
        current_state,
        gate_position: Dict[str, Any],
        gate_open_orders: List[Dict],
        gate_trades: List[Dict],
        contract_size: float,
        grid_created: bool,
    ) -> None:
        """
        运行 Recon 轨道
        
        周期性对账，确保挂单与预期一致
        """
        if not grid_created or not self.position_manager.state:
            return

        now_ts = time.time()
        grid_cfg = self.position_manager.grid_config
        if now_ts - self.recon_last_run_at < grid_cfg.recon_interval_sec:
            return
        if self._grid_lock_until and now_ts < self._grid_lock_until:
            return

        async with self._grid_lock:
            # 更新持仓快照
            holdings = float(gate_position.get("contracts", 0) or 0)
            avg_entry = float(gate_position.get("entry_price", 0) or 0)
            self.position_manager.update_position_snapshot(holdings, avg_entry)
            
            if self.position_manager.state:
                self.position_manager.state.contract_size = contract_size
                
                # 对账配额
                try:
                    await self._reconcile_counters(
                        current_state,
                        holdings,
                        gate_trades,
                    )
                except Exception as e:
                    self.logger.error(f"配额对齐失败: {e}", exc_info=True)

            exchange_min_qty_btc = self.get_exchange_min_qty_btc(contract_size)
            actions = self.position_manager.build_recon_actions(
                current_price=current_state.close if current_state else 0,
                open_orders=gate_open_orders,
                exchange_min_qty_btc=exchange_min_qty_btc,
            )

        # 有撤单时加锁避免竞争
        if any(a.get("action") == "cancel" for a in actions):
            self._grid_lock_until = now_ts + grid_cfg.order_action_timeout_sec

        await self._execute_actions(actions)
        
        if actions and self.notifier:
            placed = sum(1 for a in actions if a.get("action") == "place")
            cancelled = sum(1 for a in actions if a.get("action") == "cancel")
            summary = f"新增 {placed}，撤销 {cancelled}"
            await self.notifier.notify_recon_summary(
                symbol=self.config.symbol,
                summary=summary,
            )
        
        self.recon_last_run_at = now_ts
    
    async def _reconcile_counters(
        self,
        current_state,
        holdings_btc: float,
        gate_trades: List[Dict],
    ) -> None:
        """对账配额"""
        # 组合本地账本和交易所成交记录
        local_trades = self.trade_store.load_all_trades()
        exchange_trades = [t for t in gate_trades if t.get("side") == "buy"]
        
        combined_trades = local_trades.copy()
        local_ids = {str(t.get("order_id") or t.get("id", "")) for t in local_trades if t.get("order_id") or t.get("id")}
        
        new_discovered_count = 0
        for t in exchange_trades:
            order_id = str(t.get("order_id") or t.get("id", ""))
            if order_id not in local_ids:
                combined_trades.append(t)
                self.trade_store.append_trade(t)
                new_discovered_count += 1
        
        if new_discovered_count > 0:
            self.logger.info("📓 [TradeStore] 从交易所补齐了 %d 条成交记录", new_discovered_count)

        result = self.position_manager.reconcile_counters_with_position(
            current_price=current_state.close if current_state else 0,
            holdings_btc=holdings_btc,
            recent_trades=combined_trades,
        )
        
        if result and self.notifier:
            await self.notifier.notify_quota_event(
                symbol=self.config.symbol,
                action=result.get("action", "reconcile"),
                detail=result.get("detail", ""),
            )
    
    async def run_event_track(
        self,
        current_state,
        gate_trades: List[Dict],
        contract_size: float,
    ) -> None:
        """
        运行 Event 轨道
        
        实时响应成交事件
        """
        if not self.position_manager.state:
            return

        # 初始化已处理的成交 ID
        if not self._last_trade_ids and gate_trades:
            self._last_trade_ids = {t.get("id") for t in gate_trades if t.get("id")}
            return

        # 找出新成交
        new_trades = []
        for trade in gate_trades:
            trade_id = trade.get("id")
            if not trade_id or trade_id in self._last_trade_ids:
                continue
            new_trades.append(trade)
            self._last_trade_ids.add(trade_id)

        if not new_trades:
            return

        async with self._grid_lock:
            exchange_min_qty_btc = self.get_exchange_min_qty_btc(contract_size)
            
            for trade in reversed(new_trades):
                await self._handle_trade(
                    trade,
                    current_state,
                    exchange_min_qty_btc,
                )
    
    async def _handle_trade(
        self,
        trade: Dict,
        current_state,
        exchange_min_qty_btc: float,
    ) -> None:
        """处理单个成交"""
        side = trade.get("side")
        qty = float(trade.get("amount", 0) or 0)
        price = float(trade.get("price", 0) or 0)
        cost = float(trade.get("cost", 0) or 0)
        order_id = str(trade.get("order", "") or trade.get("orderId", "") or "")
        trade_id = str(trade.get("id", "") or "")
        
        if cost <= 0 and qty > 0 and price > 0:
            cost = qty * price

        if side == "buy":
            await self._handle_buy_fill(
                price, qty, cost, order_id, trade_id,
                current_state, exchange_min_qty_btc
            )
        elif side == "sell":
            await self._handle_sell_fill(
                price, qty, cost, order_id, trade_id,
                current_state, exchange_min_qty_btc
            )
    
    async def _handle_buy_fill(
        self,
        price: float,
        qty: float,
        cost: float,
        order_id: str,
        trade_id: str,
        current_state,
        exchange_min_qty_btc: float,
    ) -> None:
        """处理买单成交"""
        if self._mark_level_filled_callback:
            self._mark_level_filled_callback("buy", price)
        
        # 查找成交的支撑位 ID
        filled_support_lvl = self.position_manager._find_support_level_for_price(price)
        filled_support_level_id = filled_support_lvl.level_id if filled_support_lvl else None
        
        # 增量补卖单
        actions = self.position_manager.build_event_sell_increment(
            qty,
            exchange_min_qty_btc,
            current_state.close if current_state else 0,
            filled_support_level_id=filled_support_level_id,
        )
        if actions:
            self.logger.debug(
                f"⚡ Event买成补卖: price={price:.2f}, qty={qty:.6f}, "
                f"support_level_id={filled_support_level_id}"
            )
        await self._execute_actions(actions)
        
        # 通知
        if cost > 0 and self._notify_order_filled_callback:
            await self._notify_order_filled_callback(
                side="buy",
                fill_price=price,
                fill_amount=cost,
                grid_index=0,
                realized_pnl=0,
            )
        
        # 更新持仓清单
        self.position_manager.increment_fill_counter_by_order(order_id, price, qty)
        
        # 写入本地账本（包含 level_index）
        level_index = self.position_manager.get_level_index_by_level_id(filled_support_level_id)
        if level_index is None:
            level_index = self.position_manager.find_level_index_for_price(price)
        self.trade_store.append_trade({
            "timestamp": int(time.time()),
            "order_id": order_id,
            "trade_id": trade_id,
            "side": "buy",
            "price": price,
            "qty": qty,
            "cost": cost,
            "level_index": level_index
        })
        
        if self._mark_level_idle_callback:
            self._mark_level_idle_callback("buy", price)
    
    async def _handle_sell_fill(
        self,
        price: float,
        qty: float,
        cost: float,
        order_id: str,
        trade_id: str,
        current_state,
        exchange_min_qty_btc: float,
    ) -> None:
        """处理卖单成交"""
        if self._mark_level_filled_callback:
            self._mark_level_filled_callback("sell", price)
        
        self.logger.debug(f"⚡ Event卖成补买: price={price:.2f}")
        
        # 尝试挂回买单
        await self._handle_sell_rebuy(
            current_state.close if current_state else 0,
            exchange_min_qty_btc
        )
        
        # 通知
        if cost > 0 and self._notify_order_filled_callback:
            await self._notify_order_filled_callback(
                side="sell",
                fill_price=price,
                fill_amount=cost,
                grid_index=0,
                realized_pnl=0,
            )
        
        # 释放持仓记录
        self.position_manager.release_fill_counter_by_qty(qty)
        
        # 写入本地账本
        self.trade_store.append_trade({
            "timestamp": int(time.time()),
            "order_id": order_id,
            "trade_id": trade_id,
            "side": "sell",
            "price": price,
            "qty": qty,
            "cost": cost
        })
        
        if self._mark_level_idle_callback:
            self._mark_level_idle_callback("sell", price)
    
    async def _handle_sell_rebuy(
        self,
        current_price: float,
        exchange_min_qty: float
    ) -> None:
        """卖单成交后尝试挂回买单"""
        if not self.position_manager.state or current_price <= 0:
            return
        
        state = self.position_manager.state
        for lvl in state.support_levels_state:
            if lvl.price >= current_price:
                continue
            if (
                lvl.status == LevelStatus.IDLE
                and current_price > lvl.price * (1 + state.buy_price_buffer_pct)
            ):
                if lvl.fill_counter >= state.max_fill_per_level:
                    self.logger.debug(
                        "🧱 回补受限: price=%.2f, fill_counter=%d, max=%d",
                        lvl.price, lvl.fill_counter, state.max_fill_per_level,
                    )
                    continue
                qty = max(state.base_amount_per_grid, exchange_min_qty)
                await self._execute_actions([{
                    "action": "place",
                    "side": "buy",
                    "price": lvl.price,
                    "qty": qty,
                    "level_id": lvl.level_id,
                    "reason": "event_rebuy",
                }])
                self.logger.debug(
                    f"⚡ Event卖成补买: price={lvl.price:.2f}, qty={qty:.6f}"
                )
                break
    
    async def _execute_actions(self, actions: List[Dict[str, Any]]) -> None:
        """执行订单动作"""
        if not actions or not self.executor:
            return
        
        from key_level_grid.executor.base import Order, OrderSide, OrderType
        
        gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
        
        for action in actions:
            act = action.get("action")
            side = action.get("side", "buy")
            price = float(action.get("price", 0) or 0)
            qty = float(action.get("qty", 0) or 0)
            level_id = action.get("level_id", 0)
            reason = action.get("reason", "")
            order_id = action.get("order_id", "")
            
            try:
                if act == "place" and price > 0 and qty > 0:
                    # 转换为张数
                    contract_size = float(getattr(self.position_manager.state, "contract_size", 0) or 0)
                    if contract_size > 0:
                        import math
                        contracts = math.ceil(qty / contract_size)
                    else:
                        contracts = qty
                    
                    order = Order.create(
                        symbol=gate_symbol,
                        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                        order_type=OrderType.LIMIT,
                        quantity=contracts,
                        price=price,
                    )
                    if side == "sell":
                        order.reduce_only = True
                    order.metadata["level_id"] = level_id
                    order.metadata["reason"] = reason
                    order.metadata["order_type"] = f"Recon-{side.upper()}"
                    
                    success = await self.executor.submit_order(order)
                    if success:
                        self.logger.info(
                            f"✅ 挂单成功: {side.upper()} {contracts}张 @ {price:.2f}, "
                            f"level_id={level_id}, reason={reason}"
                        )
                    else:
                        self.logger.warning(
                            f"⚠️ 挂单失败: {side.upper()} {contracts}张 @ {price:.2f}, "
                            f"level_id={level_id}, reason={reason}"
                        )
                    
                    # 更新水位状态
                    if self.position_manager.state:
                        levels = (
                            self.position_manager.state.support_levels_state
                            if side == "buy"
                            else self.position_manager.state.resistance_levels_state
                        )
                        for lvl in levels:
                            if lvl.level_id == level_id:
                                if success:
                                    lvl.status = LevelStatus.ACTIVE
                                    # 从 Order 对象获取 exchange_order_id，而非从返回值
                                    lvl.order_id = order.exchange_order_id or ""
                                    lvl.active_order_id = lvl.order_id
                                    lvl.open_qty = qty
                                else:
                                    lvl.status = LevelStatus.IDLE
                                    lvl.last_error = "submit_failed"
                                lvl.last_action_ts = int(time.time())
                                break
                
                elif act == "cancel" and order_id:
                    # 创建 Order 对象用于取消
                    cancel_order = Order.create(
                        symbol=gate_symbol,
                        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                        order_type=OrderType.LIMIT,
                        quantity=0.0,
                        price=0.0,
                    )
                    cancel_order.exchange_order_id = order_id
                    cancel_order.metadata["reason"] = reason
                    cancel_order.metadata["side"] = side
                    cancel_order.metadata["price"] = price
                    
                    success = await self.executor.cancel_order(cancel_order)
                    if success:
                        self.logger.info(
                            f"🗑️ 撤单成功: {side.upper()} @ {price:.2f}, "
                            f"order_id={order_id}, reason={reason}"
                        )
                    else:
                        self.logger.warning(
                            f"⚠️ 撤单失败: {side.upper()} @ {price:.2f}, "
                            f"order_id={order_id}, reason={reason}"
                        )
                    
                    # 更新水位状态
                    if self.position_manager.state:
                        levels = (
                            self.position_manager.state.support_levels_state
                            if side == "buy"
                            else self.position_manager.state.resistance_levels_state
                        )
                        for lvl in levels:
                            if lvl.level_id == level_id:
                                lvl.status = LevelStatus.IDLE if success else LevelStatus.CANCELING
                                if success:
                                    lvl.order_id = ""
                                    lvl.active_order_id = ""
                                    lvl.open_qty = 0
                                lvl.last_action_ts = int(time.time())
                                break
            
            except Exception as e:
                self.logger.error(f"执行动作失败: {action}, 错误: {e}")
    
    async def reset_fill_counters(self, reason: str = "manual") -> bool:
        """重置持仓计数器"""
        if not self.position_manager.state:
            return False
        
        async with self._grid_lock:
            self.position_manager.clear_fill_counters(reason=reason)
            
            if self.notifier:
                await self.notifier.notify_quota_event(
                    symbol=self.config.symbol,
                    action="manual_reset",
                    detail=f"原因: {reason}",
                )
                await self.notifier.notify_system_info(
                    event="计数器手动重置",
                    result="已清空所有水位配额",
                )
        
        return True
