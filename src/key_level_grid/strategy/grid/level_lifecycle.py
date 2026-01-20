"""
水位生命周期管理器 (SPEC_LEVEL_LIFECYCLE.md v2.0.0)

核心功能:
1. 按索引继承算法 (inherit_levels_by_index)
2. 水位排序与验证
3. 销毁保护机制
"""

import time
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any

from key_level_grid.core.state import (
    GridLevelState,
    GridState,
    ActiveFill,
    STATE_VERSION,
)
from key_level_grid.core.types import LevelLifecycleStatus, LevelStatus
from key_level_grid.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================
# 数据结构
# ============================================

@dataclass
class OrderRequest:
    """订单请求"""
    side: str           # buy | sell
    price: float
    qty: float
    level_id: int


@dataclass
class InheritanceResult:
    """
    继承结果 (SELL_MAPPING.md Section 7)
    
    包含继承后的活跃水位、退役水位、需要执行的订单操作
    
    注意：根据规则 2（索引归属原则），持仓的 level_index 在网格重建后不变，
    自动对应新水位，因此不再需要 inventory_updates 字段。
    """
    active_levels: List[GridLevelState] = field(default_factory=list)
    retired_levels: List[GridLevelState] = field(default_factory=list)
    orders_to_cancel: List[str] = field(default_factory=list)
    orders_to_place: List[OrderRequest] = field(default_factory=list)
    # inventory_updates 已废弃 - 持仓使用 level_index，网格重建后不变
    
    def summary(self) -> str:
        """返回结果摘要"""
        return (
            f"活跃={len(self.active_levels)}, "
            f"退役={len(self.retired_levels)}, "
            f"撤单={len(self.orders_to_cancel)}, "
            f"挂单={len(self.orders_to_place)}"
        )


# ============================================
# 工具函数
# ============================================

_level_id_counter = 0


def generate_level_id() -> int:
    """生成唯一的 level_id"""
    global _level_id_counter
    _level_id_counter = (_level_id_counter + 1) % 1000
    return int(time.time() * 1000000) + _level_id_counter


def sort_levels_descending(levels: List[GridLevelState]) -> List[GridLevelState]:
    """将水位按价格降序排列"""
    return sorted(levels, key=lambda x: x.price, reverse=True)


def validate_level_order(levels: List[GridLevelState]) -> bool:
    """验证水位数组是否满足降序约束"""
    for i in range(len(levels) - 1):
        if levels[i].price <= levels[i + 1].price:
            return False
    return True


def price_matches(p1: float, p2: float, tolerance: float = 0.0001) -> bool:
    """判断两个价格是否匹配"""
    if p2 == 0:
        return False
    return abs(p1 - p2) / p2 < tolerance


# ============================================
# 核心算法: 按索引继承
# ============================================

