"""
向后兼容层: 从 strategy.grid 重新导出

请使用新路径: from key_level_grid.strategy.grid import LevelLifecycleManager
"""

from key_level_grid.strategy.grid.level_lifecycle import (
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
]
