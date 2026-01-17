"""
历史K线回放数据源

用于回测场景
"""

import time
from typing import Callable, Dict, List, Optional

from key_level_grid.core.models import Kline, KlineFeedConfig, Timeframe
from key_level_grid.utils.logger import get_logger


class BacktestKlineFeed:
    """
    回测 K 线数据源
    
    功能:
    1. 加载历史 K 线数据
    2. 按时间顺序回放
    3. 模拟实时数据推送
    """
    
    def __init__(self, config: KlineFeedConfig):
        self.config = config
        self._kline_cache: Dict[Timeframe, List[Kline]] = {}
        self._all_klines: Dict[Timeframe, List[Kline]] = {}
        self._last_update: Dict[Timeframe, int] = {}
        self._running = False
        self._ws_callback: Optional[Callable[[Kline], None]] = None
        self.logger = get_logger(__name__)

    def set_klines(self, timeframe: Timeframe, klines: List[Kline]) -> None:
        """设置 K 线数据"""
        sorted_klines = sorted(klines, key=lambda x: x.timestamp)
        self._all_klines[timeframe] = sorted_klines
        self._kline_cache[timeframe] = []
        self._last_update[timeframe] = 0

    async def start(self) -> None:
        """启动数据源"""
        self._running = True

    async def stop(self) -> None:
        """停止数据源"""
        self._running = False

    def start_ws_subscription(self, callback: Callable[[Kline], None]) -> None:
        """设置 K 线收盘回调"""
        self._ws_callback = callback

    async def get_latest_klines(self, timeframe: Timeframe) -> List[Kline]:
        """获取最新 K 线数据"""
        return self._kline_cache.get(timeframe, [])

    def get_cached_klines(self, timeframe: Timeframe) -> List[Kline]:
        """获取缓存的 K 线数据"""
        return self._kline_cache.get(timeframe, [])

    async def update_latest(self, timeframe: Timeframe) -> None:
        """更新最新 K 线（回测模式下不需要）"""
        return

    def advance_to(self, timestamp_ms: int) -> None:
        """
        推进时间到指定时间戳
        
        Args:
            timestamp_ms: 目标时间戳（毫秒）
        """
        for tf, klines in self._all_klines.items():
            idx = self._find_last_index(klines, timestamp_ms)
            if idx < 0:
                continue
            self._kline_cache[tf] = klines[: idx + 1]
            self._last_update[tf] = int(time.time() * 1000)

    def get_stats(self) -> dict:
        """获取统计信息"""
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
        """二分查找最后一个 <= timestamp_ms 的 K 线索引"""
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
