"""
原子性重构执行器 (LEVEL_GENERATION.md v3.1.0)

核心职责:
1. 确保"先撤单、后挂单"的原子性
2. 撤单失败时绝对不挂新单
3. 挂单失败时进入 ALARM 模式
4. 本地状态更新在交易所确认后进行

关键原则:
- 撤单失败 → 不挂新单，保持原状
- 撤单成功 + 挂单失败 → ALARM 模式，人工介入
- 全部成功 → 更新本地状态
"""

import time
import json
import logging
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from pathlib import Path

from key_level_grid.core.triggers import (
    PendingMigration,
    RebuildPhase,
    RebuildTrigger,
    RebuildLog,
)
from key_level_grid.strategy.grid.level_lifecycle import (
    InheritanceResult,
    OrderRequest,
)


logger = logging.getLogger(__name__)


@dataclass
class AtomicRebuildResult:
    """原子性重构结果"""
    success: bool                      # 是否完全成功
    phase: RebuildPhase                # 最终阶段
    orders_cancelled: List[str] = field(default_factory=list)  # 成功撤销的订单
    orders_placed: List[str] = field(default_factory=list)      # 成功挂单的订单 ID
    failed_cancels: List[str] = field(default_factory=list)     # 撤单失败的订单
    failed_places: List[Dict] = field(default_factory=list)     # 挂单失败的订单
    error_message: Optional[str] = None
    needs_alarm: bool = False          # 是否需要告警


