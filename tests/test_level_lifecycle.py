"""
水位生命周期单元测试 (SPEC_LEVEL_LIFECYCLE.md v2.0.0)

测试覆盖:
1. 排序和验证函数
2. 按索引继承算法
3. 销毁保护机制
4. 向后兼容性
"""

import pytest
import sys
from pathlib import Path

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from key_level_grid.position import (
    GridLevelState,
    GridState,
    LevelLifecycleStatus,
    LevelStatus,
    ActiveFill,
    STATE_VERSION,
)
from key_level_grid.level_manager import (
    sort_levels_descending,
    validate_level_order,
    inherit_levels_by_index,
    can_destroy_level,
    process_retired_levels,
    apply_inheritance_to_state,
    rebuild_level_mapping,
    generate_level_id,
    price_matches,
    InheritanceResult,
)


# ============================================
# 测试: 排序和验证
# ============================================

class TestSortAndValidate:
    """测试排序和验证函数"""
    
    def test_sort_descending(self):
        """测试降序排序"""
        levels = [
            GridLevelState(level_id=1, price=92000, side="buy"),
            GridLevelState(level_id=2, price=96000, side="buy"),
            GridLevelState(level_id=3, price=94000, side="buy"),
        ]
        sorted_levels = sort_levels_descending(levels)
        
        assert [l.price for l in sorted_levels] == [96000, 94000, 92000]
    
    def test_sort_empty(self):
        """测试空列表排序"""
        assert sort_levels_descending([]) == []
    
    def test_validate_order_valid(self):
        """测试有效的降序验证"""
        levels = [
            GridLevelState(level_id=1, price=96000, side="buy"),
            GridLevelState(level_id=2, price=94000, side="buy"),
            GridLevelState(level_id=3, price=92000, side="buy"),
        ]
        assert validate_level_order(levels) is True
    
    def test_validate_order_invalid(self):
        """测试无效的降序验证"""
        levels = [
            GridLevelState(level_id=1, price=94000, side="buy"),
            GridLevelState(level_id=2, price=96000, side="buy"),  # 错误
        ]
        assert validate_level_order(levels) is False
    
    def test_validate_order_equal(self):
        """测试相等价格（应该无效）"""
        levels = [
            GridLevelState(level_id=1, price=94000, side="buy"),
            GridLevelState(level_id=2, price=94000, side="buy"),
        ]
        assert validate_level_order(levels) is False
    
    def test_validate_order_single(self):
        """测试单个水位"""
        levels = [GridLevelState(level_id=1, price=94000, side="buy")]
        assert validate_level_order(levels) is True
    
    def test_validate_order_empty(self):
        """测试空列表"""
        assert validate_level_order([]) is True


# ============================================
# 测试: 按索引继承
# ============================================

