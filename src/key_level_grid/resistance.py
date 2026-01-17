"""
向后兼容层: 从 analysis.resistance 和 strategy.take_profit 重新导出

请使用新路径:
- from key_level_grid.analysis import ResistanceCalculator, PriceLevel
- from key_level_grid.strategy import ResistanceBasedTakeProfit, TakeProfitPlan
"""

from key_level_grid.analysis.resistance import ResistanceCalculator, PriceLevel
from key_level_grid.strategy.take_profit import (
    ResistanceBasedTakeProfit,
    TakeProfitPlan,
    TakeProfitLevel,
)
from key_level_grid.core.config import ResistanceConfig, TakeProfitConfig
from key_level_grid.core.types import LevelType

__all__ = [
    "ResistanceCalculator",
    "PriceLevel",
    "ResistanceBasedTakeProfit",
    "TakeProfitPlan",
    "TakeProfitLevel",
    "ResistanceConfig",
    "TakeProfitConfig",
    "LevelType",
]
