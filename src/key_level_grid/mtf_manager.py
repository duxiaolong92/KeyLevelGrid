"""
向后兼容层: 从 analysis.mtf 重新导出

请使用新路径: from key_level_grid.analysis import MultiTimeframeManager
"""

from key_level_grid.analysis.mtf import MultiTimeframeManager

__all__ = ["MultiTimeframeManager"]