def inherit_levels_by_index(
    new_prices: List[float],
    old_levels: List[GridLevelState],
    active_inventory: List[ActiveFill],
    default_side: str = "buy",
    default_role: str = "support",
) -> InheritanceResult:
    """
    按索引继承水位状态
    
    核心规则:
    - 新数组第 i 个继承旧数组第 i 个的 fill_counter 和订单
    - 多余新水位 (m > n): 设为 ACTIVE, fill_counter=0
    - 多余旧水位 (m < n): 转为 RETIRED
    """
    result = InheritanceResult()
    
    m = len(new_prices)
    n = len(old_levels)
    
    logger.info(f"🔄 开始按索引继承: 新水位 {m} 个, 旧水位 {n} 个")
    
    # Step 1: 按索引一一对应继承
    for i in range(min(m, n)):
        new_price = new_prices[i]
        old_lvl = old_levels[i]
        
        new_level_id = generate_level_id()
        
        inherited_level = GridLevelState(
            level_id=new_level_id,
            price=new_price,
            side=old_lvl.side,
            role=old_lvl.role,
            status=LevelStatus.IDLE,
            lifecycle_status=LevelLifecycleStatus.ACTIVE,
            fill_counter=old_lvl.fill_counter,
            target_qty=old_lvl.target_qty,
            inherited_from_index=i,
            inheritance_ts=int(time.time()),
        )
        
        result.active_levels.append(inherited_level)
        
        price_diff = new_price - old_lvl.price
        price_diff_pct = (price_diff / old_lvl.price * 100) if old_lvl.price > 0 else 0
        logger.debug(
            f"  [继承] N[{i}]({new_price:,.0f}) ← O[{i}]({old_lvl.price:,.0f}): "
            f"fc={old_lvl.fill_counter}, Δ={price_diff:+,.0f} ({price_diff_pct:+.2f}%)"
        )
        
        if old_lvl.active_order_id:
            result.orders_to_cancel.append(old_lvl.active_order_id)
            
            if old_lvl.target_qty > 0:
                result.orders_to_place.append(OrderRequest(
                    side=old_lvl.side,
                    price=new_price,
                    qty=old_lvl.target_qty,
                    level_id=new_level_id,
                ))
        
        # 根据 SELL_MAPPING.md 规则 2（索引归属原则），
        # 持仓的 level_index 在网格重建后不变，自动对应新水位，
        # 不再需要更新 inventory
    
    # Step 2: 处理多余的新水位 (m > n)
    for i in range(n, m):
        new_price = new_prices[i]
        
        fresh_level = GridLevelState(
            level_id=generate_level_id(),
            price=new_price,
            side=default_side,
            role=default_role,
            status=LevelStatus.IDLE,
            lifecycle_status=LevelLifecycleStatus.ACTIVE,
            fill_counter=0,
        )
        
        result.active_levels.append(fresh_level)
        
        logger.debug(f"  [新增] N[{i}]({new_price:,.0f}): fc=0, ACTIVE")
    
    # Step 3: 处理多余的旧水位 (m < n) → 退役
    for i in range(m, n):
        old_lvl = old_levels[i]
        
        old_lvl.lifecycle_status = LevelLifecycleStatus.RETIRED
        result.retired_levels.append(old_lvl)
        
        logger.debug(
            f"  [退役] O[{i}]({old_lvl.price:,.0f}): fc={old_lvl.fill_counter} → RETIRED"
        )
        
        if old_lvl.side == "buy" and old_lvl.active_order_id:
            result.orders_to_cancel.append(old_lvl.active_order_id)
    
    logger.info(f"✅ 继承完成: {result.summary()}")
    
    return result


# ============================================
# 销毁保护机制
# ============================================

def can_destroy_level(
    level: GridLevelState,
    exchange_orders: List[Dict[str, Any]],
    level_mapping: Dict[int, int],
    price_tolerance: float = 0.0001,
) -> Tuple[bool, str]:
    """检查水位是否可以销毁"""
    # 条件 1: fill_counter == 0
    if level.fill_counter > 0:
        return False, f"fill_counter={level.fill_counter}, 有未清仓持仓"
    
    # 条件 2: 交易所无该价位挂单
    for order in exchange_orders:
        order_price = float(order.get("price", 0))
        if order_price > 0 and price_matches(order_price, level.price, price_tolerance):
            return False, f"交易所存在挂单 {order.get('id')} @ {order_price}"
    
    # 条件 3: 无其他水位的卖单映射到此
    for src_id, tgt_id in level_mapping.items():
        if tgt_id == level.level_id:
            return False, f"水位 L_{src_id} 的止盈仍映射到此"
    
    return True, "OK"


