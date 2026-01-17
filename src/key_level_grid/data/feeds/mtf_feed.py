"""
MTF K 线数据源 (LEVEL_GENERATION.md v3.1.0)

多时间框架 K 线管理:
- 统一管理 1d, 4h, 15m 多周期数据
- 实现 is_synced() 一致性锁
- 确保评分计算前数据对齐
"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from key_level_grid.core.triggers import (
    KlineSyncStatus,
    KLINE_INTERVAL_MS,
    DEFAULT_MAX_KLINE_LAG,
)


logger = logging.getLogger(__name__)


@dataclass
class MTFKlineData:
    """多时间框架 K 线数据"""
    timeframe: str
    klines: List[Dict]
    last_update_ts: int  # 最后更新时间戳 (秒)
    last_close_time: int  # 最新 K 线闭合时间 (毫秒)


class MTFKlineFeed:
    """
    多时间框架 K 线数据源
    
    核心职责:
    1. 管理多个时间框架的 K 线数据
    2. 提供 is_synced() 一致性检查
    3. 确保 LevelCalculator 使用对齐的数据
    """
    
    def __init__(
        self,
        timeframes: Optional[List[str]] = None,
        max_lag_sec: Optional[Dict[str, int]] = None,
        config: Optional[Dict] = None,
    ):
        """
        初始化 MTF K 线数据源
        
        Args:
            timeframes: 时间框架列表 ["1d", "4h", "15m"]
            max_lag_sec: 最大允许延迟 (秒) {"1d": 300, "4h": 60, "15m": 30}
            config: 配置字典
        """
        self.config = config or {}
        kline_sync_config = self.config.get("level_generation", {}).get("kline_sync", {})
        
        self.timeframes = timeframes or ["1d", "4h", "15m"]
        self.max_lag_sec = max_lag_sec or kline_sync_config.get(
            "max_lag_sec", 
            DEFAULT_MAX_KLINE_LAG
        )
        
        # K 线数据存储
        self._data: Dict[str, MTFKlineData] = {}
        
        # 同步状态
        self._sync_status: Dict[str, KlineSyncStatus] = {}
    
    def update(self, timeframe: str, klines: List[Dict]) -> None:
        """
        更新指定时间框架的 K 线数据
        
        Args:
            timeframe: 时间框架 "1d" | "4h" | "15m"
            klines: K 线数据列表
        """
        if not klines:
            return
        
        # 获取最新 K 线的闭合时间
        last_kline = klines[-1]
        last_close_time = int(last_kline.get("close_time", 0) or last_kline.get("timestamp", 0))
        
        self._data[timeframe] = MTFKlineData(
            timeframe=timeframe,
            klines=klines,
            last_update_ts=int(time.time()),
            last_close_time=last_close_time,
        )
        
        # 更新同步状态
        self._update_sync_status(timeframe)
        
        logger.debug(f"Updated {timeframe} klines: {len(klines)} bars, last_close={last_close_time}")
    
    def get(self, timeframe: str) -> Optional[List[Dict]]:
        """
        获取指定时间框架的 K 线数据
        
        Args:
            timeframe: 时间框架
        
        Returns:
            K 线列表或 None
        """
        data = self._data.get(timeframe)
        return data.klines if data else None
    
    def get_all(self) -> Dict[str, List[Dict]]:
        """
        获取所有时间框架的 K 线数据
        
        Returns:
            {"1d": [...], "4h": [...], "15m": [...]}
        """
        return {
            tf: data.klines 
            for tf, data in self._data.items()
        }
    
    def is_synced(self) -> bool:
        """
        检查所有时间框架是否同步
        
        同步条件:
        1. 所有时间框架都有数据
        2. 每个时间框架的最新 K 线闭合时间 <= 允许延迟
        3. 时间框架间逻辑对齐 (4h 必须是 1d 的子集)
        
        Returns:
            True if all timeframes are synced
        """
        # 检查数据完整性
        for tf in self.timeframes:
            if tf not in self._data:
                logger.debug(f"Sync check failed: missing {tf} data")
                return False
        
        # 检查每个时间框架的延迟
        for tf in self.timeframes:
            status = self._sync_status.get(tf)
            if status and status.is_stale:
                logger.debug(f"Sync check failed: {tf} is stale (lag={status.lag_seconds}s)")
                return False
        
        # 检查逻辑对齐
        if not self._check_logical_alignment():
            logger.debug("Sync check failed: logical alignment check failed")
            return False
        
        return True
    
    def get_sync_status(self) -> Dict[str, KlineSyncStatus]:
        """
        获取所有时间框架的同步状态
        
        Returns:
            {"1d": KlineSyncStatus, ...}
        """
        return self._sync_status.copy()
    
    def get_stale_timeframes(self) -> List[str]:
        """
        获取过期的时间框架列表
        
        Returns:
            过期的时间框架列表
        """
        stale = []
        for tf, status in self._sync_status.items():
            if status.is_stale:
                stale.append(tf)
        return stale
    
    def _update_sync_status(self, timeframe: str) -> None:
        """
        更新指定时间框架的同步状态
        
        Args:
            timeframe: 时间框架
        """
        data = self._data.get(timeframe)
        if not data:
            return
        
        now_ms = int(time.time() * 1000)
        interval_ms = KLINE_INTERVAL_MS.get(timeframe, 4 * 60 * 60 * 1000)
        
        # 计算预期的闭合时间
        # 当前时间向下取整到最近的 K 线边界
        expected_close_time = (now_ms // interval_ms) * interval_ms
        
        # 计算延迟
        lag_ms = now_ms - data.last_close_time
        lag_seconds = int(lag_ms / 1000)
        
        # 判断是否过期
        max_lag = self.max_lag_sec.get(timeframe, 60)
        is_stale = lag_seconds > max_lag + (interval_ms // 1000)
        
        self._sync_status[timeframe] = KlineSyncStatus(
            timeframe=timeframe,
            last_close_time=data.last_close_time,
            expected_close_time=expected_close_time,
            is_stale=is_stale,
            lag_seconds=lag_seconds,
        )
    
    def _check_logical_alignment(self) -> bool:
        """
        检查时间框架间的逻辑对齐
        
        规则:
        - 4h 的最新 K 线应该在 1d 的最新 K 线之后或同时
        - 15m 的最新 K 线应该在 4h 的最新 K 线之后或同时
        
        Returns:
            True if aligned
        """
        # 获取各时间框架的最新闭合时间
        close_times = {}
        for tf in self.timeframes:
            data = self._data.get(tf)
            if data:
                close_times[tf] = data.last_close_time
        
        if not close_times:
            return True  # 没有数据时不检查
        
        # 检查对齐
        # 1d > 4h > 15m (按粒度递减)
        if "1d" in close_times and "4h" in close_times:
            # 4h 闭合时间不能早于 1d 闭合时间太多
            diff = close_times["1d"] - close_times["4h"]
            if diff > 24 * 60 * 60 * 1000:  # 1 天
                return False
        
        if "4h" in close_times and "15m" in close_times:
            diff = close_times["4h"] - close_times["15m"]
            if diff > 4 * 60 * 60 * 1000:  # 4 小时
                return False
        
        return True
    
    def wait_for_sync(
        self,
        timeout_sec: int = 60,
        fetch_callback: Optional[callable] = None,
    ) -> bool:
        """
        等待数据同步
        
        Args:
            timeout_sec: 超时时间 (秒)
            fetch_callback: 数据获取回调 (用于触发重新获取)
        
        Returns:
            True if synced within timeout
        """
        start = time.time()
        
        while time.time() - start < timeout_sec:
            if self.is_synced():
                return True
            
            # 触发重新获取
            if fetch_callback:
                stale = self.get_stale_timeframes()
                for tf in stale:
                    try:
                        fetch_callback(tf)
                    except Exception as e:
                        logger.warning(f"Failed to fetch {tf}: {e}")
            
            time.sleep(1)
        
        return False


class MTFKlineFeedFactory:
    """
    MTFKlineFeed 工厂
    
    集成现有的 KlineFeed 实现
    """
    
    @staticmethod
    def create_from_feed(
        base_feed,
        timeframes: List[str],
        config: Optional[Dict] = None,
    ) -> MTFKlineFeed:
        """
        从现有 KlineFeed 创建 MTFKlineFeed
        
        Args:
            base_feed: 基础 KlineFeed 实例
            timeframes: 时间框架列表
            config: 配置字典
        
        Returns:
            MTFKlineFeed 实例
        """
        mtf_feed = MTFKlineFeed(
            timeframes=timeframes,
            config=config,
        )
        
        # TODO: 集成 base_feed 的数据更新逻辑
        
        return mtf_feed
