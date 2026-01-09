"""
多周期数据管理器

管理不同周期的K线数据，计算趋势并检查多周期共振
"""

from typing import Dict, List, Optional, Tuple

from key_level_grid.utils.logger import get_logger
from key_level_grid.models import (
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
        kline_feed: "BinanceKlineFeed",
        indicator: Optional["KeyLevelGridIndicator"] = None,
        trend_lookback: int = 20
    ):
        """
        初始化多周期管理器
        
        Args:
            kline_feed: K线数据源
            indicator: 指标计算器 (可选，用于计算 EMA)
            trend_lookback: 趋势判断回看K线数
        """
        self.kline_feed = kline_feed
        self.indicator = indicator
        self.trend_lookback = trend_lookback
        self.config = kline_feed.config
        self.logger = get_logger(__name__)
    
    def set_indicator(self, indicator: "KeyLevelGridIndicator") -> None:
        """设置指标计算器"""
        self.indicator = indicator
    
    async def get_all_timeframes(self) -> Dict[Timeframe, List[Kline]]:
        """
        获取所有周期的K线数据
        
        Returns:
            {
                Timeframe.H4: [Kline, ...],
                Timeframe.D1: [Kline, ...]
            }
        """
        result = {}
        
        # 主周期
        result[self.config.primary_timeframe] = await self.kline_feed.get_latest_klines(
            self.config.primary_timeframe
        )
        
        # 辅助周期
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
        
        Args:
            klines: K线列表
            lookback: 回看K线数
            
        Returns:
            "up" | "down" | "ranging" | "unknown"
        """
        if len(klines) < lookback:
            return "unknown"
        
        recent = klines[-lookback:]
        
        # 分成两半比较
        half = lookback // 2
        first_half = recent[:half]
        second_half = recent[half:]
        
        first_high = max(k.high for k in first_half)
        first_low = min(k.low for k in first_half)
        second_high = max(k.high for k in second_half)
        second_low = min(k.low for k in second_half)
        
        # 判断趋势
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
        """
        计算单个周期的趋势状态
        
        Args:
            timeframe: 时间周期
            
        Returns:
            TimeframeTrend 对象
        """
        # 获取K线数据
        klines = await self.kline_feed.get_latest_klines(
            timeframe,
            count=200  # 足够计算 EMA 169
        )
        
        if len(klines) < 169:
            return TimeframeTrend(
                timeframe=timeframe,
                trend="unknown",
                ema_144=0,
                ema_169=0,
                price_position="unknown",
                tunnel_direction="unknown",
                confidence=0
            )
        
        # 计算关键位网格
        if self.indicator:
            state = self.indicator.calculate(klines)
            trend = self._determine_trend(state, klines)
            confidence = self._calculate_confidence(state, klines)
            
            return TimeframeTrend(
                timeframe=timeframe,
                trend=trend,
                ema_144=state.ema_144,
                ema_169=state.ema_169,
                price_position=state.price_position,
                tunnel_direction=state.tunnel_direction,
                confidence=confidence
            )
        else:
            # 没有指标计算器，使用简单趋势判断
            trend = self.calculate_trend(klines, self.trend_lookback)
            return TimeframeTrend(
                timeframe=timeframe,
                trend=trend,
                ema_144=0,
                ema_169=0,
                price_position="unknown",
                tunnel_direction="unknown",
                confidence=50
            )
    
    def _determine_trend(
        self,
        state: KeyLevelGridState,
        klines: List[Kline]
    ) -> str:
        """
        判断趋势方向
        
        综合考虑:
        1. 价格相对通道位置 (权重 40%)
        2. 通道方向 (权重 30%)
        3. 价格走势 (权重 30%)
        
        Returns:
            "up" | "down" | "ranging"
        """
        score = 0
        
        # 1. 价格位置 (±40分)
        if state.price_position == "above":
            score += 40
        elif state.price_position == "below":
            score -= 40
        # "in" 通道内不加分
        
        # 2. 通道方向 (±30分)
        if state.tunnel_direction == "up":
            score += 30
        elif state.tunnel_direction == "down":
            score -= 30
        
        # 3. 价格走势 (±30分) - 用最近K线判断
        lookback = min(self.trend_lookback, len(klines))
        if lookback >= 10:
            recent = klines[-lookback:]
            half = lookback // 2
            first_half_close = sum(k.close for k in recent[:half]) / half
            second_half_close = sum(k.close for k in recent[half:]) / (lookback - half)
            
            if second_half_close > first_half_close * 1.01:  # 上涨 > 1%
                score += 30
            elif second_half_close < first_half_close * 0.99:  # 下跌 > 1%
                score -= 30
        
        # 判定趋势
        if score >= 50:
            return "up"
        elif score <= -50:
            return "down"
        else:
            return "ranging"
    
    def _calculate_confidence(
        self,
        state: KeyLevelGridState,
        klines: List[Kline]
    ) -> float:
        """
        计算趋势置信度
        
        置信度越高，趋势越明确
        
        Returns:
            0-100 的置信度值
        """
        confidence = 50.0  # 基础置信度
        
        # 通道宽度: 越宽越有趋势
        if state.tunnel_width > 0.03:  # > 3%
            confidence += 20
        elif state.tunnel_width > 0.02:
            confidence += 10
        
        # 斜率: 越陡峭越有趋势
        if abs(state.tunnel_slope) > 0.01:
            confidence += 15
        elif abs(state.tunnel_slope) > 0.005:
            confidence += 5
        
        # 价格距离通道: 越远越确定
        if state.distance_from_tunnel > 0.05:  # > 5%
            confidence += 15
        elif state.distance_from_tunnel > 0.02:
            confidence += 5
        
        return min(100.0, confidence)
    
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
        
        # 1. 获取主周期趋势
        primary_trend = await self.calculate_timeframe_trend(
            self.config.primary_timeframe
        )
        trends[self.config.primary_timeframe] = primary_trend
        
        # 2. 获取辅助周期趋势
        for tf in self.config.auxiliary_timeframes:
            trend = await self.calculate_timeframe_trend(tf)
            trends[tf] = trend
        
        # 3. 检查是否一致
        aligned = self._check_trend_alignment(trends, signal_direction)
        
        # 日志
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
        """
        检查趋势是否与信号方向一致
        
        Args:
            trends: 各周期趋势
            signal_direction: "long" | "short"
            
        Returns:
            是否一致
        """
        expected_trend = "up" if signal_direction == "long" else "down"
        
        for tf, trend_info in trends.items():
            # 震荡视为不一致
            if trend_info.trend == "ranging":
                return False
            
            # 趋势方向不一致
            if trend_info.trend != expected_trend:
                return False
            
            # 置信度太低 (< 40%)
            if trend_info.confidence < 40:
                return False
        
        return True
    
    def check_trend_alignment_sync(
        self,
        primary_trend: str,
        auxiliary_trends: Dict[Timeframe, str]
    ) -> bool:
        """
        检查多周期趋势是否一致 (同步版本)
        
        规则:
        - 主周期趋势必须与所有辅助周期一致
        - 震荡视为不一致
        
        Args:
            primary_trend: 主周期趋势
            auxiliary_trends: 辅助周期趋势
            
        Returns:
            是否一致
        """
        if primary_trend == "ranging":
            return False
        
        for tf, trend in auxiliary_trends.items():
            if trend != primary_trend:
                return False
        
        return True

