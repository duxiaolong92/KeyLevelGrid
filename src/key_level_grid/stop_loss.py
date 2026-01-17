"""
向后兼容层: 从 strategy.stop_loss 重新导出

请使用新路径: from key_level_grid.strategy import KeyLevelStopLossManager
"""

from key_level_grid.strategy.stop_loss import (
    KeyLevelStopLossManager,
    StopLossOrder,
)
from key_level_grid.core.config import StopLossConfig

__all__ = ["KeyLevelStopLossManager", "StopLossOrder", "StopLossConfig"]
