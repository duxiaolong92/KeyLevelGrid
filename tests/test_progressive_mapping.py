"""
Phase 5: 逐级邻位映射单元测试

测试内容:
- T5.1: build_level_mapping() 测试
- T5.2: sync_mapping() 测试
- T5.3: 集成测试
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from key_level_grid.position import (
    KeyLevelPositionManager,
    GridState,
    GridLevelState,
    LevelStatus,
    ActiveFill,
    GridOrder,
)


# ============================================
# 测试夹具 (Fixtures)
# ============================================

from key_level_grid.position import GridConfig, PositionConfig, ResistanceConfig

@pytest.fixture
def grid_config():
    """网格配置"""
    return GridConfig(
        max_grids=10,
        floor_buffer=0.05,
        range_mode="auto",
        sell_quota_ratio=0.7,
        min_profit_pct=0.005,
        buy_price_buffer_pct=0.002,
        sell_price_buffer_pct=0.002,
        base_amount_per_grid=0.001,
        base_position_locked=0,
        max_fill_per_level=3,
        recon_interval_sec=60,
        order_action_timeout_sec=30,
    )


@pytest.fixture
def position_config():
    """仓位配置"""
    return PositionConfig(
        total_capital=5000.0,
        max_leverage=3.0,
        allocation_mode="equal",
    )


@pytest.fixture
def resistance_config():
    """阻力配置"""
    return ResistanceConfig(
        swing_lookbacks=[10, 20],
        fib_ratios=[0.382, 0.618],
        merge_tolerance=0.005,
        min_distance_pct=0.01,
        max_distance_pct=0.1,
        min_strength=0,
    )


@pytest.fixture
def position_manager(grid_config, position_config, resistance_config):
    """创建 PositionManager 实例"""
    pm = KeyLevelPositionManager(
        symbol="BTCUSDT",
        grid_config=grid_config,
        position_config=position_config,
        resistance_config=resistance_config,
        exchange="gate",
    )
    return pm


@pytest.fixture
def sample_grid_state():
    """创建示例网格状态"""
    state = GridState(
        symbol="BTCUSDT",
        direction="long",
        upper_price=96000,
        lower_price=93000,
        grid_floor=92000,
        buy_orders=[],
        sell_orders=[],
        sell_quota_ratio=0.7,
        min_profit_pct=0.005,
        buy_price_buffer_pct=0.002,
        sell_price_buffer_pct=0.002,
        base_amount_per_grid=0.001,
        max_fill_per_level=3,
        recon_interval_sec=60,
        order_action_timeout_sec=30,
    )
    
    # 支撑位: 94000, 94500, 95000 (ID: 1, 2, 3)
    state.support_levels_state = [
        GridLevelState(level_id=1, price=94000, side="buy", role="support", status=LevelStatus.IDLE),
        GridLevelState(level_id=2, price=94500, side="buy", role="support", status=LevelStatus.IDLE),
        GridLevelState(level_id=3, price=95000, side="buy", role="support", status=LevelStatus.IDLE),
    ]
    
    # 阻力位: 95500, 96000 (ID: 1001, 1002)
    state.resistance_levels_state = [
        GridLevelState(level_id=1001, price=95500, side="sell", role="resistance", status=LevelStatus.IDLE),
        GridLevelState(level_id=1002, price=96000, side="sell", role="resistance", status=LevelStatus.IDLE),
    ]
    
    return state


# ============================================
# T5.1: build_level_mapping() 测试
# ============================================

class TestBuildLevelMapping:
    """测试 build_level_mapping() 函数"""
    
    def test_basic_mapping(self, position_manager, sample_grid_state):
        """测试基本映射: 每个支撑位映射到上方邻位"""
        position_manager.state = sample_grid_state
        
        mapping = position_manager.build_level_mapping()
        
        # S_1(94000) → S_2(94500)
        assert mapping.get(1) == 2, "S_1 应映射到 S_2"
        
        # S_2(94500) → S_3(95000)
        assert mapping.get(2) == 3, "S_2 应映射到 S_3"
        
        # S_3(95000) → R_1(95500)
        assert mapping.get(3) == 1001, "S_3 应映射到 R_1"
    
    def test_mapping_with_min_profit(self, position_manager, sample_grid_state):
        """测试最小利润过滤"""
        sample_grid_state.min_profit_pct = 0.01  # 1% 最小利润
        position_manager.state = sample_grid_state
        
        mapping = position_manager.build_level_mapping()
        
        # S_1(94000) 的目标价应 > 94000 * 1.01 = 94940
        # 所以 S_1 → S_3(95000) 而非 S_2(94500)
        assert mapping.get(1) == 3, "S_1 应跳过 S_2，映射到 S_3"
    
    def test_highest_support_no_adjacent(self, position_manager):
        """测试最高支撑位无上方邻位"""
        state = GridState(
            symbol="BTCUSDT",
            direction="long",
            upper_price=96000,
            lower_price=93000,
            grid_floor=92000,
            buy_orders=[],
            sell_orders=[],
            min_profit_pct=0.005,
            base_amount_per_grid=0.001,
        )
        # 只有支撑位，没有阻力位
        state.support_levels_state = [
            GridLevelState(level_id=1, price=95000, side="buy", role="support", status=LevelStatus.IDLE),
        ]
        state.resistance_levels_state = []
        
        position_manager.state = state
        mapping = position_manager.build_level_mapping()
        
        # 无邻位，映射应为空
        assert 1 not in mapping, "最高支撑位无邻位时不应有映射"
    
    def test_empty_state(self, position_manager):
        """测试空状态"""
        position_manager.state = None
        mapping = position_manager.build_level_mapping()
        assert mapping == {}, "空状态应返回空映射"


# ============================================
# T5.2: sync_mapping() 测试
# ============================================

class TestSyncMapping:
    """测试 sync_mapping() 函数"""
    
    def test_place_sell_order_on_deficit(self, position_manager, sample_grid_state):
        """测试缺口补单"""
        sample_grid_state.level_mapping = {1: 2, 2: 3, 3: 1001}
        sample_grid_state.support_levels_state[0].fill_counter = 2  # S_1 成交 2 次
        position_manager.state = sample_grid_state
        
        actions = position_manager.sync_mapping(
            current_price=95200,
            open_orders=[],
            exchange_min_qty=0.0001,
        )
        
        # 期望在 S_2(94500) 挂卖单
        # expected_qty = 2 * 0.001 * 0.7 = 0.0014
        assert len(actions) == 1
        assert actions[0]["action"] == "place"
        assert actions[0]["side"] == "sell"
        assert actions[0]["level_id"] == 2
        assert abs(actions[0]["qty"] - 0.0014) < 0.0001
    
    def test_no_action_when_matched(self, position_manager, sample_grid_state):
        """测试数量匹配时无动作"""
        sample_grid_state.level_mapping = {1: 2, 2: 3, 3: 1001}
        sample_grid_state.support_levels_state[0].fill_counter = 1
        position_manager.state = sample_grid_state
        
        # 已有匹配的卖单
        open_orders = [
            {"id": "order_1", "side": "sell", "price": 94500, "base_amount": 0.0007}
        ]
        
        actions = position_manager.sync_mapping(
            current_price=95200,
            open_orders=open_orders,
            exchange_min_qty=0.0001,
        )
        
        # 数量匹配，无需操作
        assert len(actions) == 0
    
    def test_cancel_excess_order(self, position_manager, sample_grid_state):
        """测试撤销多余挂单"""
        sample_grid_state.level_mapping = {1: 2, 2: 3, 3: 1001}
        # fill_counter = 0，期望卖单量为 0
        position_manager.state = sample_grid_state
        
        # 但存在挂单
        open_orders = [
            {"id": "order_1", "side": "sell", "price": 94500, "base_amount": 0.001}
        ]
        
        actions = position_manager.sync_mapping(
            current_price=95200,
            open_orders=open_orders,
            exchange_min_qty=0.0001,
        )
        
        # 应撤单
        assert len(actions) == 1
        assert actions[0]["action"] == "cancel"
        assert actions[0]["order_id"] == "order_1"
    
    def test_multiple_support_fills(self, position_manager, sample_grid_state):
        """测试多个支撑位成交"""
        sample_grid_state.level_mapping = {1: 2, 2: 3, 3: 1001}
        sample_grid_state.support_levels_state[0].fill_counter = 1  # S_1 成交 1 次
        sample_grid_state.support_levels_state[1].fill_counter = 2  # S_2 成交 2 次
        position_manager.state = sample_grid_state
        
        actions = position_manager.sync_mapping(
            current_price=95200,
            open_orders=[],
            exchange_min_qty=0.0001,
        )
        
        # S_1 → S_2: 1 * 0.001 * 0.7 = 0.0007
        # S_2 → S_3: 2 * 0.001 * 0.7 = 0.0014
        action_by_level = {a["level_id"]: a for a in actions}
        
        assert 2 in action_by_level  # S_2 的卖单
        assert 3 in action_by_level  # S_3 的卖单
        assert abs(action_by_level[2]["qty"] - 0.0007) < 0.0001
        assert abs(action_by_level[3]["qty"] - 0.0014) < 0.0001


# ============================================
# T5.3: 辅助函数测试
# ============================================

class TestHelperFunctions:
    """测试辅助函数"""
    
    def test_price_matches(self, position_manager):
        """测试价格匹配"""
        assert position_manager.price_matches(94500, 94500) is True
        assert position_manager.price_matches(94500.01, 94500) is True  # 在容差内
        assert position_manager.price_matches(94510, 94500) is False  # 超出容差
    
    def test_index_orders_by_level(self, position_manager, sample_grid_state):
        """测试挂单索引"""
        position_manager.state = sample_grid_state
        
        open_orders = [
            {"id": "o1", "side": "sell", "price": 94500, "base_amount": 0.001},
            {"id": "o2", "side": "sell", "price": 95500, "base_amount": 0.002},
            {"id": "o3", "side": "buy", "price": 94000, "base_amount": 0.001},
        ]
        
        indexed = position_manager._index_orders_by_level(open_orders, side="sell")
        
        assert 2 in indexed  # S_2 at 94500
        assert 1001 in indexed  # R_1 at 95500
        assert len(indexed[2]) == 1
        assert indexed[2][0]["id"] == "o1"
    
    def test_get_level_by_id(self, position_manager, sample_grid_state):
        """测试按 ID 查找水位"""
        position_manager.state = sample_grid_state
        
        lvl = position_manager._get_level_by_id(2)
        assert lvl is not None
        assert lvl.price == 94500
        
        lvl = position_manager._get_level_by_id(1001)
        assert lvl is not None
        assert lvl.price == 95500
        
        lvl = position_manager._get_level_by_id(9999)
        assert lvl is None


# ============================================
# T5.4: 数据结构序列化测试
# ============================================

class TestSerialization:
    """测试数据结构序列化"""
    
    def test_grid_state_with_mapping(self, sample_grid_state):
        """测试 GridState 包含 level_mapping 的序列化"""
        sample_grid_state.level_mapping = {1: 2, 2: 3, 3: 1001}
        
        data = sample_grid_state.to_dict()
        
        assert "level_mapping" in data
        assert data["level_mapping"] == {1: 2, 2: 3, 3: 1001}
    
    def test_active_fill_with_sell_tracking(self):
        """测试 ActiveFill 包含卖单追踪字段的序列化"""
        fill = ActiveFill(
            order_id="fill_001",
            level_id=1,
            price=94000,
            qty=0.001,
            timestamp=1700000000,
            target_sell_level_id=2,
            sell_order_id="sell_001",
            sell_qty=0.0007,
        )
        
        data = fill.to_dict()
        
        assert data["target_sell_level_id"] == 2
        assert data["sell_order_id"] == "sell_001"
        assert data["sell_qty"] == 0.0007
    
    def test_active_fill_from_dict_backward_compat(self):
        """测试 ActiveFill 向后兼容（旧版无新字段）"""
        old_data = {
            "order_id": "fill_001",
            "level_id": 1,
            "price": 94000,
            "qty": 0.001,
            "timestamp": 1700000000,
        }
        
        fill = ActiveFill.from_dict(old_data)
        
        assert fill.target_sell_level_id is None
        assert fill.sell_order_id is None
        assert fill.sell_qty == 0.0


# ============================================
# 运行测试
# ============================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
