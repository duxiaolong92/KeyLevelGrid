"""
类型定义模块

包含所有枚举类型和类型别名
"""

from enum import Enum


class LevelStatus(str, Enum):
    """订单操作状态"""
    IDLE = "IDLE"
    PLACING = "PLACING"
    ACTIVE = "ACTIVE"
    FILLED = "FILLED"
    CANCELING = "CANCELING"


class LevelLifecycleStatus(str, Enum):
    """
    水位生命周期状态 (SPEC_LEVEL_LIFECYCLE.md v2.0.0)
    
    状态行为:
    - ACTIVE: 活跃，允许买入和卖出
    - RETIRED: 退役，仅允许卖出清仓
    - DEAD: 销毁，待物理删除
    """
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    DEAD = "DEAD"


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


class StopLossType(Enum):
    """止损类型"""
    FIXED = "fixed"           # 固定止损 (入场时设定)
    GRID_FLOOR = "grid_floor" # 网格底线止损 (最低支撑位)
    TRAILING = "trailing"     # 跟踪止损 (跟随最高价)
    BREAKEVEN = "breakeven"   # 保本止损 (盈利后移动到成本价)


class LevelType(Enum):
    """价位类型"""
    SWING_HIGH = "swing_high"              # 摆动高点
    SWING_LOW = "swing_low"                # 摆动低点
    FIBONACCI = "fibonacci"                 # 斐波那契
    PSYCHOLOGICAL = "psychological"         # 心理关口 (整数位)
    VOLUME_NODE = "volume_node"             # 成交量密集区


class FalseBreakoutType(Enum):
    """假突破类型"""
    PIERCE_THROUGH = "pierce_through"    # 刺穿型
    WICK_REJECTION = "wick_rejection"    # 影线拒绝
    NO_FOLLOW = "no_follow_through"      # 无跟进
    REVERSAL = "reversal_pattern"        # 反转形态
