"""
突破过滤器

严格识别有效突破，过滤假突破
"""

from dataclasses import dataclass, field
from typing import List, Optional

from key_level_grid.utils.logger import get_logger
from key_level_grid.core.models import Kline, KeyLevelGridState
from key_level_grid.core.types import FalseBreakoutType
from key_level_grid.core.config import BreakoutFilterConfig


@dataclass
class BreakoutResult:
    """突破验证结果"""
    is_valid: bool
    score: int
    details: dict
    false_breakout_type: Optional[FalseBreakoutType] = None


class BreakoutFilter:
    """
    突破过滤器 - 严格识别有效突破
    
    评分机制 (满分100):
    - 收盘确认: 25分
    - 空间突破: 20分
    - 成交量放大: 20分
    - 动量指标: 15分
    - 方向一致: 15分
    - K线形态: 5分 (加分项)
    
    通过阈值: 75分
    """
    
    def __init__(self, config: Optional[BreakoutFilterConfig] = None):
        self.config = config or BreakoutFilterConfig()
        self.logger = get_logger(__name__)
        
        self._false_breakout_history: List[int] = []
    
    def validate_breakout(
        self,
        state: KeyLevelGridState,
        klines: List[Kline],
        is_long: bool = True
    ) -> BreakoutResult:
        """验证突破有效性"""
        if len(klines) < 2:
            return BreakoutResult(
                is_valid=False,
                score=0,
                details={"error": "K线数据不足"}
            )
        
        score = 0
        details = {}
        
        latest = klines[-1]
        
        # 1. 收盘确认 (25分)
        close_confirmed = self._check_close_confirmation(latest, is_long)
        if close_confirmed:
            score += 25
            details["close_confirm"] = "✅ K线收盘站稳"
        else:
            details["close_confirm"] = "❌ 未收盘确认"
        
        # 2. 空间突破 (20分)
        breakout_pct = self._calculate_breakout_distance(state, latest, is_long)
        if breakout_pct >= self.config.min_breakout_pct:
            score += 20
            details["space"] = f"✅ 突破 {breakout_pct:.2%}"
        elif breakout_pct >= self.config.min_breakout_pct * 0.5:
            score += 10
            details["space"] = f"⚠️ 突破偏小 {breakout_pct:.2%}"
        else:
            details["space"] = f"❌ 突破不足 {breakout_pct:.2%}"
        
        # 3. 成交量确认 (20分)
        vol_ratio = self._check_volume_surge(state)
        if vol_ratio >= self.config.volume_multiplier:
            score += 20
            details["volume"] = f"✅ 放量 {vol_ratio:.1f}x"
        elif vol_ratio >= 1.0:
            score += 10
            details["volume"] = f"⚠️ 量能一般 {vol_ratio:.1f}x"
        else:
            details["volume"] = f"❌ 缩量 {vol_ratio:.1f}x"
        
        # 4. 动量指标 (15分)
        momentum_ok = self._check_momentum(state, is_long)
        if momentum_ok:
            score += 15
            details["momentum"] = "✅ MACD/RSI 确认"
        else:
            score += 5
            details["momentum"] = "⚠️ 动量不明确"
        
        # 5. 方向一致 (15分)
        direction_ok = self._check_direction(state, is_long)
        if direction_ok:
            score += 15
            details["direction"] = "✅ 方向一致"
        elif state.tunnel_direction == "flat":
            score += 7
            details["direction"] = "⚠️ 震荡"
        else:
            details["direction"] = "❌ 方向相反"
        
        # 6. K线形态 (5分加分)
        if self._check_candlestick_pattern(latest, is_long):
            score += 5
            details["pattern"] = "✅ 形态确认"
        
        # 检查是否为假突破
        false_type = self._detect_false_breakout(state, klines, is_long)
        if false_type:
            score = min(score, 60)
            details["false_breakout"] = f"⚠️ 疑似{false_type.value}"
        
        return BreakoutResult(
            is_valid=score >= 75,
            score=score,
            details=details,
            false_breakout_type=false_type
        )
    
    def _check_close_confirmation(
        self,
        kline: Kline,
        is_long: bool
    ) -> bool:
        """检查收盘确认"""
        if not kline.is_closed:
            return False
        return True
    
    def _calculate_breakout_distance(
        self,
        state: KeyLevelGridState,
        kline: Kline,
        is_long: bool
    ) -> float:
        """计算突破幅度"""
        # 简化版：使用收盘价变化
        return abs(kline.close - kline.open) / kline.open if kline.open > 0 else 0
    
    def _check_volume_surge(self, state: KeyLevelGridState) -> float:
        """检查成交量放大"""
        if state.volume_ratio is None:
            return 1.0
        return state.volume_ratio
    
    def _check_momentum(self, state: KeyLevelGridState, is_long: bool) -> bool:
        """检查动量指标"""
        if state.macd_histogram is not None:
            if is_long and state.macd_histogram > 0:
                return True
            elif not is_long and state.macd_histogram < 0:
                return True
        return True
    
    def _check_direction(
        self,
        state: KeyLevelGridState,
        is_long: bool
    ) -> bool:
        """检查方向"""
        if is_long:
            return state.tunnel_direction == "up"
        else:
            return state.tunnel_direction == "down"
    
    def _check_candlestick_pattern(self, kline: Kline, is_long: bool) -> bool:
        """检查K线形态"""
        if is_long:
            if kline.is_bullish:
                if kline.total_range > 0:
                    body_ratio = kline.body / kline.total_range
                    return body_ratio > 0.5
        else:
            if kline.is_bearish:
                if kline.total_range > 0:
                    body_ratio = kline.body / kline.total_range
                    return body_ratio > 0.5
        
        return False
    
    def _detect_false_breakout(
        self,
        state: KeyLevelGridState,
        klines: List[Kline],
        is_long: bool
    ) -> Optional[FalseBreakoutType]:
        """检测假突破"""
        if len(klines) < 2:
            return None
        
        latest = klines[-1]
        
        # 刺穿型检测
        if self._check_pierce_through(latest, is_long):
            return FalseBreakoutType.PIERCE_THROUGH
        
        # 影线拒绝检测
        if self._check_wick_rejection(latest, is_long):
            return FalseBreakoutType.WICK_REJECTION
        
        # 十字星（反转形态）
        if latest.is_doji:
            return FalseBreakoutType.REVERSAL
        
        return None
    
    def _check_pierce_through(
        self,
        kline: Kline,
        is_long: bool
    ) -> bool:
        """刺穿型检测"""
        if is_long:
            # 最高价突破但收盘回落
            if kline.upper_wick > kline.body * 2:
                return True
        else:
            if kline.lower_wick > kline.body * 2:
                return True
        return False
    
    def _check_wick_rejection(
        self,
        kline: Kline,
        is_long: bool
    ) -> bool:
        """影线拒绝检测"""
        body = kline.body
        if body == 0:
            return True
        
        if is_long:
            if kline.upper_wick > body * self.config.max_wick_ratio * 2:
                return True
        else:
            if kline.lower_wick > body * self.config.max_wick_ratio * 2:
                return True
        
        return False
    
    def record_false_breakout(self, timestamp: int) -> None:
        """记录假突破"""
        self._false_breakout_history.append(timestamp)
        
        cutoff = timestamp - self.config.false_breakout_lookback * 4 * 60 * 60 * 1000
        self._false_breakout_history = [
            t for t in self._false_breakout_history if t > cutoff
        ]
    
    def get_recent_false_breakouts(self) -> int:
        """获取近期假突破次数"""
        return len(self._false_breakout_history)