class TestInheritByIndex:
    """测试按索引继承算法"""
    
    def test_equal_length(self):
        """等长继承"""
        old_levels = [
            GridLevelState(level_id=1, price=96000, side="buy", fill_counter=1),
            GridLevelState(level_id=2, price=94000, side="buy", fill_counter=2),
        ]
        new_prices = [96500, 94500]
        
        result = inherit_levels_by_index(new_prices, old_levels, [])
        
        assert len(result.active_levels) == 2
        assert result.active_levels[0].price == 96500
        assert result.active_levels[0].fill_counter == 1
        assert result.active_levels[1].price == 94500
        assert result.active_levels[1].fill_counter == 2
        assert len(result.retired_levels) == 0
    
    def test_expand_grid(self):
        """扩展网格 (m > n)"""
        old_levels = [
            GridLevelState(level_id=1, price=96000, side="buy", fill_counter=1),
        ]
        new_prices = [96500, 94500, 92500]
        
        result = inherit_levels_by_index(new_prices, old_levels, [])
        
        assert len(result.active_levels) == 3
        assert result.active_levels[0].fill_counter == 1  # 继承
        assert result.active_levels[1].fill_counter == 0  # 新增
        assert result.active_levels[2].fill_counter == 0  # 新增
        assert len(result.retired_levels) == 0
    
    def test_shrink_grid(self):
        """收缩网格 (m < n)"""
        old_levels = [
            GridLevelState(level_id=1, price=96000, side="buy", fill_counter=1),
            GridLevelState(level_id=2, price=94000, side="buy", fill_counter=2),
            GridLevelState(level_id=3, price=92000, side="buy", fill_counter=1),
        ]
        new_prices = [96500, 94500]
        
        result = inherit_levels_by_index(new_prices, old_levels, [])
        
        assert len(result.active_levels) == 2
        assert len(result.retired_levels) == 1
        assert result.retired_levels[0].lifecycle_status == LevelLifecycleStatus.RETIRED
        assert result.retired_levels[0].fill_counter == 1
        assert result.retired_levels[0].price == 92000
    
    def test_inherit_with_orders(self):
        """继承时处理订单"""
        old_levels = [
            GridLevelState(
                level_id=1, price=96000, side="buy",
                fill_counter=1, active_order_id="order_001", target_qty=100
            ),
        ]
        new_prices = [96500]
        
        result = inherit_levels_by_index(new_prices, old_levels, [])
        
        assert len(result.orders_to_cancel) == 1
        assert result.orders_to_cancel[0] == "order_001"
        assert len(result.orders_to_place) == 1
        assert result.orders_to_place[0].price == 96500
        assert result.orders_to_place[0].qty == 100
    
    def test_inherit_updates_inventory(self):
        """继承时更新持仓记录"""
        old_levels = [
            GridLevelState(level_id=100, price=96000, side="buy", fill_counter=1),
        ]
        inventory = [
            ActiveFill(order_id="fill_001", price=95800, qty=0.001, level_id=100, timestamp=1234567890),
        ]
        new_prices = [96500]
        
        result = inherit_levels_by_index(new_prices, old_levels, inventory)
        
        assert len(result.inventory_updates) == 1
        fill_id, old_id, new_id = result.inventory_updates[0]
        assert fill_id == "fill_001"
        assert old_id == 100
        assert new_id == result.active_levels[0].level_id
    
    def test_empty_old_levels(self):
        """旧水位为空"""
        new_prices = [96500, 94500]
        
        result = inherit_levels_by_index(new_prices, [], [])
        
        assert len(result.active_levels) == 2
        assert all(lvl.fill_counter == 0 for lvl in result.active_levels)
        assert len(result.retired_levels) == 0
    
    def test_empty_new_prices(self):
        """新价格为空"""
        old_levels = [
            GridLevelState(level_id=1, price=96000, side="buy", fill_counter=1),
        ]
        
        result = inherit_levels_by_index([], old_levels, [])
        
        assert len(result.active_levels) == 0
        assert len(result.retired_levels) == 1
        assert result.retired_levels[0].lifecycle_status == LevelLifecycleStatus.RETIRED
    
    def test_lifecycle_status_set_correctly(self):
        """验证生命周期状态设置正确"""
        old_levels = [
            GridLevelState(level_id=1, price=96000, side="buy", fill_counter=1),
        ]
        new_prices = [96500]
        
        result = inherit_levels_by_index(new_prices, old_levels, [])
        
        assert result.active_levels[0].lifecycle_status == LevelLifecycleStatus.ACTIVE
    
    def test_retired_buy_order_cancelled(self):
        """退役水位的买单应被撤销"""
        old_levels = [
            GridLevelState(
                level_id=1, price=96000, side="buy",
                fill_counter=1, active_order_id="buy_001"
            ),
            GridLevelState(
                level_id=2, price=94000, side="buy",
                fill_counter=0, active_order_id="buy_002"
            ),
        ]
        new_prices = [96500]  # 只保留一个
        
        result = inherit_levels_by_index(new_prices, old_levels, [])
        
        # 第一个水位的订单因继承被撤销
        # 第二个水位因退役被撤销
        assert "buy_001" in result.orders_to_cancel
        assert "buy_002" in result.orders_to_cancel


# ============================================
# 测试: 销毁保护
# ============================================

class TestCanDestroy:
    """测试销毁保护机制"""
    
    def test_can_destroy_empty(self):
        """可以销毁: fill_counter=0, 无挂单, 无映射"""
        level = GridLevelState(
            level_id=1, price=94000, side="buy",
            fill_counter=0,
            lifecycle_status=LevelLifecycleStatus.RETIRED,
        )
        can, reason = can_destroy_level(level, [], {})
        assert can is True
        assert reason == "OK"
    
    def test_cannot_destroy_has_counter(self):
        """不能销毁: fill_counter > 0"""
        level = GridLevelState(
            level_id=1, price=94000, side="buy",
            fill_counter=1,
            lifecycle_status=LevelLifecycleStatus.RETIRED,
        )
        can, reason = can_destroy_level(level, [], {})
        assert can is False
        assert "fill_counter" in reason
    
    def test_cannot_destroy_has_order(self):
        """不能销毁: 交易所有挂单"""
        level = GridLevelState(
            level_id=1, price=94000, side="buy",
            fill_counter=0,
        )
        exchange_orders = [{"id": "order_001", "price": "94000.0"}]
        can, reason = can_destroy_level(level, exchange_orders, {})
        assert can is False
        assert "挂单" in reason
    
    def test_cannot_destroy_has_mapping(self):
        """不能销毁: 有映射指向"""
        level = GridLevelState(
            level_id=100, price=94000, side="buy",
            fill_counter=0,
        )
        level_mapping = {50: 100}  # 水位 50 的止盈映射到 100
        can, reason = can_destroy_level(level, [], level_mapping)
        assert can is False
        assert "映射" in reason
    
    def test_price_tolerance(self):
        """价格容差匹配"""
        level = GridLevelState(
            level_id=1, price=94000, side="buy",
            fill_counter=0,
        )
        # 价格稍有偏差
        exchange_orders = [{"id": "order_001", "price": "94000.01"}]
        can, reason = can_destroy_level(level, exchange_orders, {}, price_tolerance=0.0001)
        assert can is False  # 在容差范围内，匹配


