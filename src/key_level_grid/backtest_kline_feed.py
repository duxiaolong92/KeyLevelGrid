"""
历史K线回放数据源
"""

import time
from typing import Callable, Dict, List, Optional

from key_level_grid.models import Kline, KlineFeedConfig, Timeframe
from key_level_grid.utils.logger import get_logger


class BacktestKlineFeed:
    def __init__(self, config: KlineFeedConfig):
        self.config = config
        self._kline_cache: Dict[Timeframe, List[Kline]] = {}
        self._all_klines: Dict[Timeframe, List[Kline]] = {}
        self._last_update: Dict[Timeframe, int] = {}
        self._running = False
        self._ws_callback: Optional[Callable[[Kline], None]] = None
        self.logger = get_logger(__name__)

    def set_klines(self, timeframe: Timeframe, klines: List[Kline]) -> None:
        sorted_klines = sorted(klines, key=lambda x: x.timestamp)
        self._all_klines[timeframe] = sorted_klines
        self._kline_cache[timeframe] = []
        self._last_update[timeframe] = 0

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def start_ws_subscription(self, callback: Callable[[Kline], None]) -> None:
        self._ws_callback = callback

    async def get_latest_klines(self, timeframe: Timeframe) -> List[Kline]:
        return self._kline_cache.get(timeframe, [])

    def get_cached_klines(self, timeframe: Timeframe) -> List[Kline]:
        return self._kline_cache.get(timeframe, [])

    async def update_latest(self, timeframe: Timeframe) -> None:
        return

    def advance_to(self, timestamp_ms: int) -> None:
        for tf, klines in self._all_klines.items():
            idx = self._find_last_index(klines, timestamp_ms)
            if idx < 0:
                continue
            self._kline_cache[tf] = klines[: idx + 1]
            self._last_update[tf] = int(time.time() * 1000)

    def get_stats(self) -> dict:
        return {
            "symbol": self.config.symbol,
            "primary_timeframe": self.config.primary_timeframe.value,
            "auxiliary_timeframes": [tf.value for tf in self.config.auxiliary_timeframes],
            "cache_sizes": {tf.value: len(v) for tf, v in self._kline_cache.items()},
            "last_updates": {tf.value: ts for tf, ts in self._last_update.items()},
            "running": self._running,
            "ws_connected": False,
        }

    @staticmethod
    def _find_last_index(klines: List[Kline], timestamp_ms: int) -> int:
        left, right = 0, len(klines) - 1
        best = -1
        while left <= right:
            mid = (left + right) // 2
            if klines[mid].timestamp <= timestamp_ms:
                best = mid
                left = mid + 1
            else:
                right = mid - 1
        return best
