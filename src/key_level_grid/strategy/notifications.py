"""
通知助手模块

封装 Telegram 通知逻辑，降低 strategy.py 复杂度
"""

import time
from typing import Any, Dict, List, Optional

from key_level_grid.utils.logger import get_logger


class NotificationHelper:
    """通知助手类"""
    
    def __init__(
        self,
        notifier,
        config,
        position_manager,
        get_display_data_func,
    ):
        """
        初始化通知助手
        
        Args:
            notifier: NotificationManager 实例
            config: 策略配置 (KeyLevelGridConfig)
            position_manager: GridPositionManager 实例
            get_display_data_func: 获取展示数据的函数
        """
        self.notifier = notifier
        self.config = config
        self.position_manager = position_manager
        self.get_display_data = get_display_data_func
        self.logger = get_logger(__name__)
        
        # Telegram Bot 健康检查
        self._tg_bot = None
        self._tg_bot_checked_at: float = 0
    
    def set_tg_bot(self, tg_bot):
        """设置 Telegram Bot 实例"""
        self._tg_bot = tg_bot
    
    async def send_startup_notification(
        self,
        gate_position: Dict[str, Any] = None,
    ) -> None:
        """发送启动通知"""
        if not self.notifier:
            return
        
        try:
            data = self.get_display_data()
            
            price_obj = data.get("price", {})
            current_price = price_obj.get("current", 0) if isinstance(price_obj, dict) else 0
            
            account_data = data.get("account", {})
            account = {
                "total_balance": account_data.get("total_balance", 0),
                "available": account_data.get("available", 0),
                "frozen": account_data.get("frozen", 0),
            }
            
            pos_data = data.get("position", {})
            position = {
                "value": pos_data.get("value", pos_data.get("notional", 0)),
                "avg_price": pos_data.get("avg_entry_price", pos_data.get("avg_price", 0)),
                "unrealized_pnl": pos_data.get("unrealized_pnl", 0),
                "pnl_pct": 0,
            }
            if position["value"] > 0 and position["unrealized_pnl"] != 0:
                position["pnl_pct"] = position["unrealized_pnl"] / position["value"]
            
            pending_orders = data.get("pending_orders", [])
            orders = [
                {
                    "side": o.get("side", ""),
                    "price": o.get("price", 0),
                    "amount": o.get("amount", 0),
                }
                for o in pending_orders
            ]
            
            grid_cfg = account_data.get("grid_config", {})
            grid_config = {
                "max_position": grid_cfg.get("max_position", 0),
                "leverage": self.config.leverage,
                "num_grids": self.position_manager.grid_config.max_grids,
                "grid_min": self.position_manager.grid_config.manual_lower if self.position_manager.grid_config.range_mode == "manual" else 0,
                "grid_max": self.position_manager.grid_config.manual_upper if self.position_manager.grid_config.range_mode == "manual" else 0,
                "grid_floor": grid_cfg.get("grid_floor", 0),
            }
            sl_cfg = getattr(self.position_manager, "stop_loss_config", None)
            if sl_cfg:
                grid_config["sl_pct"] = float(getattr(sl_cfg, "fixed_pct", 0) or 0) * 100
            
            resistance_levels = data.get("resistance_levels", [])
            support_levels = data.get("support_levels", [])
            
            await self.notifier.notify_startup(
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
    
    async def send_shutdown_notification(
        self,
        reason: str = "手动停止",
        gate_position: Dict[str, Any] = None,
    ) -> None:
        """发送停止通知"""
        if not self.notifier:
            return
        
        try:
            position = None
            if gate_position and gate_position.get("contracts", 0) > 0:
                position = {
                    "value": gate_position.get("notional", 0),
                }
            
            await self.notifier.notify_shutdown(
                reason=reason,
                position=position,
                total_pnl=self.notifier._stats.get("realized_pnl", 0) if self.notifier else 0,
            )
        except Exception as e:
            self.logger.error(f"发送停止通知失败: {e}")
    
    async def notify_order_filled(
        self,
        side: str,
        fill_price: float,
        fill_amount: float,
        grid_index: int = 0,
        total_grids: int = 0,
        realized_pnl: float = 0,
        gate_position: Dict[str, Any] = None,
    ) -> None:
        """发送成交通知"""
        if not self.notifier:
            return
        
        try:
            position_after = None
            if gate_position and gate_position.get("contracts", 0) > 0:
                value = gate_position.get("notional", 0)
                unrealized_pnl = gate_position.get("unrealized_pnl", 0)
                position_after = {
                    "value": value,
                    "avg_price": gate_position.get("entry_price", 0),
                    "unrealized_pnl": unrealized_pnl,
                    "pnl_pct": unrealized_pnl / value if value > 0 else 0,
                }
            
            await self.notifier.notify_order_filled(
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
    
    async def notify_grid_rebuild(
        self,
        reason: str,
        old_anchor: float,
        new_anchor: float,
        new_orders: list,
    ) -> None:
        """发送网格重建通知"""
        if not self.notifier:
            return
        
        try:
            orders = [
                {
                    "side": o.get("side", "buy"),
                    "price": o.get("price", 0),
                    "amount": o.get("amount", 0),
                }
                for o in new_orders
            ]
            
            await self.notifier.notify_grid_rebuild(
                symbol=self.config.symbol,
                reason=reason,
                old_anchor=old_anchor,
                new_anchor=new_anchor,
                new_orders=orders,
            )
        except Exception as e:
            self.logger.error(f"发送网格重建通知失败: {e}")
    
    async def check_telegram_bot(self) -> None:
        """定期检查 Telegram Bot 状态"""
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
                return

            last_ts = self._tg_bot.get_last_update_ts()
            if last_ts and (time.time() - last_ts) > 600:
                self.logger.warning("⚠️ Telegram Bot 超过 10 分钟无指令，尝试重启")
                await self._tg_bot.restart()
                self.logger.info("✅ Telegram Bot 重启完成")
        except Exception as e:
            self.logger.error(f"Telegram Bot 重连失败: {e}")
    
    async def notify_error(
        self,
        error_type: str,
        error_msg: str,
        context: str = "",
        suggestion: str = "",
    ) -> None:
        """发送错误通知"""
        if not self.notifier:
            return
        
        try:
            await self.notifier.notify_error(
                error_type=error_type,
                error_msg=error_msg,
                context=context,
                suggestion=suggestion,
            )
        except Exception as e:
            self.logger.error(f"发送错误通知失败: {e}")

    async def notify_alert(
        self,
        *,
        error_type: str,
        error_msg: str,
        impact: str,
        error_code: str = "",
        suggestion: str = "",
        traceback_text: str = "",
    ) -> None:
        """发送告警通知"""
        if not self.notifier:
            return
        try:
            await self.notifier.notify_system_alert(
                error_type=error_type,
                error_code=error_code,
                error_msg=error_msg,
                impact=impact,
                suggestion=suggestion,
                traceback_text=traceback_text[:600],
            )
        except Exception as e:
            self.logger.error(f"发送告警通知失败: {e}")
    
    async def notify_stop_loss_triggered(
        self,
        trigger_price: float,
        loss_usdt: float,
        loss_pct: float,
        fill_contracts: float,
        entry_price: float,
        gate_position: Dict[str, Any] = None,
    ) -> None:
        """发送止损触发通知"""
        if not self.notifier:
            return
        
        try:
            remaining_value = 0
            if gate_position:
                remaining_value = gate_position.get("notional", 0)
            
            await self.notifier.notify_stop_loss(
                symbol=self.config.symbol,
                trigger_price=trigger_price,
                loss_usdt=loss_usdt,
                loss_pct=loss_pct,
                fill_contracts=fill_contracts,
                entry_price=entry_price,
                remaining_value=remaining_value,
            )
        except Exception as e:
            self.logger.error(f"发送止损通知失败: {e}")
