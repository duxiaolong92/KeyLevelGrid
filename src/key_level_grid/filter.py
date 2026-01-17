"""
向后兼容层: 从 signal.filters 重新导出

请使用新路径: from key_level_grid.signal.filters import SignalFilterChain
"""

from key_level_grid.signal.filters.chain import SignalFilterChain
from key_level_grid.core.config import FilterConfig

__all__ = ["SignalFilterChain", "FilterConfig"]
