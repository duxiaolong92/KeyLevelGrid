"""
K 线数据源模块 (LEVEL_GENERATION.md v3.1.0)

支持多个交易所和回测数据源

V3.0 新增:
- MTFKlineFeed: 多时间框架 K 线管理 + 一致性锁
"""

from .binance import BinanceKlineFeed
from .gate import GateKlineFeed
from .polygon import PolygonKlineFeed
from .backtest import BacktestKlineFeed
from .mtf_feed import MTFKlineFeed, MTFKlineFeedFactory, MTFKlineData

__all__ = [
    "BinanceKlineFeed",
    "GateKlineFeed",
    "PolygonKlineFeed",
    "BacktestKlineFeed",
    # V3.0 新增
    "MTFKlineFeed",
    "MTFKlineFeedFactory",
    "MTFKlineData",
]
