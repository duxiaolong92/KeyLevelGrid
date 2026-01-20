"""
V3.0 AtomicRebuildExecutor 单元测试

测试覆盖:
1. 原子性执行流程
2. 撤单失败处理
3. 挂单失败处理
4. ALARM 模式
5. 崩溃恢复
"""

import pytest
import sys
import json
import tempfile
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from key_level_grid.strategy.grid.atomic_rebuild import (
    AtomicRebuildExecutor,
    AtomicRebuildResult,
)
from key_level_grid.strategy.grid.level_lifecycle import (
    InheritanceResult,
    OrderRequest,
)
from key_level_grid.core.triggers import (
    RebuildPhase,
    PendingMigration,
)
from key_level_grid.core.state import GridLevelState
from key_level_grid.core.types import LevelLifecycleStatus


# ============================================
# Mock 执行器
# ============================================

class MockExecutor:
    """模拟交易所执行器"""
    
    def __init__(self):
        self.cancelled_orders = []
        self.placed_orders = []
        self.cancel_fail_ids = set()
        self.place_fail_prices = set()
        self._order_id_counter = 1000
    
    async def cancel_order(self, symbol: str, order_id: str):
        """模拟撤单"""
        if order_id in self.cancel_fail_ids:
            raise Exception(f"Failed to cancel order {order_id}")
        self.cancelled_orders.append(order_id)
    
    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        price: float,
        amount: float,
    ) -> str:
        """模拟挂单"""
        if price in self.place_fail_prices:
            raise Exception(f"Failed to place order at {price}")
        
        self._order_id_counter += 1
        order_id = f"order_{self._order_id_counter}"
        self.placed_orders.append({
            "id": order_id,
            "price": price,
            "amount": amount,
            "side": side,
        })
        return order_id


# ============================================
# 测试: 基础功能
# ============================================

class TestAtomicRebuildBasic:
    """测试基础功能"""
    
    def test_init(self):
        """测试初始化"""
        executor = MockExecutor()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            rebuilder = AtomicRebuildExecutor(
                executor=executor,
                state_dir=tmpdir,
                max_retries=3,
            )
            
            assert rebuilder.max_retries == 3
            assert rebuilder.state_dir == Path(tmpdir)
    
    def test_order_request_to_dict(self):
        """测试 OrderRequest 转字典"""
        order = OrderRequest(
            price=95000.0,
            qty=100,
            side="buy",
            level_id=12345,
        )
        
        result = AtomicRebuildExecutor._order_request_to_dict(order)
        
        assert result["price"] == 95000.0
        assert result["qty"] == 100
        assert result["side"] == "buy"
        assert result["level_id"] == 12345


# ============================================
# 测试: 成功执行
# ============================================

class TestAtomicRebuildSuccess:
    """测试成功执行流程"""
    
    @pytest.mark.asyncio
    async def test_execute_no_orders(self):
        """无订单时直接成功"""
        executor = MockExecutor()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            rebuilder = AtomicRebuildExecutor(
                executor=executor,
                state_dir=tmpdir,
            )
            
            inheritance = InheritanceResult(
                active_levels=[],
                retired_levels=[],
                orders_to_cancel=[],
                orders_to_place=[],
            )
            
            result = await rebuilder.execute(inheritance, "BTCUSDT")
            
            assert result.success is True
            assert result.phase == RebuildPhase.COMPLETED
            assert len(result.orders_cancelled) == 0
            assert len(result.orders_placed) == 0
    
    @pytest.mark.asyncio
    async def test_execute_cancel_and_place(self):
        """撤单并挂单"""
        executor = MockExecutor()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            rebuilder = AtomicRebuildExecutor(
                executor=executor,
                state_dir=tmpdir,
            )
            
            inheritance = InheritanceResult(
                active_levels=[],
                retired_levels=[],
                orders_to_cancel=["old_order_1", "old_order_2"],
                orders_to_place=[
                    OrderRequest(price=95000.0, qty=100, side="buy", level_id=1),
                    OrderRequest(price=94000.0, qty=100, side="buy", level_id=2),
                ],
            )
            
            result = await rebuilder.execute(inheritance, "BTCUSDT")
            
            assert result.success is True
            assert result.phase == RebuildPhase.COMPLETED
            assert len(result.orders_cancelled) == 2
            assert len(result.orders_placed) == 2
            assert "old_order_1" in result.orders_cancelled
            assert "old_order_2" in result.orders_cancelled


