"""
网格策略模块 (LEVEL_GENERATION.md v3.1.0)

V3.0 新增:
- AtomicRebuildExecutor: 原子性重构执行器
"""

from .level_lifecycle import (
    LevelLifecycleManager,
    InheritanceResult,
    OrderRequest,
    inherit_levels_by_index,
    generate_level_id,
    sort_levels_descending,
    validate_level_order,
    can_destroy_level,
    process_retired_levels,
    apply_inheritance_to_state,
    rebuild_level_mapping,
    get_all_active_levels,
    get_levels_by_lifecycle,
    count_total_fill_counter,
    price_matches,
)
from .atomic_rebuild import (
    AtomicRebuildExecutor,
    AtomicRebuildResult,
)

__all__ = [
    "LevelLifecycleManager",
    "InheritanceResult",
    "OrderRequest",
    "inherit_levels_by_index",
    "generate_level_id",
    "sort_levels_descending",
    "validate_level_order",
    "can_destroy_level",
    "process_retired_levels",
    "apply_inheritance_to_state",
    "rebuild_level_mapping",
    "get_all_active_levels",
    "get_levels_by_lifecycle",
    "count_total_fill_counter",
    "price_matches",
    # V3.0 新增
    "AtomicRebuildExecutor",
    "AtomicRebuildResult",
]
