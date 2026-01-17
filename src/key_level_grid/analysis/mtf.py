"""
多周期趋势分析模块

管理不同周期的K线数据，计算趋势并检查多周期共振
"""

from typing import Dict, List, Optional, Tuple

from key_level_grid.utils.logger import get_logger
from key_level_grid.core.models import (
    Kline,
    Timeframe,
    TimeframeTrend,
    KeyLevelGridState,
)


class MultiTimeframeManager:
    """
    多周期数据管理器
    
    功能:
    1. 获取多周期K线数据
    2. 计算各周期趋势方向
    3. 检查多周期趋势一致性
    """
    
    def __init__(
        self,
        kline_feed,
        indicator = None,
        trend_lookback: int = 20
    ):
        """
        初始化多周期管理器
        
        Args:
            kline_feed: K线数据源
            indicator: 指标计算器 (可选)
            trend_lookback: 趋势判断回看K线数
        """
        self.kline_feed = kline_feed
        self.indicator = indicator
        self.trend_lookback = trend_lookback
        self.config = kline_feed.config
        self.logger = get_logger(__name__)
    
    def set_indicator(self, indicator) -> None:
        """设置指标计算器"""
        self.indicator = indicator
    
    async def get_all_timeframes(self) -> Dict[Timeframe, List[Kline]]:
        """获取所有周期的K线数据"""
        result = {}
        
        result[self.config.primary_timeframe] = await self.kline_feed.get_latest_klines(
            self.config.primary_timeframe
        )
        
        for tf in self.config.auxiliary_timeframes:
            result[tf] = await self.kline_feed.get_latest_klines(tf)
        
        return result
    
    def calculate_trend(self, klines: List[Kline], lookback: int = 20) -> str:
        """
        计算趋势方向
        
        使用简单的高低点判断:
        - 最近N根K线: 高点走高 + 低点走高 = 上升趋势
        - 最近N根K线: 高点走低 + 低点走低 = 下降趋势
        - 其他 = 震荡
        
        Returns:
            "up" | "down" | "ranging" | "unknown"
        """
        if len(klines) < lookback:
            return "unknown"
        
        recent = klines[-lookback:]
        
        half = lookback // 2
        first_half = recent[:half]
        second_half = recent[half:]
        
        first_high = max(k.high for k in first_half)
        first_low = min(k.low for k in first_half)
        second_high = max(k.high for k in second_half)
        second_low = min(k.low for k in second_half)
        
        higher_highs = second_high > first_high
        higher_lows = second_low > first_low
        lower_highs = second_high < first_high
        lower_lows = second_low < first_low
        
        if higher_highs and higher_lows:
            return "up"
        elif lower_highs and lower_lows:
            return "down"
        else:
            return "ranging"
    
    async def calculate_timeframe_trend(
        self,
        timeframe: Timeframe
    ) -> TimeframeTrend:
        """计算单个周期的趋势状态"""
        klines = await self.kline_feed.get_latest_klines(
            timeframe,
            count=200
        )
        
        if len(klines) < 50:
            return TimeframeTrend(
                timeframe=timeframe,
                trend="unknown",
                price_position="unknown",
                confidence=0
            )
        
        trend = self.calculate_trend(klines, self.trend_lookback)
        
        return TimeframeTrend(
            timeframe=timeframe,
            trend=trend,
            price_position="middle",
            confidence=50
        )
    
    async def check_alignment(
        self,
        signal_direction: str
    ) -> Tuple[bool, Dict[Timeframe, TimeframeTrend]]:
        """
        检查多周期是否共振
        
        Args:
            signal_direction: 信号方向 "long" | "short"
        
        Returns:
            (是否共振, 各周期趋势状态)
        """
        trends: Dict[Timeframe, TimeframeTrend] = {}
        
        primary_trend = await self.calculate_timeframe_trend(
            self.config.primary_timeframe
        )
        trends[self.config.primary_timeframe] = primary_trend
        
        for tf in self.config.auxiliary_timeframes:
            trend = await self.calculate_timeframe_trend(tf)
            trends[tf] = trend
        
        aligned = self._check_trend_alignment(trends, signal_direction)
        
        trend_str = ", ".join([
            f"{tf.value}:{t.trend}({t.confidence:.0f}%)"
            for tf, t in trends.items()
        ])
        self.logger.info(
            f"多周期共振检查: 方向={signal_direction}, "
            f"趋势=[{trend_str}], 结果={'✅ 共振' if aligned else '❌ 不共振'}"
        )
        
        return aligned, trends
    
    def _check_trend_alignment(
        self,
        trends: Dict[Timeframe, TimeframeTrend],
        signal_direction: str
    ) -> bool:
        """检查趋势是否与信号方向一致"""
        expected_trend = "up" if signal_direction == "long" else "down"
        
        for tf, trend_info in trends.items():
            if trend_info.trend == "ranging":
                return False
            
            if trend_info.trend != expected_trend:
                return False
            
            if trend_info.confidence < 40:
                return False
        
        return True
    
    def check_trend_alignment_sync(
        self,
        primary_trend: str,
        auxiliary_trends: Dict[Timeframe, str]
    ) -> bool:
        """检查多周期趋势是否一致（同步版本）"""
        if primary_trend == "ranging":
            return False
        
        for tf, trend in auxiliary_trends.items():
            if trend != primary_trend:
                return False
        
        return True