# ============================================
# 测试: 撤单失败
# ============================================

class TestAtomicRebuildCancelFail:
    """测试撤单失败处理"""
    
    @pytest.mark.asyncio
    async def test_cancel_fail_no_place(self):
        """撤单失败时不挂新单"""
        executor = MockExecutor()
        executor.cancel_fail_ids.add("fail_order")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            rebuilder = AtomicRebuildExecutor(
                executor=executor,
                state_dir=tmpdir,
                max_retries=1,  # 只重试一次
            )
            
            inheritance = InheritanceResult(
                active_levels=[],
                retired_levels=[],
                orders_to_cancel=["fail_order"],
                orders_to_place=[
                    OrderRequest(price=95000.0, qty=100, side="buy", level_id=1),
                ],
            )
            
            result = await rebuilder.execute(inheritance, "BTCUSDT")
            
            # 撤单失败，进入 ALARM 模式
            assert result.success is False
            assert result.phase == RebuildPhase.ALARM
            assert result.needs_alarm is True
            
            # 不应该挂新单
            assert len(result.orders_placed) == 0
            assert len(executor.placed_orders) == 0


# ============================================
# 测试: 挂单失败
# ============================================

class TestAtomicRebuildPlaceFail:
    """测试挂单失败处理"""
    
    @pytest.mark.asyncio
    async def test_place_fail_alarm(self):
        """挂单失败进入 ALARM 模式"""
        executor = MockExecutor()
        executor.place_fail_prices.add(95000.0)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            rebuilder = AtomicRebuildExecutor(
                executor=executor,
                state_dir=tmpdir,
                max_retries=1,
            )
            
            inheritance = InheritanceResult(
                active_levels=[],
                retired_levels=[],
                orders_to_cancel=["old_order"],
                orders_to_place=[
                    OrderRequest(price=95000.0, qty=100, side="buy", level_id=1),  # 会失败
                ],
            )
            
            result = await rebuilder.execute(inheritance, "BTCUSDT")
            
            # 撤单成功但挂单失败
            assert result.success is False
            assert result.phase == RebuildPhase.ALARM
            assert result.needs_alarm is True
            assert len(result.orders_cancelled) == 1
            assert len(result.failed_places) == 1


# ============================================
# 测试: 告警回调
# ============================================

class TestAtomicRebuildAlarm:
    """测试告警功能"""
    
    @pytest.mark.asyncio
    async def test_alarm_callback(self):
        """告警回调被调用"""
        executor = MockExecutor()
        executor.cancel_fail_ids.add("fail_order")
        
        alarm_messages = []
        
        def alarm_callback(message):
            alarm_messages.append(message)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            rebuilder = AtomicRebuildExecutor(
                executor=executor,
                state_dir=tmpdir,
                max_retries=1,
            )
            rebuilder.set_alarm_callback(alarm_callback)
            
            inheritance = InheritanceResult(
                active_levels=[],
                retired_levels=[],
                orders_to_cancel=["fail_order"],
                orders_to_place=[],
            )
            
            result = await rebuilder.execute(inheritance, "BTCUSDT")
            
            assert result.needs_alarm is True
            assert len(alarm_messages) == 1
            assert "ALARM" in alarm_messages[0]


# ============================================
# 测试: 迁移计划持久化
# ============================================

