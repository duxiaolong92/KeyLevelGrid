"""
向后兼容层: 从 signal 重新导出

请使用新路径: from key_level_grid.signal import KeyLevelSignalGenerator
"""

from key_level_grid.signal.generator import (
    KeyLevelSignalGenerator,
    KeyLevelSignal,
)
from key_level_grid.core.types import SignalType, SignalGrade
from key_level_grid.core.config import SignalConfig

__all__ = [
    "KeyLevelSignalGenerator",
    "KeyLevelSignal",
    "SignalType",
    "SignalGrade",
    "SignalConfig",
]