# ============================================
# 测试: 向后兼容
# ============================================

class TestBackwardCompatibility:
    """测试向后兼容性"""
    
    def test_gridlevelstate_from_dict_v1(self):
        """从 v1 格式加载 GridLevelState（无 lifecycle_status）"""
        v1_data = {
            "level_id": 1,
            "price": 94000.0,
            "side": "buy",
            "role": "support",
            "status": "IDLE",
            "fill_counter": 2,
            # 没有 lifecycle_status 字段
        }
        
        level = GridLevelState.from_dict(v1_data)
        
        # 应该默认为 ACTIVE
        assert level.lifecycle_status == LevelLifecycleStatus.ACTIVE
        assert level.fill_counter == 2
    
    def test_gridlevelstate_from_dict_v2(self):
        """从 v2 格式加载 GridLevelState"""
        v2_data = {
            "level_id": 1,
            "price": 94000.0,
            "side": "buy",
            "role": "support",
            "status": "IDLE",
            "lifecycle_status": "RETIRED",
            "fill_counter": 1,
        }
        
        level = GridLevelState.from_dict(v2_data)
        
        assert level.lifecycle_status == LevelLifecycleStatus.RETIRED
    
    def test_gridlevelstate_to_dict(self):
        """GridLevelState.to_dict 包含新字段"""
        level = GridLevelState(
            level_id=1, price=94000, side="buy",
            lifecycle_status=LevelLifecycleStatus.RETIRED,
            inherited_from_index=0,
            inheritance_ts=1234567890,
        )
        
        data = level.to_dict()
        
        assert data["lifecycle_status"] == "RETIRED"
        assert data["inherited_from_index"] == 0
        assert data["inheritance_ts"] == 1234567890
    
    def test_state_version(self):
        """验证 STATE_VERSION (V3.0 升级到 3)"""
        assert STATE_VERSION == 3


# ============================================
# 测试: 辅助函数
# ============================================

class TestHelperFunctions:
    """测试辅助函数"""
    
    def test_generate_level_id_unique(self):
        """level_id 应该唯一"""
        ids = [generate_level_id() for _ in range(100)]
        assert len(set(ids)) == 100
    
    def test_price_matches_exact(self):
        """精确匹配"""
        assert price_matches(94000.0, 94000.0) is True
    
    def test_price_matches_within_tolerance(self):
        """容差内匹配"""
        assert price_matches(94000.0, 94000.01, tolerance=0.0001) is True
    
    def test_price_matches_outside_tolerance(self):
        """超出容差"""
        assert price_matches(94000.0, 95000.0, tolerance=0.0001) is False
    
    def test_price_matches_zero(self):
        """零价格"""
        assert price_matches(94000.0, 0.0) is False


# ============================================
# 测试: 状态应用
# ============================================

class TestApplyInheritance:
    """测试状态应用函数"""
    
    def test_apply_to_state(self):
        """应用继承结果到状态"""
        state = GridState(symbol="BTCUSDT")
        state.active_inventory = [
            ActiveFill(order_id="fill_001", price=95800, qty=0.001, level_id=100, timestamp=1234567890),
        ]
        
        result = InheritanceResult(
            active_levels=[
                GridLevelState(level_id=200, price=96500, side="buy", fill_counter=1),
            ],
            retired_levels=[
                GridLevelState(level_id=100, price=96000, side="buy", fill_counter=0, lifecycle_status=LevelLifecycleStatus.RETIRED),
            ],
            inventory_updates=[("fill_001", 100, 200)],
        )
        
        apply_inheritance_to_state(state, result, role="support")
        
        assert len(state.support_levels_state) == 1
        assert state.support_levels_state[0].level_id == 200
        assert len(state.retired_levels) == 1
        assert state.active_inventory[0].level_id == 200  # 已更新


# ============================================
# 运行测试
# ============================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
