"""
突破过滤增强模块

严格识别有效突破，过滤假突破
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from key_level_grid.utils.logger import get_logger
from key_level_grid.models import Kline, KeyLevelGridState
from key_level_grid.signal import KeyLevelSignal, SignalType


class FalseBreakoutType(Enum):
    """假突破类型"""
    PIERCE_THROUGH = "pierce_through"    # 刺穿型
    WICK_REJECTION = "wick_rejection"    # 影线拒绝
    NO_FOLLOW = "no_follow_through"      # 无跟进
    REVERSAL = "reversal_pattern"        # 反转形态


@dataclass
class BreakoutFilterConfig:
    """突破过滤器配置"""
    
    # 时间确认
    close_confirmation: bool = True       # 要求K线收盘确认
    min_hold_bars: int = 2                # 最少维持N根K线
    
    # 空间确认
    min_breakout_pct: float = 0.005       # 最小突破幅度 0.5%
    max_wick_ratio: float = 0.5           # 上/下影线占比 < 50%
    
    # 成交量确认
    volume_multiplier: float = 1.5        # 突破时成交量倍数
    volume_ma_period: int = 20            # 均量周期
    
    # 动量确认
    require_macd_cross: bool = False      # 要求MACD金/死叉
    require_rsi_confirm: bool = False     # RSI确认 (非超买超卖区)
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    
    # 形态确认
    require_pattern: bool = False         # 要求K线形态
    valid_patterns: List[str] = field(default_factory=lambda: [
        "engulfing", "hammer", "morning_star", "three_white_soldiers"
    ])
    
    # 假突破识别
    false_breakout_lookback: int = 10     # 回看N根K线识别假突破历史
    max_recent_false_breakouts: int = 2   # 近期假突破次数阈值


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
    - 多周期共振: 15分
    - K线形态: 5分 (加分项)
    
    通过阈值: 75分
    """
    
    def __init__(self, config: Optional[BreakoutFilterConfig] = None):
        self.config = config or BreakoutFilterConfig()
        self.logger = get_logger(__name__)
        
        # 假突破历史记录
        self._false_breakout_history: List[int] = []  # 时间戳列表
    
    def validate_breakout(
        self,
        state: KeyLevelGridState,
        klines: List[Kline],
        is_long: bool = True
    ) -> BreakoutResult:
        """
        验证突破有效性
        
        Args:
            state: 通道状态
            klines: K线列表
            is_long: 是否做多
            
        Returns:
            BreakoutResult
        """
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
        close_confirmed = self._check_close_confirmation(state, latest, is_long)
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
            score += 5  # 给部分分
            details["momentum"] = "⚠️ 动量不明确"
        
        # 5. 通道方向 (15分) - 代替多周期共振
        direction_ok = self._check_tunnel_direction(state, is_long)
        if direction_ok:
            score += 15
            details["direction"] = "✅ 通道方向一致"
        elif state.tunnel_direction == "flat":
            score += 7
            details["direction"] = "⚠️ 通道震荡"
        else:
            details["direction"] = "❌ 通道方向相反"
        
        # 6. K线形态 (5分加分)
        if self._check_candlestick_pattern(latest, is_long):
            score += 5
            details["pattern"] = "✅ 形态确认"
        
        # 检查是否为假突破
        false_type = self._detect_false_breakout(state, klines, is_long)
        if false_type:
            score = min(score, 60)  # 限制最高分
            details["false_breakout"] = f"⚠️ 疑似{false_type.value}"
        
        return BreakoutResult(
            is_valid=score >= 75,
            score=score,
            details=details,
            false_breakout_type=false_type
        )
    
    def _check_close_confirmation(
        self,
        state: KeyLevelGridState,
        kline: Kline,
        is_long: bool
    ) -> bool:
        """检查收盘确认"""
        if not kline.is_closed:
            return False
        
        if is_long:
            # 收盘价 > EMA 169
            return kline.close > state.ema_169
        else:
            # 收盘价 < EMA 144
            return kline.close < state.ema_144
    
    def _calculate_breakout_distance(
        self,
        state: KeyLevelGridState,
        kline: Kline,
        is_long: bool
    ) -> float:
        """计算突破幅度"""
        if is_long:
            if state.ema_169 == 0:
                return 0.0
            return (kline.close - state.ema_169) / state.ema_169
        else:
            if state.ema_144 == 0:
                return 0.0
            return (state.ema_144 - kline.close) / state.ema_144
    
    def _check_volume_surge(self, state: KeyLevelGridState) -> float:
        """检查成交量放大"""
        if state.volume_ratio is None:
            return 1.0  # 无数据返回中性值
        return state.volume_ratio
    
    def _check_momentum(self, state: KeyLevelGridState, is_long: bool) -> bool:
        """检查动量指标"""
        # MACD 检查
        if state.macd_histogram is not None:
            if is_long and state.macd_histogram > 0:
                return True
            elif not is_long and state.macd_histogram < 0:
                return True
        
        # RSI 检查 (可选)
        if self.config.require_rsi_confirm and state.rsi is not None:
            if is_long and state.rsi < self.config.rsi_overbought:
                return True
            elif not is_long and state.rsi > self.config.rsi_oversold:
                return True
            return False
        
        return True  # 默认通过
    
    def _check_tunnel_direction(
        self,
        state: KeyLevelGridState,
        is_long: bool
    ) -> bool:
        """检查通道方向"""
        if is_long:
            return state.tunnel_direction == "up"
        else:
            return state.tunnel_direction == "down"
    
    def _check_candlestick_pattern(self, kline: Kline, is_long: bool) -> bool:
        """检查K线形态"""
        if is_long:
            # 阳线，且实体占比 > 50%
            if kline.is_bullish:
                if kline.total_range > 0:
                    body_ratio = kline.body / kline.total_range
                    return body_ratio > 0.5
        else:
            # 阴线，且实体占比 > 50%
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
        """
        检测假突破
        
        假突破特征:
        1. 刺穿型: 价格突破后快速回落到通道内
        2. 影线型: 突破K线有长影线，实体在通道内
        3. 孤立型: 突破后无后续跟进
        4. 反转型: 突破后形成反转K线形态
        """
        if len(klines) < 2:
            return None
        
        latest = klines[-1]
        
        # 1. 刺穿型检测
        if self._check_pierce_through(state, latest, is_long):
            return FalseBreakoutType.PIERCE_THROUGH
        
        # 2. 影线拒绝检测
        if self._check_wick_rejection(state, latest, is_long):
            return FalseBreakoutType.WICK_REJECTION
        
        # 3. 无跟进检测 (需要历史数据)
        if len(klines) >= 4:
            if self._check_no_follow_through(klines, is_long):
                return FalseBreakoutType.NO_FOLLOW
        
        # 4. 反转形态检测
        if self._check_reversal_pattern(klines):
            return FalseBreakoutType.REVERSAL
        
        return None
    
    def _check_pierce_through(
        self,
        state: KeyLevelGridState,
        kline: Kline,
        is_long: bool
    ) -> bool:
        """
        刺穿型检测
        
        条件:
        - 价格一度突破 EMA169 (向上) 或 EMA144 (向下)
        - 但收盘价回落到通道内
        """
        if is_long:
            # 最高价突破，但收盘在通道内
            if kline.high > state.ema_169 and kline.close < state.ema_169:
                return True
        else:
            # 最低价突破，但收盘在通道内
            if kline.low < state.ema_144 and kline.close > state.ema_144:
                return True
        
        return False
    
    def _check_wick_rejection(
        self,
        state: KeyLevelGridState,
        kline: Kline,
        is_long: bool
    ) -> bool:
        """
        影线拒绝检测
        
        条件:
        - 突破K线影线长度 > 实体长度
        - 影线方向与突破方向一致
        """
        body = kline.body
        if body == 0:
            return True  # 十字星视为拒绝
        
        if is_long:
            # 向上突破，检查上影线
            if kline.upper_wick > body * self.config.max_wick_ratio * 2:
                return True
        else:
            # 向下突破，检查下影线
            if kline.lower_wick > body * self.config.max_wick_ratio * 2:
                return True
        
        return False
    
    def _check_no_follow_through(
        self,
        klines: List[Kline],
        is_long: bool
    ) -> bool:
        """
        无跟进检测
        
        条件:
        - 突破后2-3根K线未能继续扩大突破距离
        - 或者形成横盘整理
        """
        if len(klines) < 4:
            return False
        
        breakout_close = klines[-3].close  # 假设突破发生在3根前
        subsequent = klines[-2:]
        
        # 检查是否有进一步推进
        for bar in subsequent:
            if is_long:
                if bar.close > breakout_close * 1.005:  # 继续上涨 > 0.5%
                    return False
            else:
                if bar.close < breakout_close * 0.995:  # 继续下跌 > 0.5%
                    return False
        
        return True  # 无明显跟进
    
    def _check_reversal_pattern(self, klines: List[Kline]) -> bool:
        """
        反转形态检测
        
        检测突破后是否出现:
        - 十字星
        - 锤子线/倒锤子
        - 吞没形态
        """
        if len(klines) < 2:
            return False
        
        latest = klines[-1]
        
        # 十字星: 实体极小
        if latest.is_doji:
            return True
        
        # 可以扩展更多形态检测...
        
        return False
    
    def record_false_breakout(self, timestamp: int) -> None:
        """记录假突破"""
        self._false_breakout_history.append(timestamp)
        
        # 只保留最近的记录
        cutoff = timestamp - self.config.false_breakout_lookback * 4 * 60 * 60 * 1000
        self._false_breakout_history = [
            t for t in self._false_breakout_history if t > cutoff
        ]
    
    def get_recent_false_breakouts(self) -> int:
        """获取近期假突破次数"""
        return len(self._false_breakout_history)


