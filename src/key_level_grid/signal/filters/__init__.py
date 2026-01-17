"""
信号过滤器模块

支持可组合的过滤器链
"""

from .chain import (
    SignalFilter,
    FilterResult,
    SignalFilterChain,
)
from .breakout import (
    BreakoutFilter,
    BreakoutResult,
)
from .technical import (
    MACDTrendFilter,
    RSIFilter,
    ADXFilter,
    VolumeFilter,
)

# 从 core 模块重新导出
from key_level_grid.core.config import FilterConfig, BreakoutFilterConfig
from key_level_grid.core.types import FalseBreakoutType

__all__ = [
    "SignalFilter",
    "FilterResult",
    "FilterConfig",
    "SignalFilterChain",
    "BreakoutFilter",
    "BreakoutFilterConfig",
    "BreakoutResult",
    "FalseBreakoutType",
    "MACDTrendFilter",
    "RSIFilter",
    "ADXFilter",
    "VolumeFilter",
]