class AtomicRebuildExecutor:
    """
    原子性重构执行器
    
    执行流程:
    1. 持久化迁移计划 (崩溃恢复用)
    2. 执行撤单 (全部成功才继续)
    3. 执行挂单
    4. 更新本地状态
    5. 清理迁移计划
    """
    
    def __init__(
        self,
        executor,  # 交易所执行器
        state_dir: Optional[str] = None,
        max_retries: int = 3,
        retry_delay_sec: float = 1.0,
        config: Optional[Dict] = None,
    ):
        """
        初始化原子性重构执行器
        
        Args:
            executor: 交易所执行器 (支持 cancel_order, place_order)
            state_dir: 状态目录 (用于持久化迁移计划)
            max_retries: 最大重试次数
            retry_delay_sec: 重试延迟 (秒)
            config: 配置字典
        """
        self.executor = executor
        self.state_dir = Path(state_dir) if state_dir else Path("state")
        self.max_retries = max_retries
        self.retry_delay_sec = retry_delay_sec
        self.config = config or {}
        
        # 迁移计划文件
        self._migration_file = self.state_dir / "pending_migration.json"
        
        # 当前迁移状态
        self._pending: Optional[PendingMigration] = None
        
        # 告警回调
        self._alarm_callback: Optional[callable] = None
    
    def set_alarm_callback(self, callback: callable) -> None:
        """
        设置告警回调
        
        Args:
            callback: 告警回调函数 (message: str) -> None
        """
        self._alarm_callback = callback
    
    async def execute(
        self,
        inheritance_result: InheritanceResult,
        symbol: str,
    ) -> AtomicRebuildResult:
        """
        执行原子性重构
        
        Args:
            inheritance_result: 继承结果 (包含待撤/待挂订单)
            symbol: 交易对
        
        Returns:
            AtomicRebuildResult
        """
        # 1. 创建迁移计划
        self._pending = PendingMigration(
            phase=RebuildPhase.PENDING,
            started_at=int(time.time()),
            orders_to_cancel=inheritance_result.orders_to_cancel,
            orders_to_place=[self._order_request_to_dict(o) for o in inheritance_result.orders_to_place],
        )
        
        # 2. 持久化迁移计划 (崩溃恢复用)
        self._save_migration()
        
        result = AtomicRebuildResult(
            success=False,
            phase=RebuildPhase.PENDING,
        )
        
        try:
            # 3. 执行撤单阶段
            cancel_success = await self._execute_cancels(symbol, result)
            
            if not cancel_success:
                # 撤单失败，不继续挂单
                result.phase = RebuildPhase.ALARM
                result.needs_alarm = True
                result.error_message = "Cancel phase failed, aborting rebuild"
                await self._trigger_alarm(result)
                return result
            
            # 4. 执行挂单阶段
            place_success = await self._execute_places(symbol, result)
            
            if not place_success:
                # 撤单成功但挂单失败 → ALARM 模式
                result.phase = RebuildPhase.ALARM
                result.needs_alarm = True
                result.error_message = "Place phase failed after successful cancels"
                await self._trigger_alarm(result)
                return result
            
            # 5. 全部成功
            result.success = True
            result.phase = RebuildPhase.COMPLETED
            self._pending.phase = RebuildPhase.COMPLETED
            
        except Exception as e:
            logger.error(f"Atomic rebuild failed: {e}")
            result.phase = RebuildPhase.ALARM
            result.needs_alarm = True
            result.error_message = str(e)
            await self._trigger_alarm(result)
            
        finally:
            # 6. 清理迁移计划 (成功时)
            if result.success:
                self._clear_migration()
            else:
                # 失败时保留迁移计划，供恢复使用
                self._save_migration()
        
        return result
    
    async def _execute_cancels(
        self,
        symbol: str,
        result: AtomicRebuildResult,
    ) -> bool:
        """
        执行撤单阶段
        
        Returns:
            True if all cancels succeeded
        """
        self._pending.phase = RebuildPhase.CANCELLING
        self._save_migration()
        
        orders_to_cancel = self._pending.orders_to_cancel
        
        if not orders_to_cancel:
            logger.debug("No orders to cancel")
            return True
        
        logger.info(f"Cancelling {len(orders_to_cancel)} orders...")
        
        for order_id in orders_to_cancel:
            success = await self._cancel_order_with_retry(symbol, order_id)
            
            if success:
                result.orders_cancelled.append(order_id)
                self._pending.orders_cancelled.append(order_id)
            else:
                result.failed_cancels.append(order_id)
                logger.error(f"Failed to cancel order {order_id}")
        
        # 更新迁移计划
        self._save_migration()
        
        # 全部成功才继续
        return len(result.failed_cancels) == 0
    
    async def _execute_places(
        self,
        symbol: str,
        result: AtomicRebuildResult,
    ) -> bool:
        """
        执行挂单阶段
        
        Returns:
            True if all places succeeded
        """
        self._pending.phase = RebuildPhase.PLACING
        self._save_migration()
        
        orders_to_place = self._pending.orders_to_place
        
        if not orders_to_place:
            logger.debug("No orders to place")
            return True
        
        logger.info(f"Placing {len(orders_to_place)} orders...")
        
        for order_dict in orders_to_place:
            new_order_id, success = await self._place_order_with_retry(
                symbol,
                order_dict["price"],
                order_dict["qty"],
                order_dict["side"],
            )
            
            if success and new_order_id:
                result.orders_placed.append(new_order_id)
                self._pending.orders_placed.append(new_order_id)
            else:
                result.failed_places.append(order_dict)
                self._pending.failed_orders.append(order_dict)
                logger.error(f"Failed to place order at price {order_dict['price']}")
        
        # 更新迁移计划
        self._save_migration()
        
        # 全部成功才算成功
        return len(result.failed_places) == 0
    
    async def _cancel_order_with_retry(
        self,
        symbol: str,
        order_id: str,
    ) -> bool:
        """
        带重试的撤单
        
        Returns:
            True if cancelled
        """
        for attempt in range(self.max_retries):
            try:
                await self.executor.cancel_order(symbol, order_id)
                logger.debug(f"Cancelled order {order_id}")
                return True
            except Exception as e:
                logger.warning(f"Cancel attempt {attempt + 1} failed for {order_id}: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay_sec)
        
        return False
    
    async def _place_order_with_retry(
        self,
        symbol: str,
        price: float,
        qty: float,
        side: str,
    ) -> Tuple[Optional[str], bool]:
        """
        带重试的挂单
        
        Returns:
            (order_id, success)
        """
        for attempt in range(self.max_retries):
            try:
                order_id = await self.executor.place_limit_order(
                    symbol=symbol,
                    side=side,
                    price=price,
                    amount=qty,
                )
                logger.debug(f"Placed order {order_id} at {price}")
                return order_id, True
            except Exception as e:
                logger.warning(f"Place attempt {attempt + 1} failed at {price}: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay_sec)
        
        return None, False
    
    async def _trigger_alarm(self, result: AtomicRebuildResult) -> None:
        """
        触发告警
        
        Args:
            result: 重构结果
        """
        message = f"""
🚨 **网格重构告警 (ALARM)**

**阶段**: {result.phase.value}
**错误**: {result.error_message or 'Unknown'}

**已撤订单**: {len(result.orders_cancelled)}
**撤单失败**: {len(result.failed_cancels)}
**已挂订单**: {len(result.orders_placed)}
**挂单失败**: {len(result.failed_places)}

⚠️ 系统进入告警模式，需要人工检查！
"""
        
        logger.critical(message)
        
        if self._alarm_callback:
            try:
                self._alarm_callback(message)
            except Exception as e:
                logger.error(f"Alarm callback failed: {e}")
    
    def _save_migration(self) -> None:
        """持久化迁移计划"""
        if not self._pending:
            return
        
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with open(self._migration_file, "w") as f:
                json.dump(self._pending.to_dict(), f, indent=2)
            logger.debug(f"Saved migration plan to {self._migration_file}")
        except Exception as e:
            logger.error(f"Failed to save migration plan: {e}")
    
    def _clear_migration(self) -> None:
        """清理迁移计划"""
        try:
            if self._migration_file.exists():
                self._migration_file.unlink()
            self._pending = None
            logger.debug("Cleared migration plan")
        except Exception as e:
            logger.error(f"Failed to clear migration plan: {e}")
    
    def load_pending_migration(self) -> Optional[PendingMigration]:
        """
        加载未完成的迁移计划 (崩溃恢复用)
        
        Returns:
            PendingMigration or None
        """
        try:
            if not self._migration_file.exists():
                return None
            
            with open(self._migration_file) as f:
                data = json.load(f)
            
            self._pending = PendingMigration.from_dict(data)
            
            if self._pending.is_incomplete():
                logger.warning(f"Found incomplete migration at phase {self._pending.phase.value}")
                return self._pending
            
            return None
        except Exception as e:
            logger.error(f"Failed to load migration plan: {e}")
            return None
    
    async def resume_migration(self, symbol: str) -> Optional[AtomicRebuildResult]:
        """
        恢复未完成的迁移
        
        Args:
            symbol: 交易对
        
        Returns:
            AtomicRebuildResult or None
        """
        pending = self.load_pending_migration()
        if not pending:
            return None
        
        logger.warning(f"Resuming migration from phase {pending.phase.value}")
        
        result = AtomicRebuildResult(
            success=False,
            phase=pending.phase,
            orders_cancelled=pending.orders_cancelled.copy(),
            orders_placed=pending.orders_placed.copy(),
        )
        
        try:
            # 根据阶段恢复
            if pending.phase == RebuildPhase.CANCELLING:
                # 继续撤单
                remaining = [
                    oid for oid in pending.orders_to_cancel 
                    if oid not in pending.orders_cancelled
                ]
                self._pending.orders_to_cancel = remaining
                
                cancel_success = await self._execute_cancels(symbol, result)
                if not cancel_success:
                    result.phase = RebuildPhase.ALARM
                    result.needs_alarm = True
                    await self._trigger_alarm(result)
                    return result
                
                # 继续挂单
                place_success = await self._execute_places(symbol, result)
                if not place_success:
                    result.phase = RebuildPhase.ALARM
                    result.needs_alarm = True
                    await self._trigger_alarm(result)
                    return result
                
            elif pending.phase == RebuildPhase.PLACING:
                # 继续挂单
                remaining = [
                    o for o in pending.orders_to_place
                    if o not in pending.failed_orders
                ]
                self._pending.orders_to_place = remaining
                
                place_success = await self._execute_places(symbol, result)
                if not place_success:
                    result.phase = RebuildPhase.ALARM
                    result.needs_alarm = True
                    await self._trigger_alarm(result)
                    return result
            
            elif pending.phase == RebuildPhase.ALARM:
                # 已经在告警模式，需要人工处理
                result.needs_alarm = True
                result.error_message = "Migration stuck in ALARM phase, manual intervention required"
                return result
            
            # 成功完成
            result.success = True
            result.phase = RebuildPhase.COMPLETED
            self._clear_migration()
            
        except Exception as e:
            logger.error(f"Resume migration failed: {e}")
            result.phase = RebuildPhase.ALARM
            result.needs_alarm = True
            result.error_message = str(e)
        
        return result
    
    @staticmethod
    def _order_request_to_dict(order: OrderRequest) -> Dict:
        """将 OrderRequest 转换为字典"""
        return {
            "price": order.price,
            "qty": order.qty,
            "side": order.side,
            "level_id": order.level_id,
        }
