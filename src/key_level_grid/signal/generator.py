"""
信号生成模块

生成突破信号和回踩信号
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from key_level_grid.utils.logger import get_logger
from key_level_grid.core.models import Kline, KeyLevelGridState
from key_level_grid.core.types import SignalType, SignalGrade
from key_level_grid.core.config import SignalConfig


@dataclass
class KeyLevelSignal:
    """关键位网格交易信号"""
    signal_id: str
    signal_type: SignalType
    symbol: str
    timestamp: int
    
    # 价格信息
    current_price: float
    entry_price: float                    # 建议入场价
    stop_loss: float                      # 止损价
    take_profits: List[float] = field(default_factory=list)  # 止盈价列表
    
    # 信号质量
    confidence: float = 0.0               # 置信度 (0-100)
    score: int = 0                        # 综合评分
    grade: SignalGrade = SignalGrade.C    # 信号等级
    
    # 触发条件
    trigger_reason: str = ""
    market_state: Optional[KeyLevelGridState] = None
    
    # 过滤状态
    filters_passed: List[str] = field(default_factory=list)
    filters_failed: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "signal_id": self.signal_id,
            "signal_type": self.signal_type.value,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "current_price": self.current_price,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profits": self.take_profits,
            "confidence": self.confidence,
            "score": self.score,
            "grade": self.grade.value,
            "trigger_reason": self.trigger_reason,
            "filters_passed": self.filters_passed,
            "filters_failed": self.filters_failed,
        }


class KeyLevelSignalGenerator:
    """
    关键位网格信号生成器
    
    信号类型:
    1. 突破做多: 价格收盘突破阻力位
    2. 突破做空: 价格收盘跌破支撑位
    3. 回踩做多: 价格在通道上方，回调触及支撑后反弹
    4. 回踩做空: 价格在通道下方，反弹触及阻力后回落
    """
    
    def __init__(self, config: Optional[SignalConfig] = None, symbol: str = ""):
        self.config = config or SignalConfig()
        self.symbol = symbol
        self.logger = get_logger(__name__)
        
        # 历史状态
        self._prev_state: Optional[KeyLevelGridState] = None
        self._prev_klines: List[Kline] = []
        
        # 冷却管理
        self._last_signal_time: int = 0
    
    def generate(
        self,
        current_state: KeyLevelGridState,
        klines: List[Kline]
    ) -> Optional[KeyLevelSignal]:
        """生成交易信号"""
        # 冷却期检查
        if self._is_in_cooldown():
            return None
        
        signal = None
        
        # 检测突破信号
        signal = self._check_breakout(current_state, klines)
        
        # 如果没有突破，检测回踩信号
        if signal is None and self.config.pullback_enabled:
            signal = self._check_pullback(current_state, klines)
        
        # 更新历史状态
        self._prev_state = current_state
        self._prev_klines = klines[-10:] if len(klines) > 10 else klines.copy()
        
        if signal is not None:
            self._last_signal_time = int(time.time() * 1000)
            self.logger.info(
                f"生成信号: {signal.signal_type.value}, "
                f"价格={signal.current_price}, "
                f"评分={signal.score}, 等级={signal.grade.value}"
            )
        
        return signal
    
    def _check_breakout(
        self,
        state: KeyLevelGridState,
        klines: List[Kline]
    ) -> Optional[KeyLevelSignal]:
        """检测突破信号"""
        if self._prev_state is None:
            return None
        
        latest = klines[-1] if klines else None
        if not latest:
            return None
        
        # 检测向上突破
        if self._is_bullish_breakout(state, latest):
            return self._create_breakout_signal(
                SignalType.BREAKOUT_LONG,
                state,
                latest
            )
        
        # 检测向下突破
        if self._is_bearish_breakout(state, latest):
            return self._create_breakout_signal(
                SignalType.BREAKOUT_SHORT,
                state,
                latest
            )
        
        return None
    
    def _is_bullish_breakout(
        self,
        state: KeyLevelGridState,
        kline: Kline
    ) -> bool:
        """检测是否向上突破"""
        # 简化版：基于 MACD 和 RSI 判断
        if state.macd_histogram is not None and state.macd_histogram > 0:
            if kline.is_bullish and kline.body > kline.total_range * 0.5:
                return True
        return False
    
    def _is_bearish_breakout(
        self,
        state: KeyLevelGridState,
        kline: Kline
    ) -> bool:
        """检测是否向下突破"""
        if state.macd_histogram is not None and state.macd_histogram < 0:
            if kline.is_bearish and kline.body > kline.total_range * 0.5:
                return True
        return False
    
    def _create_breakout_signal(
        self,
        signal_type: SignalType,
        state: KeyLevelGridState,
        kline: Kline
    ) -> KeyLevelSignal:
        """创建突破信号"""
        is_long = signal_type == SignalType.BREAKOUT_LONG
        
        entry_price = kline.close
        
        # 止损价
        if is_long:
            stop_loss = entry_price * (1 - 0.05)
        else:
            stop_loss = entry_price * (1 + 0.05)
        
        # 止盈价
        risk = abs(entry_price - stop_loss)
        if is_long:
            take_profits = [
                entry_price + risk * 1.5,
                entry_price + risk * 2.5,
                entry_price + risk * 4.0,
            ]
        else:
            take_profits = [
                entry_price - risk * 1.5,
                entry_price - risk * 2.5,
                entry_price - risk * 4.0,
            ]
        
        score = self._calculate_breakout_score(state, kline, is_long)
        grade = self._score_to_grade(score)
        
        confidence = min(100, score)
        
        return KeyLevelSignal(
            signal_id=str(uuid.uuid4())[:8],
            signal_type=signal_type,
            symbol=self.symbol or state.symbol,
            timestamp=int(time.time() * 1000),
            current_price=kline.close,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profits=take_profits,
            confidence=confidence,
            score=score,
            grade=grade,
            trigger_reason=f"{'向上' if is_long else '向下'}突破",
            market_state=state,
        )
    
    def _check_pullback(
        self,
        state: KeyLevelGridState,
        klines: List[Kline]
    ) -> Optional[KeyLevelSignal]:
        """检测回踩信号"""
        if len(klines) < 3:
            return None
        
        latest = klines[-1]
        
        # 检测回踩做多
        if self._is_bullish_pullback(state, klines):
            return self._create_pullback_signal(
                SignalType.PULLBACK_LONG,
                state,
                latest
            )
        
        # 检测回踩做空
        if self._is_bearish_pullback(state, klines):
            return self._create_pullback_signal(
                SignalType.PULLBACK_SHORT,
                state,
                latest
            )
        
        return None
    
    def _is_bullish_pullback(
        self,
        state: KeyLevelGridState,
        klines: List[Kline]
    ) -> bool:
        """检测回踩做多"""
        if len(klines) < 3:
            return False
        
        latest = klines[-1]
        
        if state.tunnel_direction != "up":
            return False
        
        if latest.is_bullish:
            return True
        
        if latest.lower_wick > latest.body * 2 and latest.upper_wick < latest.body:
            return True
        
        return False
    
    def _is_bearish_pullback(
        self,
        state: KeyLevelGridState,
        klines: List[Kline]
    ) -> bool:
        """检测回踩做空"""
        if len(klines) < 3:
            return False
        
        latest = klines[-1]
        
        if state.tunnel_direction != "down":
            return False
        
        if latest.is_bearish:
            return True
        
        if latest.upper_wick > latest.body * 2 and latest.lower_wick < latest.body:
            return True
        
        return False
    
    def _create_pullback_signal(
        self,
        signal_type: SignalType,
        state: KeyLevelGridState,
        kline: Kline
    ) -> KeyLevelSignal:
        """创建回踩信号"""
        is_long = signal_type == SignalType.PULLBACK_LONG
        
        entry_price = kline.close
        
        if is_long:
            stop_loss = entry_price * (1 - 0.05)
        else:
            stop_loss = entry_price * (1 + 0.05)
        
        risk = abs(entry_price - stop_loss)
        if is_long:
            take_profits = [
                entry_price + risk * 1.5,
                entry_price + risk * 2.5,
                entry_price + risk * 4.0,
            ]
        else:
            take_profits = [
                entry_price - risk * 1.5,
                entry_price - risk * 2.5,
                entry_price - risk * 4.0,
            ]
        
        score = self._calculate_pullback_score(state, kline, is_long)
        grade = self._score_to_grade(score)
        
        confidence = min(100, score + 10)
        
        return KeyLevelSignal(
            signal_id=str(uuid.uuid4())[:8],
            signal_type=signal_type,
            symbol=self.symbol or state.symbol,
            timestamp=int(time.time() * 1000),
            current_price=kline.close,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profits=take_profits,
            confidence=confidence,
            score=score,
            grade=grade,
            trigger_reason=f"{'回踩做多' if is_long else '反弹做空'}",
            market_state=state,
        )
    
    def _calculate_breakout_score(
        self,
        state: KeyLevelGridState,
        kline: Kline,
        is_long: bool
    ) -> int:
        """计算突破信号评分"""
        score = 0
        
        # 收盘确认
        score += 25
        
        # 成交量
        if state.volume_ratio is not None:
            if state.volume_ratio >= 2.0:
                score += 20
            elif state.volume_ratio >= 1.5:
                score += 15
            elif state.volume_ratio >= 1.0:
                score += 10
        else:
            score += 10
        
        # 动量指标
        if state.macd_histogram is not None:
            if is_long and state.macd_histogram > 0:
                score += 15
            elif not is_long and state.macd_histogram < 0:
                score += 15
            else:
                score += 5
        else:
            score += 7
        
        # K线形态
        if is_long:
            if kline.is_bullish and kline.body > kline.total_range * 0.5:
                score += 10
            elif kline.is_bullish:
                score += 5
        else:
            if kline.is_bearish and kline.body > kline.total_range * 0.5:
                score += 10
            elif kline.is_bearish:
                score += 5
        
        # 趋势方向
        if is_long and state.tunnel_direction == "up":
            score += 10
        elif not is_long and state.tunnel_direction == "down":
            score += 10
        elif state.tunnel_direction == "flat":
            score += 5
        
        return score
    
    def _calculate_pullback_score(
        self,
        state: KeyLevelGridState,
        kline: Kline,
        is_long: bool
    ) -> int:
        """计算回踩信号评分"""
        score = 0
        
        # 趋势方向一致
        if is_long and state.tunnel_direction == "up":
            score += 30
        elif not is_long and state.tunnel_direction == "down":
            score += 30
        
        # 确认K线
        if is_long and kline.is_bullish:
            score += 25
        elif not is_long and kline.is_bearish:
            score += 25
        elif is_long and kline.lower_wick > kline.body * 2:
            score += 20
        elif not is_long and kline.upper_wick > kline.body * 2:
            score += 20
        
        # 成交量
        if state.volume_ratio is not None:
            if state.volume_ratio >= 1.0:
                score += 15
            elif state.volume_ratio >= 0.7:
                score += 10
        else:
            score += 7
        
        # MACD 确认
        if state.macd_histogram is not None:
            if is_long and state.macd_histogram > 0:
                score += 10
            elif not is_long and state.macd_histogram < 0:
                score += 10
        else:
            score += 5
        
        return score
    
    def _score_to_grade(self, score: int) -> SignalGrade:
        """评分转等级"""
        if score >= self.config.grade_a_score:
            return SignalGrade.A
        elif score >= self.config.grade_b_score:
            return SignalGrade.B
        elif score >= self.config.grade_c_score:
            return SignalGrade.C
        else:
            return SignalGrade.REJECT
    
    def _is_in_cooldown(self) -> bool:
        """检查是否在冷却期"""
        if self._last_signal_time == 0:
            return False
        
        cooldown_ms = self.config.cooldown_hours * 60 * 60 * 1000
        elapsed = int(time.time() * 1000) - self._last_signal_time
        
        return elapsed < cooldown_ms
    
    def reset_cooldown(self) -> None:
        """重置冷却期"""
        self._last_signal_time = 0
    
    def get_cooldown_remaining(self) -> float:
        """获取剩余冷却时间 (小时)"""
        if self._last_signal_time == 0:
            return 0.0
        
        cooldown_ms = self.config.cooldown_hours * 60 * 60 * 1000
        elapsed = int(time.time() * 1000) - self._last_signal_time
        remaining_ms = cooldown_ms - elapsed
        
        if remaining_ms <= 0:
            return 0.0
        
        return remaining_ms / (60 * 60 * 1000)
