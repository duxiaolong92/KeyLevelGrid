"""
风控模块

负责止损单管理、止损触发检测
"""

import time
import uuid
from typing import Any, Dict, Optional

from key_level_grid.utils.logger import get_logger


class RiskManager:
    """
    风控管理器
    
    负责:
    1. 止损单的创建、更新、取消
    2. 止损触发检测和通知
    """
    
    def __init__(
        self,
        executor,
        config,
        position_manager,
        notifier=None,
        logger=None,
    ):
        """
        初始化风控管理器
        
        Args:
            executor: GateExecutor 实例
            config: 策略配置 (KeyLevelGridConfig)
            position_manager: GridPositionManager 实例
            notifier: NotificationManager 实例 (可选)
            logger: 日志实例 (可选)
        """
        self.executor = executor
        self.config = config
        self.position_manager = position_manager
        self.notifier = notifier
        self.logger = logger or get_logger(__name__)
        
        # 止损单状态
        self.stop_loss_order_id: Optional[str] = None
        self.stop_loss_contracts: float = 0
        self.stop_loss_trigger_price: float = 0
        self.sl_order_updated_at: float = 0
        self.sl_synced_from_exchange: bool = False
        self.sl_last_entry_price: float = 0
    
    def _convert_to_gate_symbol(self, binance_symbol: str) -> str:
        """将 Binance 符号转换为 Gate 格式"""
        symbol = binance_symbol.upper()
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT:USDT"
        return symbol
    
    async def check_and_update_stop_loss(
        self,
        gate_position: Dict[str, Any],
        contract_size: float,
    ) -> None:
        """
        检查并更新止损单
        
        Args:
            gate_position: Gate 持仓数据
            contract_size: 合约大小
        """
        if self.config.dry_run or not self.executor:
            self.logger.debug("止损单检查: dry_run 或无执行器，跳过")
            return
        
        if not self.position_manager.state:
            self.logger.debug("止损单检查: 无 position_manager.state，跳过")
            return
        
        # 获取当前持仓张数
        current_contracts = int(float(gate_position.get("raw_contracts", 0) or 0))
        
        # 获取网格底线（止损价）
        grid_floor = self.position_manager.state.grid_floor if self.position_manager.state else 0
        sl_cfg = getattr(self.position_manager, "stop_loss_config", None)
        if sl_cfg and getattr(sl_cfg, "trigger", "") == "fixed_pct":
            avg_entry = float(gate_position.get("entry_price", 0) or 0)
            fixed_pct = float(getattr(sl_cfg, "fixed_pct", 0) or 0)
            if avg_entry > 0 and fixed_pct > 0:
                grid_floor = avg_entry * (1 - fixed_pct)
        
        self.logger.debug(
            f"止损单检查: current_contracts={current_contracts}, grid_floor={grid_floor}, "
            f"sl_order_id={self.stop_loss_order_id}, sl_contracts={self.stop_loss_contracts}"
        )
        
        if grid_floor <= 0:
            self.logger.warning(f"⚠️ 网格底线无效 (grid_floor={grid_floor})，跳过止损单更新")
            return
        
        # 情况1: 无持仓，但有止损单 → 取消止损单
        if current_contracts == 0 and self.stop_loss_order_id:
            self.logger.info("📭 持仓已清空，取消止损单")
            await self._cancel_stop_loss_order()
            return
        
        # 情况2: 无持仓，无止损单 → 无需操作
        if current_contracts == 0:
            return
        
        # 若本地无止损单信息，先尝试从交易所同步
        if not self.stop_loss_order_id or self.stop_loss_order_id == "pending":
            await self._sync_stop_loss_from_exchange()
            if self.stop_loss_order_id and self.stop_loss_contracts == current_contracts:
                if grid_floor > 0 and self.stop_loss_trigger_price > 0:
                    diff = abs(self.stop_loss_trigger_price - grid_floor) / grid_floor
                    if diff < 0.001:
                        self.logger.debug(
                            "止损单已存在且触发价一致，跳过更新: %s",
                            self.stop_loss_order_id,
                        )
                        return

        # 情况3: 有持仓，持仓张数未变化且已有止损单 → 无需更新
        if current_contracts == self.stop_loss_contracts and self.stop_loss_order_id:
            self.logger.debug(f"止损单无需更新: {current_contracts}张 @ {grid_floor:.2f}")
            return
        
        # 防止短时间内重复提交（30秒冷却）
        if self.sl_order_updated_at > 0 and (time.time() - self.sl_order_updated_at) < 30:
            self.logger.debug("止损单冷却中，跳过本次更新")
            return
        
        # 情况4: 有持仓，持仓变化或无止损单 → 创建/更新止损单
        self.logger.info(
            f"🛡️ 准备更新止损单: {self.stop_loss_contracts}张 → {current_contracts}张 @ {grid_floor:.2f}"
        )
        
        # 先取消旧止损单
        old_order_id = self.stop_loss_order_id
        if old_order_id:
            self.logger.info(f"🔄 取消旧止损单: ID={old_order_id}")
            await self._cancel_stop_loss_order_on_exchange(old_order_id)
        
        # 提交新止损单
        self.logger.info(f"📤 开始提交新止损单: {current_contracts}张 @ {grid_floor:.2f}")
        success = await self._submit_stop_loss_order(
            current_contracts, grid_floor, gate_position, contract_size
        )
        if not success:
            self.logger.error("❌ 止损单提交失败，30秒后重试")
    
    async def _submit_stop_loss_order(
        self,
        contracts: int,
        trigger_price: float,
        gate_position: Dict[str, Any],
        contract_size: float,
    ) -> bool:
        """提交止损单"""
        from key_level_grid.executor.base import Order, OrderSide, OrderType
        
        if contracts <= 0 or trigger_price <= 0:
            return False
        
        gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
        
        try:
            sl_order = Order(
                order_id=f"sl_{uuid.uuid4().hex[:8]}",
                symbol=gate_symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=contracts,
                price=0,
                reduce_only=True,
            )
            
            sl_order.metadata['order_mode'] = 'trigger'
            sl_order.metadata['triggerPrice'] = trigger_price
            sl_order.metadata['rule'] = 2  # <= (价格跌破触发)
            sl_order.metadata['is_stop_loss'] = True
            sl_order.metadata['reason'] = "stop_loss"
            sl_order.metadata['order_type'] = "止损单"
            sl_order.metadata['side'] = "sell"
            sl_order.metadata['price'] = trigger_price
            sl_order.metadata['qty_btc'] = contracts * contract_size
            
            self.logger.info(
                f"📤 提交止损单: {contracts}张, 触发价={trigger_price:.2f}, "
                f"symbol={gate_symbol}"
            )
            
            success = await self.executor.submit_order(sl_order)
            
            if success:
                order_id = getattr(sl_order, 'exchange_order_id', None) or sl_order.metadata.get('order_id', '')
                self.stop_loss_order_id = str(order_id) if order_id else "pending"
                self.stop_loss_contracts = contracts
                self.stop_loss_trigger_price = trigger_price
                self.sl_order_updated_at = time.time()
                self.sl_last_entry_price = float(gate_position.get('entry_price', 0) or 0)
                self.logger.info(f"✅ 止损单提交成功: ID={self.stop_loss_order_id}")
                return True
            else:
                self.logger.error(f"❌ 止损单提交失败: {sl_order.reject_reason}")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ 提交止损单异常: {e}", exc_info=True)
            return False
    
    async def _cancel_stop_loss_order_on_exchange(self, order_id: str) -> bool:
        """仅取消交易所的止损单，不清空本地状态"""
        if not order_id or order_id == "pending":
            return True
        
        gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
        
        try:
            if hasattr(self.executor, 'cancel_plan_order'):
                success = await self.executor.cancel_plan_order(gate_symbol, order_id)
            else:
                success = await self.executor.cancel_order(gate_symbol, order_id)
            
            if success:
                self.logger.info(f"✅ 止损单已取消: ID={order_id}")
            else:
                self.logger.warning(f"⚠️ 取消止损单失败: ID={order_id}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"❌ 取消止损单异常: {e}")
            return False
    
    async def _cancel_stop_loss_order(self) -> bool:
        """取消当前止损单并清空本地状态"""
        if not self.stop_loss_order_id:
            return True
        
        success = await self._cancel_stop_loss_order_on_exchange(self.stop_loss_order_id)
        
        self.stop_loss_order_id = None
        self.stop_loss_contracts = 0
        
        return success
    
    async def _sync_stop_loss_from_exchange(self) -> None:
        """从交易所同步现有止损单"""
        if self.config.dry_run or not self.executor:
            return
        
        try:
            symbol = self._convert_to_gate_symbol(self.config.symbol)
            plan_orders = await self.executor.get_plan_orders(symbol, status='open')
            
            if not plan_orders:
                self.logger.info("📊 启动同步: 交易所无现有止损单")
                return
            
            self.logger.debug(f"📊 获取到 {len(plan_orders)} 个计划委托")
            
            for order in plan_orders:
                order_id = str(order.get('id', ''))
                # Gate API 返回的 size 可能在 initial 字段中
                initial = order.get('initial', {})
                size_raw = order.get('size', 0) or initial.get('size', 0)
                size = abs(int(size_raw or 0))
                is_sell = int(size_raw or 0) < 0
                
                # trigger 信息
                trigger_info = order.get('trigger', {})
                trigger_price = float(trigger_info.get('price', 0) if isinstance(trigger_info, dict) else 0)
                
                self.logger.debug(
                    f"📊 检查订单: id={order_id}, size_raw={size_raw}, "
                    f"is_sell={is_sell}, trigger_price={trigger_price}"
                )
                
                if is_sell and size > 0:
                    self.stop_loss_order_id = order_id
                    self.stop_loss_contracts = size
                    self.stop_loss_trigger_price = trigger_price
                    self.logger.info(
                        f"✅ 启动同步: 找到现有止损单 ID={order_id}, "
                        f"数量={size}张, 触发价=${trigger_price:,.2f}"
                    )
                    return
            
            # 未找到符合条件的止损单，但有其他计划委托
            # 可能是格式不匹配或旧版止损单，先清理掉
            self.logger.warning(
                f"⚠️ 启动同步: 未找到符合条件的止损单 (共 {len(plan_orders)} 个订单)，清理残留"
            )
            await self._cleanup_orphan_stop_loss_orders(symbol)
            
        except Exception as e:
            self.logger.error(f"❌ 同步止损单失败: {e}", exc_info=True)
    
    async def _cleanup_orphan_stop_loss_orders(self, symbol: str) -> None:
        """清理孤立的止损单（重启时使用）"""
        if not self.executor:
            return
        
        try:
            if hasattr(self.executor, 'cancel_all_plan_orders'):
                success = await self.executor.cancel_all_plan_orders(symbol)
                if success:
                    self.logger.info("🧹 已清理所有残留计划委托")
                else:
                    self.logger.warning("⚠️ 清理残留计划委托失败")
        except Exception as e:
            self.logger.error(f"❌ 清理残留计划委托异常: {e}")
    
    async def check_stop_loss_triggered(
        self,
        gate_position: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        检测止损单是否被触发执行
        
        Returns:
            触发信息字典（如果触发），否则返回 None
        """
        if self.config.dry_run or not self.executor:
            return None
        
        if not self.stop_loss_order_id or self.stop_loss_contracts == 0:
            return None
        
        try:
            symbol = self._convert_to_gate_symbol(self.config.symbol)
            plan_orders = await self.executor.get_plan_orders(symbol, status='finished')
            
            for order in plan_orders:
                order_id = str(order.get('id', ''))
                if order_id == self.stop_loss_order_id:
                    status = order.get('status', '')
                    finish_as = order.get('finish_as', '')
                    
                    if finish_as == 'succeeded' or status == 'finished':
                        trigger_info_data = order.get('trigger', {})
                        trigger_price = float(trigger_info_data.get('price', 0) if isinstance(trigger_info_data, dict) else 0)
                        contracts = abs(int(order.get('size', 0)))
                        contract_size = float(gate_position.get('contract_size', 0.0001) or 0.0001)
                        
                        entry_price = self.sl_last_entry_price or float(gate_position.get('entry_price', 0) or 0)
                        
                        triggered_info = None
                        if entry_price > 0 and trigger_price > 0:
                            loss_usdt = (entry_price - trigger_price) * contracts * contract_size
                            loss_pct = (trigger_price - entry_price) / entry_price * 100
                            
                            triggered_info = {
                                "trigger_price": trigger_price,
                                "fill_contracts": contracts,
                                "loss_usdt": abs(loss_usdt),
                                "loss_pct": abs(loss_pct),
                                "entry_price": entry_price,
                            }
                            
                            self.logger.warning(
                                f"🛑 止损触发: {contracts}张 @ ${trigger_price:,.2f}, "
                                f"亏损 ${abs(loss_usdt):,.2f} ({abs(loss_pct):.2f}%)"
                            )
                        
                        # 清空本地止损单状态
                        self.stop_loss_order_id = None
                        self.stop_loss_contracts = 0
                        self.sl_last_entry_price = 0
                        return triggered_info
            
            return None
                        
        except Exception as e:
            self.logger.error(f"❌ 检测止损触发失败: {e}", exc_info=True)
            return None
    
