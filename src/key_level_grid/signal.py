"""
关键位网格信号生成模块

生成突破信号和回踩信号
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from key_level_grid.utils.logger import get_logger
from key_level_grid.models import Kline, KeyLevelGridState


class SignalType(Enum):
    """信号类型"""
    NO_SIGNAL = "no_signal"
    BREAKOUT_LONG = "breakout_long"      # 向上突破
    BREAKOUT_SHORT = "breakout_short"    # 向下突破
    PULLBACK_LONG = "pullback_long"      # 回踩做多
    PULLBACK_SHORT = "pullback_short"    # 反弹做空


class SignalGrade(Enum):
    """信号等级"""
    A = "A"     # 高质量信号
    B = "B"     # 中等质量
    C = "C"     # 低质量
    REJECT = "REJECT"  # 拒绝


@dataclass
class SignalConfig:
    """信号配置"""
    # 突破确认
    breakout_threshold: float = 0.005     # 最小突破幅度 0.5%
    volume_multiplier: float = 1.5        # 突破时成交量倍数
    confirmation_candles: int = 2         # 确认K线数
    max_wick_ratio: float = 0.5           # 最大影线占比
    
    # 回踩信号
    pullback_enabled: bool = True
    pullback_max_depth: float = 0.02      # 回踩最大深度 2%
    
    # 冷却
    cooldown_hours: int = 8
    
    # 评分阈值
    min_score: int = 75
    grade_a_score: int = 90
    grade_b_score: int = 80
    grade_c_score: int = 75


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
    market_state: Optional[KeyLevelGridState] = None  # 市场状态 (指标数据)
    
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
    1. 突破做多: 价格收盘突破 EMA 169 (上轨)
    2. 突破做空: 价格收盘跌破 EMA 144 (下轨)
    3. 回踩做多: 价格在通道上方，回调触及通道后反弹
    4. 回踩做空: 价格在通道下方，反弹触及通道后回落
    """
    
    def __init__(self, config: Optional[SignalConfig] = None, symbol: str = ""):
        self.config = config or SignalConfig()
        self.symbol = symbol
        self.logger = get_logger(__name__)
        
        # 历史状态 (用于检测突破/回踩)
        self._prev_state: Optional[KeyLevelGridState] = None
        self._prev_klines: List[Kline] = []
        
        # 冷却管理
        self._last_signal_time: int = 0
    
    def generate(
        self,
        current_state: KeyLevelGridState,
        klines: List[Kline]
    ) -> Optional[KeyLevelSignal]:
        """
        生成交易信号
        
        Args:
            current_state: 当前通道状态
            klines: K线列表
            
        Returns:
            KeyLevelSignal 或 None (无信号)
        """
        # 冷却期检查
        if self._is_in_cooldown():
            return None
        
        signal = None
        
        # 1. 检测突破信号
        signal = self._check_breakout(current_state, klines)
        
        # 2. 如果没有突破，检测回踩信号
        if signal is None and self.config.pullback_enabled:
            signal = self._check_pullback(current_state, klines)
        
        # 更新历史状态
        self._prev_state = current_state
        self._prev_klines = klines[-10:] if len(klines) > 10 else klines.copy()
        
        # 如果生成了信号，更新冷却时间
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
        """
        检测突破信号
        
        向上突破条件:
        - 价格从通道内/下方进入通道上方
        - 收盘价 > EMA 169
        - 突破幅度 > breakout_threshold
        
        向下突破条件:
        - 价格从通道内/上方进入通道下方
        - 收盘价 < EMA 144
        - 突破幅度 > breakout_threshold
        """
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
        # 当前价格在通道上方
        if state.price_position != "above":
            return False
        
        # 之前价格不在通道上方 (即从内部或下方突破)
        if self._prev_state and self._prev_state.price_position == "above":
            return False
        
        # 收盘价高于 EMA 169
        if kline.close <= state.ema_169:
            return False
        
        # 突破幅度检查
        breakout_pct = (kline.close - state.ema_169) / state.ema_169
        if breakout_pct < self.config.breakout_threshold:
            return False
        
        # K线实体确认 (收盘价应该在 K 线上半部分)
        if kline.total_range > 0:
            body_position = (kline.close - kline.low) / kline.total_range
            if body_position < 0.5:  # 收盘价在下半部分
                return False
        
        return True
    
    def _is_bearish_breakout(
        self,
        state: KeyLevelGridState,
        kline: Kline
    ) -> bool:
        """检测是否向下突破"""
        # 当前价格在通道下方
        if state.price_position != "below":
            return False
        
        # 之前价格不在通道下方
        if self._prev_state and self._prev_state.price_position == "below":
            return False
        
        # 收盘价低于 EMA 144
        if kline.close >= state.ema_144:
            return False
        
        # 突破幅度检查
        breakout_pct = (state.ema_144 - kline.close) / state.ema_144
        if breakout_pct < self.config.breakout_threshold:
            return False
        
        # K线实体确认 (收盘价应该在 K 线下半部分)
        if kline.total_range > 0:
            body_position = (kline.close - kline.low) / kline.total_range
            if body_position > 0.5:  # 收盘价在上半部分
                return False
        
        return True
    
    def _create_breakout_signal(
        self,
        signal_type: SignalType,
        state: KeyLevelGridState,
        kline: Kline
    ) -> KeyLevelSignal:
        """创建突破信号"""
        is_long = signal_type == SignalType.BREAKOUT_LONG
        
        # 入场价 = 当前收盘价
        entry_price = kline.close
        
        # 止损价
        if is_long:
            # 做多止损在 EMA 144 下方
            stop_loss = state.ema_144 * (1 - 0.005)
        else:
            # 做空止损在 EMA 169 上方
            stop_loss = state.ema_169 * (1 + 0.005)
        
        # 止盈价 (简单的 R 倍数)
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
        
        # 计算评分
        score = self._calculate_breakout_score(state, kline, is_long)
        grade = self._score_to_grade(score)
        
        # 计算置信度
        confidence = min(100, score + state.distance_from_tunnel * 100)
        
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
            trigger_reason=f"{'向上' if is_long else '向下'}突破通道",
            market_state=state,
        )
    
    def _check_pullback(
        self,
        state: KeyLevelGridState,
        klines: List[Kline]
    ) -> Optional[KeyLevelSignal]:
        """
        检测回踩信号
        
        回踩做多条件:
        - 价格在通道上方运行 (已确认上升趋势)
        - 回调触及 EMA 169 或 EMA 144
        - 出现反弹确认 (阳线或锤子线)
        
        回踩做空条件:
        - 价格在通道下方运行 (已确认下降趋势)
        - 反弹触及 EMA 144 或 EMA 169
        - 出现回落确认 (阴线或倒锤子)
        """
        if len(klines) < 3:
            return None
        
        latest = klines[-1]
        prev = klines[-2]
        
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
        prev = klines[-2]
        
        # 通道方向向上
        if state.tunnel_direction != "up":
            return False
        
        # 当前或之前K线触及通道
        touched_tunnel = False
        for k in klines[-3:]:
            if k.low <= state.ema_169:
                touched_tunnel = True
                break
        
        if not touched_tunnel:
            return False
        
        # 当前价格回到通道上方或内部
        if state.price_position == "below":
            return False
        
        # 确认反弹: 当前K线收阳或锤子线
        if latest.is_bullish:
            return True
        
        # 锤子线: 下影线 > 2倍实体，上影线小
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
        
        # 通道方向向下
        if state.tunnel_direction != "down":
            return False
        
        # 当前或之前K线触及通道
        touched_tunnel = False
        for k in klines[-3:]:
            if k.high >= state.ema_144:
                touched_tunnel = True
                break
        
        if not touched_tunnel:
            return False
        
        # 当前价格回到通道下方或内部
        if state.price_position == "above":
            return False
        
        # 确认回落: 当前K线收阴或倒锤子
        if latest.is_bearish:
            return True
        
        # 倒锤子: 上影线 > 2倍实体，下影线小
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
        
        # 入场价 = 当前收盘价
        entry_price = kline.close
        
        # 止损价
        if is_long:
            # 做多止损在 EMA 144 下方
            stop_loss = state.ema_144 * (1 - 0.005)
        else:
            # 做空止损在 EMA 169 上方
            stop_loss = state.ema_169 * (1 + 0.005)
        
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
        
        # 计算评分 (回踩信号通常比突破更可靠)
        score = self._calculate_pullback_score(state, kline, is_long)
        grade = self._score_to_grade(score)
        
        confidence = min(100, score + 10)  # 回踩加分
        
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
        """
        计算突破信号评分
        
        评分维度:
        - 收盘确认: 25分
        - 突破幅度: 20分
        - 成交量: 20分
        - 动量指标: 15分
        - K线形态: 10分
        - 通道方向: 10分
        """
        score = 0
        
        # 1. 收盘确认 (25分)
        if is_long:
            if kline.close > state.ema_169:
                score += 25
        else:
            if kline.close < state.ema_144:
                score += 25
        
        # 2. 突破幅度 (20分)
        if is_long:
            breakout_pct = (kline.close - state.ema_169) / state.ema_169
        else:
            breakout_pct = (state.ema_144 - kline.close) / state.ema_144
        
        if breakout_pct >= 0.02:  # >= 2%
            score += 20
        elif breakout_pct >= 0.01:  # >= 1%
            score += 15
        elif breakout_pct >= 0.005:  # >= 0.5%
            score += 10
        
        # 3. 成交量 (20分)
        if state.volume_ratio is not None:
            if state.volume_ratio >= 2.0:
                score += 20
            elif state.volume_ratio >= 1.5:
                score += 15
            elif state.volume_ratio >= 1.0:
                score += 10
        else:
            score += 10  # 无数据给基础分
        
        # 4. 动量指标 (15分)
        if state.macd_histogram is not None:
            if is_long and state.macd_histogram > 0:
                score += 15
            elif not is_long and state.macd_histogram < 0:
                score += 15
            else:
                score += 5  # 不一致但给部分分
        else:
            score += 7  # 无数据给基础分
        
        # 5. K线形态 (10分)
        if is_long:
            if kline.is_bullish and kline.body > kline.total_range * 0.5:
                score += 10  # 大阳线
            elif kline.is_bullish:
                score += 5
        else:
            if kline.is_bearish and kline.body > kline.total_range * 0.5:
                score += 10  # 大阴线
            elif kline.is_bearish:
                score += 5
        
        # 6. 通道方向 (10分)
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
        
        # 1. 通道方向一致 (30分)
        if is_long and state.tunnel_direction == "up":
            score += 30
        elif not is_long and state.tunnel_direction == "down":
            score += 30
        
        # 2. 确认K线 (25分)
        if is_long and kline.is_bullish:
            score += 25
        elif not is_long and kline.is_bearish:
            score += 25
        elif is_long and kline.lower_wick > kline.body * 2:
            score += 20  # 锤子线
        elif not is_long and kline.upper_wick > kline.body * 2:
            score += 20  # 倒锤子
        
        # 3. 回踩深度适中 (20分)
        depth = state.distance_from_tunnel
        if 0.01 <= depth <= 0.03:  # 1-3% 最佳
            score += 20
        elif depth < 0.01:
            score += 10  # 太浅
        elif depth <= 0.05:
            score += 15  # 可接受
        
        # 4. 成交量 (15分)
        if state.volume_ratio is not None:
            if state.volume_ratio >= 1.0:
                score += 15
            elif state.volume_ratio >= 0.7:
                score += 10
        else:
            score += 7
        
        # 5. MACD 确认 (10分)
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