def process_retired_levels(
    state: GridState,
    exchange_orders: List[Dict[str, Any]],
) -> List[GridLevelState]:
    """处理退役水位：检查是否可以转为 DEAD 并销毁"""
    destroyed = []
    remaining_retired = []
    
    for level in state.retired_levels:
        can_destroy, reason = can_destroy_level(
            level, exchange_orders, state.level_mapping
        )
        
        if can_destroy:
            level.lifecycle_status = LevelLifecycleStatus.DEAD
            destroyed.append(level)
            logger.info(f"🗑️ RETIRED → DEAD: L_{level.level_id} @ {level.price:,.0f}")
        else:
            remaining_retired.append(level)
            logger.debug(f"⏳ L_{level.level_id} 暂不能销毁: {reason}")
    
    state.retired_levels = remaining_retired
    
    if destroyed:
        destroyed_ids = {lvl.level_id for lvl in destroyed}
        state.level_mapping = {
            k: v for k, v in state.level_mapping.items()
            if k not in destroyed_ids and v not in destroyed_ids
        }
    
    return destroyed


# ============================================
# 状态应用函数
# ============================================

def apply_inheritance_to_state(
    state: GridState,
    result: InheritanceResult,
    role: str = "support",
) -> None:
    """将继承结果应用到网格状态"""
    if role == "support":
        state.support_levels_state = result.active_levels
    else:
        state.resistance_levels_state = result.active_levels
    
    existing_retired_ids = {lvl.level_id for lvl in state.retired_levels}
    for retired_lvl in result.retired_levels:
        if retired_lvl.level_id not in existing_retired_ids:
            state.retired_levels.append(retired_lvl)
    
    # 根据 SELL_MAPPING.md 规则 2（索引归属原则），
    # 持仓的 level_index 在网格重建后不变，自动对应新水位，
    # 不再需要更新 inventory


def rebuild_level_mapping(state: GridState) -> Dict[int, int]:
    """重建逐级邻位映射表"""
    all_levels = (
        state.support_levels_state + 
        state.resistance_levels_state + 
        [lvl for lvl in state.retired_levels if lvl.fill_counter > 0]
    )
    sorted_levels = sorted(all_levels, key=lambda x: x.price)
    
    mapping = {}
    
    for i, level in enumerate(sorted_levels):
        if level.fill_counter <= 0:
            continue
        
        for j in range(i + 1, len(sorted_levels)):
            adjacent = sorted_levels[j]
            if adjacent.price > level.price * 1.0001:
                mapping[level.level_id] = adjacent.level_id
                break
    
    state.level_mapping = mapping
    logger.info(f"🔗 重建邻位映射: {len(mapping)} 个")
    
    return mapping


# ============================================
# 便捷函数
# ============================================

def get_all_active_levels(state: GridState) -> List[GridLevelState]:
    """获取所有活跃水位"""
    return state.support_levels_state + state.resistance_levels_state


def get_levels_by_lifecycle(
    state: GridState,
    status: LevelLifecycleStatus
) -> List[GridLevelState]:
    """按生命周期状态筛选水位"""
    all_levels = get_all_active_levels(state) + state.retired_levels
    return [lvl for lvl in all_levels if lvl.lifecycle_status == status]


def count_total_fill_counter(levels: List[GridLevelState]) -> int:
    """计算水位列表的 fill_counter 总和"""
    return sum(lvl.fill_counter for lvl in levels)


class LevelLifecycleManager:
    """水位生命周期管理器封装类"""
    
    def __init__(self):
        self.logger = get_logger(__name__)
    
    def inherit_levels(
        self,
        new_prices: List[float],
        old_levels: List[GridLevelState],
        active_inventory: List[ActiveFill],
        default_side: str = "buy",
        default_role: str = "support",
    ) -> InheritanceResult:
        """按索引继承水位状态"""
        return inherit_levels_by_index(
            new_prices, old_levels, active_inventory, default_side, default_role
        )
    
    def process_retired(
        self,
        state: GridState,
        exchange_orders: List[Dict[str, Any]],
    ) -> List[GridLevelState]:
        """处理退役水位"""
        return process_retired_levels(state, exchange_orders)
    
    def apply_inheritance(
        self,
        state: GridState,
        result: InheritanceResult,
        role: str = "support",
    ) -> None:
        """应用继承结果"""
        apply_inheritance_to_state(state, result, role)
    
    def rebuild_mapping(self, state: GridState) -> Dict[int, int]:
        """重建映射表"""
        return rebuild_level_mapping(state)