class TestAtomicRebuildPersistence:
    """测试迁移计划持久化"""
    
    @pytest.mark.asyncio
    async def test_migration_saved_on_fail(self):
        """失败时保存迁移计划"""
        executor = MockExecutor()
        executor.cancel_fail_ids.add("fail_order")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            rebuilder = AtomicRebuildExecutor(
                executor=executor,
                state_dir=tmpdir,
                max_retries=1,
            )
            
            inheritance = InheritanceResult(
                active_levels=[],
                retired_levels=[],
                orders_to_cancel=["fail_order"],
                orders_to_place=[],
            )
            
            await rebuilder.execute(inheritance, "BTCUSDT")
            
            # 检查迁移文件是否存在
            migration_file = Path(tmpdir) / "pending_migration.json"
            assert migration_file.exists()
            
            with open(migration_file) as f:
                data = json.load(f)
            
            # 迁移文件保存的是执行过程中的状态
            # 可能是 CANCELLING 或 ALARM，取决于保存时机
            assert data["phase"] in ["CANCELLING", "ALARM"]
    
    @pytest.mark.asyncio
    async def test_migration_cleared_on_success(self):
        """成功时清除迁移计划"""
        executor = MockExecutor()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            rebuilder = AtomicRebuildExecutor(
                executor=executor,
                state_dir=tmpdir,
            )
            
            inheritance = InheritanceResult(
                active_levels=[],
                retired_levels=[],
                orders_to_cancel=[],
                orders_to_place=[],
            )
            
            await rebuilder.execute(inheritance, "BTCUSDT")
            
            # 迁移文件应该被删除
            migration_file = Path(tmpdir) / "pending_migration.json"
            assert not migration_file.exists()
    
    def test_load_pending_migration(self):
        """加载未完成的迁移计划"""
        executor = MockExecutor()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # 手动创建迁移文件
            migration_file = Path(tmpdir) / "pending_migration.json"
            migration_data = {
                "phase": "CANCELLING",
                "started_at": 1234567890,
                "orders_to_cancel": ["order_1"],
                "orders_cancelled": [],
                "orders_to_place": [],
                "orders_placed": [],
                "failed_orders": [],
                "error_message": None,
                "retry_count": 0,
            }
            with open(migration_file, "w") as f:
                json.dump(migration_data, f)
            
            rebuilder = AtomicRebuildExecutor(
                executor=executor,
                state_dir=tmpdir,
            )
            
            pending = rebuilder.load_pending_migration()
            
            assert pending is not None
            assert pending.phase == RebuildPhase.CANCELLING
            assert pending.orders_to_cancel == ["order_1"]


# ============================================
# 测试: PendingMigration 数据类
# ============================================

class TestPendingMigration:
    """测试 PendingMigration 数据类"""
    
    def test_to_dict(self):
        """测试序列化"""
        pending = PendingMigration(
            phase=RebuildPhase.PLACING,
            started_at=1234567890,
            orders_to_cancel=["order_1"],
            orders_cancelled=["order_2"],
        )
        
        data = pending.to_dict()
        
        assert data["phase"] == "PLACING"
        assert data["started_at"] == 1234567890
    
    def test_from_dict(self):
        """测试反序列化"""
        data = {
            "phase": "COMPLETED",
            "started_at": 1234567890,
            "orders_to_cancel": [],
            "orders_cancelled": ["order_1"],
            "orders_to_place": [],
            "orders_placed": ["order_2"],
            "failed_orders": [],
            "error_message": None,
            "retry_count": 0,
        }
        
        pending = PendingMigration.from_dict(data)
        
        assert pending.phase == RebuildPhase.COMPLETED
        assert pending.orders_cancelled == ["order_1"]
    
    def test_is_incomplete(self):
        """测试是否未完成"""
        pending_incomplete = PendingMigration(
            phase=RebuildPhase.CANCELLING,
            started_at=1234567890,
        )
        assert pending_incomplete.is_incomplete() is True
        
        pending_complete = PendingMigration(
            phase=RebuildPhase.COMPLETED,
            started_at=1234567890,
        )
        assert pending_complete.is_incomplete() is False
    
    def test_needs_intervention(self):
        """测试是否需要人工介入"""
        pending_alarm = PendingMigration(
            phase=RebuildPhase.ALARM,
            started_at=1234567890,
        )
        assert pending_alarm.needs_intervention() is True
        
        pending_normal = PendingMigration(
            phase=RebuildPhase.PLACING,
            started_at=1234567890,
        )
        assert pending_normal.needs_intervention() is False


# ============================================
# 运行测试
# ============================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
