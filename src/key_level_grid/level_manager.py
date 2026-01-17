"""
水位生命周期管理器 (SPEC_LEVEL_LIFECYCLE.md v2.0.0)

核心功能:
1. 按索引继承算法 (inherit_levels_by_index)
2. 水位排序与验证
3. 销毁保护机制
"""

import time
import random
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any

from key_level_grid.position import (
    GridLevelState,
    GridState,
    LevelLifecycleStatus,
    LevelStatus,
    ActiveFill,
    STATE_VERSION,
)
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
    继承结果
    
    包含继承后的活跃水位、退役水位、需要执行的订单操作
    """
    active_levels: List[GridLevelState] = field(default_factory=list)
    retired_levels: List[GridLevelState] = field(default_factory=list)
    orders_to_cancel: List[str] = field(default_factory=list)
    orders_to_place: List[OrderRequest] = field(default_factory=list)
    inventory_updates: List[Tuple[str, int, int]] = field(default_factory=list)  # (fill_id, old_level_id, new_level_id)
    
    def summary(self) -> str:
        """返回结果摘要"""
        return (
            f"活跃={len(self.active_levels)}, "
            f"退役={len(self.retired_levels)}, "
            f"撤单={len(self.orders_to_cancel)}, "
            f"挂单={len(self.orders_to_place)}, "
            f"更新持仓={len(self.inventory_updates)}"
        )


# ============================================
# 工具函数
# ============================================

# 全局计数器，确保同一毫秒内的 ID 唯一
_level_id_counter = 0


def generate_level_id() -> int:
    """
    生成唯一的 level_id
    
    格式: 时间戳微秒 + 计数器 (保证同一进程内唯一)
    """
    global _level_id_counter
    _level_id_counter = (_level_id_counter + 1) % 1000
    return int(time.time() * 1000000) + _level_id_counter


def sort_levels_descending(levels: List[GridLevelState]) -> List[GridLevelState]:
    """
    将水位按价格降序排列
    
    排序后: levels[0] 是最高价，levels[-1] 是最低价
    """
    return sorted(levels, key=lambda x: x.price, reverse=True)


def validate_level_order(levels: List[GridLevelState]) -> bool:
    """
    验证水位数组是否满足降序约束
    
    Returns:
        True if levels[0].price > levels[1].price > ... > levels[n].price
    """
    for i in range(len(levels) - 1):
        if levels[i].price <= levels[i + 1].price:
            return False
    return True


def price_matches(p1: float, p2: float, tolerance: float = 0.0001) -> bool:
    """
    判断两个价格是否匹配（在容差范围内）
    """
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
    按索引继承水位状态 (SPEC_LEVEL_LIFECYCLE.md Section 4.2)
    
    核心规则:
    - 新数组第 i 个继承旧数组第 i 个的 fill_counter 和订单
    - 多余新水位 (m > n): 设为 ACTIVE, fill_counter=0
    - 多余旧水位 (m < n): 转为 RETIRED
    
    Args:
        new_prices: 新水位价格列表（必须已按降序排列）
        old_levels: 旧水位列表（必须已按降序排列）
        active_inventory: 当前持仓记录
        default_side: 新水位默认方向
        default_role: 新水位默认角色
    
    Returns:
        InheritanceResult: 继承结果
    """
    result = InheritanceResult()
    
    m = len(new_prices)
    n = len(old_levels)
    
    logger.info(f"🔄 开始按索引继承: 新水位 {m} 个, 旧水位 {n} 个")
    
    # ========================================
    # Step 1: 按索引一一对应继承 (i = 0, 1, ..., min(m,n)-1)
    # ========================================
    for i in range(min(m, n)):
        new_price = new_prices[i]
        old_lvl = old_levels[i]
        
        new_level_id = generate_level_id()
        
        # 创建新水位，继承旧水位的状态
        inherited_level = GridLevelState(
            level_id=new_level_id,
            price=new_price,                              # 使用新价格
            side=old_lvl.side,
            role=old_lvl.role,
            status=LevelStatus.IDLE,                      # 重置订单状态
            lifecycle_status=LevelLifecycleStatus.ACTIVE,
            fill_counter=old_lvl.fill_counter,            # 继承 fill_counter
            target_qty=old_lvl.target_qty,                # 继承目标数量
            inherited_from_index=i,
            inheritance_ts=int(time.time()),
        )
        
        result.active_levels.append(inherited_level)
        
        # 日志
        price_diff = new_price - old_lvl.price
        price_diff_pct = (price_diff / old_lvl.price * 100) if old_lvl.price > 0 else 0
        logger.debug(
            f"  [继承] N[{i}]({new_price:,.0f}) ← O[{i}]({old_lvl.price:,.0f}): "
            f"fc={old_lvl.fill_counter}, Δ={price_diff:+,.0f} ({price_diff_pct:+.2f}%)"
        )
        
        # 撤销旧订单（价格已变化，需要重挂）
        if old_lvl.active_order_id:
            result.orders_to_cancel.append(old_lvl.active_order_id)
            
            # 按新价格重挂（如果有目标数量）
            if old_lvl.target_qty > 0:
                result.orders_to_place.append(OrderRequest(
                    side=old_lvl.side,
                    price=new_price,
                    qty=old_lvl.target_qty,
                    level_id=new_level_id,
                ))
        
        # 更新 active_inventory 中的 level_id
        for fill in active_inventory:
            if fill.level_id == old_lvl.level_id:
                result.inventory_updates.append(
                    (fill.order_id, old_lvl.level_id, new_level_id)
                )
    
    # ========================================
    # Step 2: 处理多余的新水位 (m > n)
    # ========================================
    for i in range(n, m):
        new_price = new_prices[i]
        
        # 全新水位，fill_counter = 0
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
    
    # ========================================
    # Step 3: 处理多余的旧水位 (m < n) → 退役
    # ========================================
    for i in range(m, n):
        old_lvl = old_levels[i]
        
        # 转为 RETIRED（仅允许卖出清仓）
        old_lvl.lifecycle_status = LevelLifecycleStatus.RETIRED
        result.retired_levels.append(old_lvl)
        
        logger.debug(
            f"  [退役] O[{i}]({old_lvl.price:,.0f}): fc={old_lvl.fill_counter} → RETIRED"
        )
        
        # 若有买单挂单，撤销（退役水位禁止买入）
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
    """
    检查水位是否可以销毁 (SPEC_LEVEL_LIFECYCLE.md Section 6.1)
    
    强制条件:
    1. fill_counter == 0
    2. 交易所无该价位挂单
    3. 无其他水位的卖单映射到此（卖单未平仓不能销毁）
    
    Args:
        level: 待检查的水位
        exchange_orders: 交易所当前挂单列表
        level_mapping: 邻位映射表 {buy_level_id: sell_level_id}
        price_tolerance: 价格匹配容差
    
    Returns:
        (can_destroy, reason)
    """
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
    """
    处理退役水位：检查是否可以转为 DEAD 并销毁
    
    Args:
        state: 网格状态
        exchange_orders: 交易所当前挂单列表
    
    Returns:
        被销毁的水位列表
    """
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
    
    # 更新 state
    state.retired_levels = remaining_retired
    
    # 清理 level_mapping 中的无效引用
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
    """
    将继承结果应用到网格状态
    
    Args:
        state: 网格状态（会被修改）
        result: 继承结果
        role: 水位角色 ("support" | "resistance")
    """
    if role == "support":
        state.support_levels_state = result.active_levels
    else:
        state.resistance_levels_state = result.active_levels
    
    # 合并退役水位（避免重复）
    existing_retired_ids = {lvl.level_id for lvl in state.retired_levels}
    for retired_lvl in result.retired_levels:
        if retired_lvl.level_id not in existing_retired_ids:
            state.retired_levels.append(retired_lvl)
    
    # 应用 inventory 更新
    for fill_id, old_id, new_id in result.inventory_updates:
        for fill in state.active_inventory:
            if fill.order_id == fill_id and fill.level_id == old_id:
                fill.level_id = new_id
                logger.debug(f"📦 更新持仓 {fill_id}: level_id {old_id} → {new_id}")
                break


def rebuild_level_mapping(state: GridState) -> Dict[int, int]:
    """
    重建逐级邻位映射表
    
    规则: 支撑位 → 其上方最近的水位（可以是支撑或阻力）
    
    Args:
        state: 网格状态
    
    Returns:
        新的 level_mapping
    """
    # 合并所有活跃水位和退役水位（按价格升序）
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
        
        # 找上一格（物理邻位）
        for j in range(i + 1, len(sorted_levels)):
            adjacent = sorted_levels[j]
            # 确保有最小利润空间
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
    """获取所有活跃水位（支撑 + 阻力）"""
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
