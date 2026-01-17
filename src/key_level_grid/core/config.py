"""
配置数据类模块

包含所有策略相关的配置
"""

from dataclasses import dataclass, field
from typing import List


# ============================================
# 网格配置
# ============================================

@dataclass
class GridConfig:
    """网格配置"""
    # 区间设置
    range_mode: str = "auto"          # auto | manual
    manual_upper: float = 0.0         # 手动上边界
    manual_lower: float = 0.0         # 手动下边界
    
    # 网格数量
    count_mode: str = "by_levels"     # by_levels | fixed
    fixed_count: int = 5              # fixed 模式的网格数量
    max_grids: int = 10               # 最大网格数量
    
    # 网格底线
    floor_buffer: float = 0.005       # 最低支撑下方 0.5%
    
    # ============================================
    # Spec2.0 核心策略参数
    # ============================================
    sell_quota_ratio: float = 0.7        # 动态止盈比例
    min_profit_pct: float = 0.005        # 均价利润保护阈值
    buy_price_buffer_pct: float = 0.002   # 买单空间缓冲
    sell_price_buffer_pct: float = 0.002  # 卖单空间缓冲
    max_fill_per_level: int = 1           # 单水位最大补买次数
    base_amount_per_grid: float = 1.0    # 标准网格单位（BTC数量）
    base_position_locked: float = 0.0    # 固定底仓数量（BTC数量）
    recon_interval_sec: int = 30         # Recon 周期
    order_action_timeout_sec: int = 10   # 挂/撤单超时
    restore_state_enabled: bool = True   # 是否从持久化恢复网格


@dataclass
class PositionConfig:
    """仓位配置"""
    total_capital: float = 5000.0     # 账户总金额 (USDT)
    max_leverage: float = 3.0         # 最大杠杆
    max_capital_usage: float = 0.8    # 使用 80% 资金
    
    # 仓位分配
    allocation_mode: str = "equal"    # equal | weighted
    
    # 手续费假设
    taker_fee: float = 0.0004         # 0.04%
    slippage: float = 0.001           # 0.1%

    @property
    def max_position_usdt(self) -> float:
        """最大仓位 = 总资金 × 杠杆 × 使用率"""
        return self.total_capital * self.max_leverage * self.max_capital_usage


# ============================================
# 止损止盈配置
# ============================================

@dataclass
class StopLossConfig:
    """止损配置"""
    mode: str = "total"               # total: 统一止损
    trigger: str = "grid_floor"       # grid_floor | fixed_pct
    fixed_pct: float = 0.10           # 固定止损 10%
    
    # 详细止损参数
    grid_buffer: float = 0.005        # 网格止损: 最低支撑下方 0.5%
    min_distance_pct: float = 0.02    # 最小止损距离 2%
    
    # 保本止损 (可选)
    breakeven_enabled: bool = False
    breakeven_activation_rr: float = 0.5   # 0.5R 后激活保本
    breakeven_offset: float = 0.002        # 保本位 + 0.2% (覆盖手续费)
    
    # 跟踪止损 (可选)
    trailing_enabled: bool = False
    trailing_activation_rr: float = 1.0    # 1R 后激活跟踪
    trailing_pct: float = 0.03             # 回撤 3% 触发


@dataclass
class TakeProfitConfig:
    """止盈配置"""
    mode: str = "by_resistance"       # by_resistance | fixed_pct
    fixed_pct: float = 0.05           # 固定止盈 5%
    min_rr_ratio: float = 1.5         # 最小盈亏比


# ============================================
# 支撑阻力配置
# ============================================

@dataclass
class ResistanceConfig:
    """支撑/阻力位配置"""
    min_strength: int = 80            # 最低强度阈值
    
    # 多尺度摆动点
    swing_lookbacks: List[int] = field(default_factory=lambda: [5, 13, 34])
    swing_weights: List[float] = field(default_factory=lambda: [0.2, 0.3, 0.5])
    
    # 斐波那契
    fib_ratios: List[float] = field(default_factory=lambda: [0.382, 0.5, 0.618, 1.0, 1.618])
    
    # 成交量密集区
    volume_enabled: bool = True
    volume_bucket_pct: float = 0.01
    volume_top_pct: float = 0.20
    
    # 多周期融合
    multi_timeframe: bool = True
    mtf_boost: float = 0.30
    auxiliary_boost: float = 1.2
    
    # 距离过滤
    merge_tolerance: float = 0.005
    min_distance_pct: float = 0.005   # 最小距离 0.5%
    max_distance_pct: float = 0.30    # 最大距离 30%
    min_rr_for_tp: float = 1.5        # 止盈最小盈亏比
    
    # 强度衰减
    strength_decay_bars: int = 200


# ============================================
# 信号配置
# ============================================

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
class FilterConfig:
    """过滤器配置"""
    # MACD趋势过滤
    macd_trend_enabled: bool = True
    
    # 成交量过滤
    volume_confirmation: bool = True
    volume_min_ratio: float = 1.0
    
    # RSI 过滤
    rsi_enabled: bool = True
    rsi_overbought: float = 70.0      # 超买阈值
    rsi_oversold: float = 30.0        # 超卖阈值
    
    # ADX 过滤 (趋势强度)
    adx_enabled: bool = True
    adx_min: float = 20.0             # 最小趋势强度
    
    # 冷却过滤
    cooldown_hours: int = 8
    
    # 时间过滤
    time_filter_enabled: bool = False
    trading_hours: List[int] = None   # 允许交易的小时 (UTC)
    
    # 多周期过滤
    mtf_enabled: bool = True
    mtf_min_confidence: float = 40.0


# ============================================
# 指标配置
# ============================================

@dataclass
class IndicatorConfig:
    """指标配置"""
    # MACD
    macd_enabled: bool = True
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    
    # RSI
    rsi_enabled: bool = True
    rsi_period: int = 14
    
    # ATR
    atr_enabled: bool = True
    atr_period: int = 14
    
    # ADX (趋势强度)
    adx_enabled: bool = True
    adx_period: int = 14
    
    # 成交量
    volume_ma_period: int = 20


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
