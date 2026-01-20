"""
MTF K 线数据源 (LEVEL_GENERATION.md v3.2.5)

多时间框架 K 线管理 - 四层级系统:
- L1 战略层: 1w/3d (可选)
- L2 骨架层: 1d (必须)
- L3 中继层: 4h (必须)
- L4 战术层: 15m (可选)

- 实现 is_synced() 一致性锁
- 确保评分计算前数据对齐
"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from key_level_grid.core.triggers import (
    KlineSyncStatus,
    KLINE_INTERVAL_MS,
    DEFAULT_MAX_KLINE_LAG,
    TimeframeLayerConfig,
)


logger = logging.getLogger(__name__)


# 四层级系统配置常量
LAYER_HIERARCHY = ["l1", "l2", "l3", "l4"]
DEFAULT_LAYER_INTERVALS = {
    "l1": "1w",   # 战略层
    "l2": "1d",   # 骨架层
    "l3": "4h",   # 中继层
    "l4": "15m",  # 战术层
}
REQUIRED_LAYERS = ["l2", "l3"]  # L2 和 L3 为必须层


@dataclass
class MTFKlineData:
    """多时间框架 K 线数据"""
    timeframe: str
    layer: Optional[str] = None  # 所属层级: "l1", "l2", "l3", "l4"
    klines: List[Dict] = field(default_factory=list)
    last_update_ts: int = 0  # 最后更新时间戳 (秒)
    last_close_time: int = 0  # 最新 K 线闭合时间 (毫秒)


class MTFKlineFeed:
    """
    多时间框架 K 线数据源 (V3.2.5)
    
    核心职责:
    1. 管理四层级时间框架的 K 线数据
    2. 提供 is_synced() 一致性检查
    3. 确保 LevelCalculator 使用对齐的数据
    4. 支持 L1 层 3d 降级功能
    """
    
    def __init__(
        self,
        timeframes: Optional[List[str]] = None,
        max_lag_sec: Optional[Dict[str, int]] = None,
        config: Optional[Dict] = None,
        layer_configs: Optional[Dict[str, TimeframeLayerConfig]] = None,
    ):
        """
        初始化 MTF K 线数据源
        
        Args:
            timeframes: 时间框架列表 ["1w", "1d", "4h", "15m"]
            max_lag_sec: 最大允许延迟 (秒)
            config: 配置字典
            layer_configs: 层级配置 (V3.2.5)
        """
        self.config = config or {}
        level_gen_config = self.config.get("level_generation", {})
        kline_sync_config = level_gen_config.get("kline_sync", {})
        
        # 初始化层级配置
        self.layer_configs = layer_configs or self._load_layer_configs(level_gen_config)
        
        # 从层级配置构建时间框架列表
        self.timeframes = timeframes or self._get_enabled_timeframes()
        
        self.max_lag_sec = max_lag_sec or kline_sync_config.get(
            "max_lag", 
            DEFAULT_MAX_KLINE_LAG
        )
        
        # K 线数据存储
        self._data: Dict[str, MTFKlineData] = {}
        
        # 同步状态
        self._sync_status: Dict[str, KlineSyncStatus] = {}
        
        # L1 降级标志
        self._l1_fallback_active: bool = False
        
        logger.info(f"MTFKlineFeed 初始化: timeframes={self.timeframes}")
    
    def _load_layer_configs(self, config: Dict) -> Dict[str, TimeframeLayerConfig]:
        """从配置加载层级配置"""
        layer_configs = {}
        tf_config = config.get("timeframes", {})
        
        # L1 战略层
        l1_config = tf_config.get("l1_strategy", {})
        layer_configs["l1"] = TimeframeLayerConfig.from_dict(l1_config, "l1")
        
        # L2 骨架层
        l2_config = tf_config.get("l2_skeleton", {})
        layer_configs["l2"] = TimeframeLayerConfig.from_dict(l2_config, "l2")
        layer_configs["l2"].enabled = True  # L2 必须启用
        
        # L3 中继层
        l3_config = tf_config.get("l3_relay", {})
        layer_configs["l3"] = TimeframeLayerConfig.from_dict(l3_config, "l3")
        layer_configs["l3"].enabled = True  # L3 必须启用
        
        # L4 战术层
        l4_config = tf_config.get("l4_tactical", {})
        layer_configs["l4"] = TimeframeLayerConfig.from_dict(l4_config, "l4")
        
        return layer_configs
    
    def _get_enabled_timeframes(self) -> List[str]:
        """获取已启用的时间框架列表"""
        timeframes = []
        for layer in LAYER_HIERARCHY:
            cfg = self.layer_configs.get(layer)
            if cfg and cfg.enabled:
                timeframes.append(cfg.interval)
        return timeframes
    
    def get_layer_for_timeframe(self, timeframe: str) -> Optional[str]:
        """获取时间框架对应的层级"""
        for layer, cfg in self.layer_configs.items():
            if cfg.interval == timeframe:
                return layer
            # 检查降级配置
            if cfg.fallback_interval == timeframe:
                return layer
        return None
    
    def get_timeframe_for_layer(self, layer: str) -> Optional[str]:
        """获取层级对应的时间框架"""
        cfg = self.layer_configs.get(layer)
        if not cfg or not cfg.enabled:
            return None
        
        # 检查 L1 降级
        if layer == "l1" and self._l1_fallback_active and cfg.fallback_interval:
            return cfg.fallback_interval
        
        return cfg.interval
    
    def get_layer_config(self, layer: str) -> Optional[TimeframeLayerConfig]:
        """获取层级配置"""
        return self.layer_configs.get(layer)
    
    def is_layer_enabled(self, layer: str) -> bool:
        """检查层级是否启用"""
        cfg = self.layer_configs.get(layer)
        return cfg.enabled if cfg else False
    
    def update(self, timeframe: str, klines: List[Dict]) -> None:
        """
        更新指定时间框架的 K 线数据
        
        Args:
            timeframe: 时间框架 "1w" | "3d" | "1d" | "4h" | "15m"
            klines: K 线数据列表
        """
        if not klines:
            return
        
        # 获取最新 K 线的闭合时间
        last_kline = klines[-1]
        last_close_time = int(last_kline.get("close_time", 0) or last_kline.get("timestamp", 0))
        
        layer = self.get_layer_for_timeframe(timeframe)
        
        self._data[timeframe] = MTFKlineData(
            timeframe=timeframe,
            layer=layer,
            klines=klines,
            last_update_ts=int(time.time()),
            last_close_time=last_close_time,
        )
        
        # 更新同步状态
        self._update_sync_status(timeframe)
        
        logger.debug(f"Updated {timeframe} (layer={layer}) klines: {len(klines)} bars, last_close={last_close_time}")
    
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
    
    def get_by_layer(self, layer: str) -> Optional[List[Dict]]:
        """
        按层级获取 K 线数据
        
        Args:
            layer: 层级 "l1" | "l2" | "l3" | "l4"
        
        Returns:
            K 线列表或 None
        """
        timeframe = self.get_timeframe_for_layer(layer)
        if not timeframe:
            return None
        return self.get(timeframe)
    
    def get_all(self) -> Dict[str, List[Dict]]:
        """
        获取所有时间框架的 K 线数据
        
        Returns:
            {"1w": [...], "1d": [...], "4h": [...], "15m": [...]}
        """
        return {
            tf: data.klines 
            for tf, data in self._data.items()
        }
    
    def get_all_by_layer(self) -> Dict[str, List[Dict]]:
        """
        按层级获取所有 K 线数据
        
        Returns:
            {"l1": [...], "l2": [...], "l3": [...], "l4": [...]}
        """
        result = {}
        for layer in LAYER_HIERARCHY:
            klines = self.get_by_layer(layer)
            if klines:
                result[layer] = klines
        return result
    
    def is_synced(self) -> bool:
        """
        检查所有时间框架是否同步
        
        同步条件:
        1. 必须层 (L2, L3) 都有数据
        2. 每个时间框架的最新 K 线闭合时间 <= 允许延迟
        3. 时间框架间逻辑对齐
        
        Returns:
            True if all required timeframes are synced
        """
        # 检查必须层的数据完整性
        for layer in REQUIRED_LAYERS:
            cfg = self.layer_configs.get(layer)
            if not cfg or not cfg.enabled:
                continue
            
            tf = self.get_timeframe_for_layer(layer)
            if tf and tf not in self._data:
                logger.debug(f"Sync check failed: missing {layer} ({tf}) data")
                return False
        
        # 检查已启用时间框架的延迟
        for tf in self.timeframes:
            if tf not in self._data:
                continue
                
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
    
    def try_l1_fallback(self) -> bool:
        """
        尝试 L1 层降级 (1w → 3d)
        
        当 1w 数据不足时，自动降级到 3d
        
        Returns:
            True if fallback was activated
        """
        l1_config = self.layer_configs.get("l1")
        if not l1_config or not l1_config.enabled:
            return False
        
        if not l1_config.use_fallback or not l1_config.fallback_interval:
            return False
        
        # 检查 1w 数据是否可用
        if l1_config.interval in self._data:
            data = self._data[l1_config.interval]
            if len(data.klines) >= min(l1_config.fib_lookback):
                # 数据足够，不需要降级
                self._l1_fallback_active = False
                return False
        
        # 激活降级
        self._l1_fallback_active = True
        logger.info(f"L1 层降级激活: {l1_config.interval} → {l1_config.fallback_interval}")
        
        # 更新时间框架列表
        self.timeframes = self._get_enabled_timeframes()
        
        return True
    
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
        - 小周期的最新 K 线应该在大周期的最新 K 线之后或同时
        - 允许一定容差 (2 个周期)
        
        Returns:
            True if aligned
        """
        # 获取各时间框架的最新闭合时间
        close_times = {}
        for tf, data in self._data.items():
            close_times[tf] = data.last_close_time
        
        if len(close_times) < 2:
            return True  # 不足 2 个时间框架时不检查
        
        # 按周期从大到小排序
        tf_order = ["1w", "3d", "1d", "4h", "15m"]
        sorted_tfs = [tf for tf in tf_order if tf in close_times]
        
        # 检查对齐: 大周期的闭合时间不能领先小周期太多
        for i in range(len(sorted_tfs) - 1):
            larger_tf = sorted_tfs[i]
            smaller_tf = sorted_tfs[i + 1]
            
            larger_interval = KLINE_INTERVAL_MS.get(larger_tf, 0)
            diff = close_times[larger_tf] - close_times[smaller_tf]
            
            # 允许差异最多为 2 个大周期
            if diff > 2 * larger_interval:
                logger.debug(f"Alignment failed: {larger_tf} leads {smaller_tf} by {diff}ms")
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
    MTFKlineFeed 工厂 (V3.2.5)
    
    集成现有的 KlineFeed 实现
    """
    
    @staticmethod
    def create_from_config(config: Dict) -> MTFKlineFeed:
        """
        从配置创建 MTFKlineFeed
        
        Args:
            config: 完整配置字典
        
        Returns:
            MTFKlineFeed 实例
        """
        return MTFKlineFeed(config=config)
    
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