class DynamicThresholdAdjuster:
    """
    根据市场环境动态调整过滤阈值
    """
    
    def __init__(self):
        self.logger = get_logger(__name__)
    
    def adjust_thresholds(
        self,
        base_config: BreakoutFilterConfig,
        atr_percentile: float,
        adx: Optional[float] = None
    ) -> BreakoutFilterConfig:
        """
        根据市场状态调整阈值
        
        Args:
            base_config: 基础配置
            atr_percentile: ATR 百分位 (0-1)
            adx: ADX 值 (趋势强度)
            
        Returns:
            调整后的配置
        """
        from dataclasses import replace
        
        config = replace(base_config)
        
        # ATR 百分位判断波动率
        if atr_percentile > 0.8:  # 高波动
            config.min_breakout_pct = 0.01      # 提高到1%
            config.volume_multiplier = 1.2      # 降低到1.2x
            self.logger.debug("高波动市场: 提高突破阈值")
        elif atr_percentile < 0.2:  # 低波动
            config.min_breakout_pct = 0.003     # 降低到0.3%
            config.volume_multiplier = 2.0      # 提高到2x
            self.logger.debug("低波动市场: 降低突破阈值，提高量能要求")
        
        # ADX 判断趋势强度
        if adx is not None:
            if adx > 25:  # 趋势市场
                config.min_hold_bars = 1
                self.logger.debug("趋势市场: 放宽确认条件")
            else:  # 震荡市场
                config.min_hold_bars = 3
                config.require_macd_cross = True
                self.logger.debug("震荡市场: 严格确认条件")
        
        return config

