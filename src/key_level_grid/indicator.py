"""
向后兼容层: 从 analysis.indicator 重新导出

请使用新路径: from key_level_grid.analysis import KeyLevelGridIndicator
"""

from key_level_grid.analysis.indicator import KeyLevelGridIndicator
from key_level_grid.core.config import IndicatorConfig

__all__ = ["KeyLevelGridIndicator", "IndicatorConfig"]
