"""
向后兼容层: 从 data.feeds 重新导出

请使用新路径: from key_level_grid.data import BinanceKlineFeed
"""

from key_level_grid.data.feeds.binance import BinanceKlineFeed

__all__ = ["BinanceKlineFeed"]
