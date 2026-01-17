"""
水位评分数据结构 (LEVEL_GENERATION.md v3.1.0)

包含:
- LevelScore: 水位评分详情
- FractalPoint: 分形点
- VPVRData: 成交量分布数据
- MTFLevelCandidate: MTF 水位候选
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from enum import Enum


class VolumeZone(str, Enum):
    """成交量区域类型"""
    HVN = "HVN"        # 高成交量节点 (High Volume Node)
    NORMAL = "NORMAL"  # 普通区域
    LVN = "LVN"        # 真空区 (Low Volume Node)


class TrendState(str, Enum):
    """趋势状态"""
    BULLISH = "BULLISH"    # 多头
    BEARISH = "BEARISH"    # 空头
    NEUTRAL = "NEUTRAL"    # 震荡


@dataclass
class FractalPoint:
    """
    分形点 (MTF 增强版)
    
    分形点是基于斐波那契周期的物理极值点，
    用于识别市场结构中的关键支撑/阻力位。
    """
    price: float                # 分形价格
    timestamp: int              # 时间戳 (ms)
    type: str                   # "HIGH" | "LOW"
    timeframe: str              # 时间框架 "1d" | "4h" | "15m"
    period: int                 # 回溯周期 8/13/21/34/55/89
    kline_index: int            # K 线索引 (从最新向前计数)
    
    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "timestamp": self.timestamp,
            "type": self.type,
            "timeframe": self.timeframe,
            "period": self.period,
            "kline_index": self.kline_index,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "FractalPoint":
        return cls(
            price=float(data.get("price", 0)),
            timestamp=int(data.get("timestamp", 0)),
            type=data.get("type", "LOW"),
            timeframe=data.get("timeframe", "4h"),
            period=int(data.get("period", 21)),
            kline_index=int(data.get("kline_index", 0)),
        )


@dataclass
class VPVRData:
    """
    成交量分布数据 (Volume Profile Visible Range)
    
    用于识别筹码密集区 (HVN) 和真空区 (LVN)，
    为水位评分提供能量验证。
    """
    poc_price: float                          # 控制价 (Point of Control)
    hvn_zones: List[Tuple[float, float]]      # 高能量区间列表 [(low, high), ...]
    lvn_zones: List[Tuple[float, float]]      # 真空区间列表 [(low, high), ...]
    total_volume: float                        # 总成交量
    price_range: Tuple[float, float] = (0, 0)  # 价格范围 (min, max)
    
    def to_dict(self) -> dict:
        return {
            "poc_price": self.poc_price,
            "hvn_zones": self.hvn_zones,
            "lvn_zones": self.lvn_zones,
            "total_volume": self.total_volume,
            "price_range": self.price_range,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "VPVRData":
        return cls(
            poc_price=float(data.get("poc_price", 0)),
            hvn_zones=data.get("hvn_zones", []),
            lvn_zones=data.get("lvn_zones", []),
            total_volume=float(data.get("total_volume", 0)),
            price_range=tuple(data.get("price_range", (0, 0))),
        )
    
    def get_zone_type(self, price: float) -> VolumeZone:
        """判断价格所在的成交量区域类型"""
        for low, high in self.hvn_zones:
            if low <= price <= high:
                return VolumeZone.HVN
        for low, high in self.lvn_zones:
            if low <= price <= high:
                return VolumeZone.LVN
        return VolumeZone.NORMAL


@dataclass
class LevelScore:
    """
    水位评分详情 (MTF 增强版)
    
    评分公式: Final_Score = S_base × W_volume × W_psychology × T_env × M_mtf
    
    - S_base: 基础分 (时间框架权重 × 回溯周期权重)
    - W_volume: 成交量权重 (HVN=1.3, NORMAL=1.0, LVN=0.6)
    - W_psychology: 心理位权重 (对齐=1.2, 无=1.0)
    - T_env: 趋势系数 (顺势=1.1, 逆势=0.9)
    - M_mtf: MTF 共振系数 (三框架=2.0, 双框架=1.2~1.5, 单框架=1.0)
    """
    # 基础信息
    base_score: float                          # 基础分 (来自周期×框架)
    source_timeframes: List[str] = field(default_factory=list)  # 来源时间框架 ["1d", "4h"]
    source_periods: List[int] = field(default_factory=list)     # 来源周期列表 [21, 55]
    
    # 成交量修正
    volume_weight: float = 1.0                 # 成交量权重
    volume_zone: VolumeZone = VolumeZone.NORMAL  # 成交量区域
    
    # 心理位修正
    psychology_weight: float = 1.0             # 心理位权重
    psychology_anchor: Optional[float] = None  # 吸附的心理位价格
    
    # 趋势修正
    trend_coefficient: float = 1.0             # 趋势系数
    trend_state: TrendState = TrendState.NEUTRAL  # 趋势状态
    
    # MTF 共振
    mtf_coefficient: float = 1.0               # MTF 共振系数
    is_resonance: bool = False                 # 是否为共振水位
    
    # 最终评分
    final_score: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "base_score": self.base_score,
            "source_timeframes": self.source_timeframes,
            "source_periods": self.source_periods,
            "volume_weight": self.volume_weight,
            "volume_zone": self.volume_zone.value if isinstance(self.volume_zone, VolumeZone) else str(self.volume_zone),
            "psychology_weight": self.psychology_weight,
            "psychology_anchor": self.psychology_anchor,
            "trend_coefficient": self.trend_coefficient,
            "trend_state": self.trend_state.value if isinstance(self.trend_state, TrendState) else str(self.trend_state),
            "mtf_coefficient": self.mtf_coefficient,
            "is_resonance": self.is_resonance,
            "final_score": self.final_score,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "LevelScore":
        # 处理 volume_zone
        volume_zone = data.get("volume_zone", "NORMAL")
        try:
            volume_zone = VolumeZone(volume_zone)
        except (ValueError, TypeError):
            volume_zone = VolumeZone.NORMAL
        
        # 处理 trend_state
        trend_state = data.get("trend_state", "NEUTRAL")
        try:
            trend_state = TrendState(trend_state)
        except (ValueError, TypeError):
            trend_state = TrendState.NEUTRAL
        
        return cls(
            base_score=float(data.get("base_score", 0)),
            source_timeframes=data.get("source_timeframes", []),
            source_periods=data.get("source_periods", []),
            volume_weight=float(data.get("volume_weight", 1.0)),
            volume_zone=volume_zone,
            psychology_weight=float(data.get("psychology_weight", 1.0)),
            psychology_anchor=data.get("psychology_anchor"),
            trend_coefficient=float(data.get("trend_coefficient", 1.0)),
            trend_state=trend_state,
            mtf_coefficient=float(data.get("mtf_coefficient", 1.0)),
            is_resonance=bool(data.get("is_resonance", False)),
            final_score=float(data.get("final_score", 0)),
        )
    
    def calculate_final(self) -> float:
        """重新计算最终评分"""
        self.final_score = (
            self.base_score 
            * self.volume_weight 
            * self.psychology_weight 
            * self.trend_coefficient 
            * self.mtf_coefficient
        )
        return self.final_score


@dataclass
class MTFLevelCandidate:
    """
    MTF 水位候选
    
    当多个时间框架在相近价位识别出分形点时，
    会被合并为一个 MTF 水位候选，并计算共振加成。
    """
    price: float                               # 原始价格
    source_fractals: List[FractalPoint] = field(default_factory=list)  # 来源分形点列表
    source_timeframes: List[str] = field(default_factory=list)         # 来源时间框架
    is_resonance: bool = False                 # 是否共振 (多框架识别)
    merged_price: float = 0.0                  # 合并后价格 (若共振则取高框架价格)
    
    def __post_init__(self):
        if self.merged_price == 0.0:
            self.merged_price = self.price
        if not self.source_timeframes and self.source_fractals:
            self.source_timeframes = list(set(f.timeframe for f in self.source_fractals))
        self.is_resonance = len(self.source_timeframes) > 1
    
    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "source_fractals": [f.to_dict() for f in self.source_fractals],
            "source_timeframes": self.source_timeframes,
            "is_resonance": self.is_resonance,
            "merged_price": self.merged_price,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "MTFLevelCandidate":
        return cls(
            price=float(data.get("price", 0)),
            source_fractals=[
                FractalPoint.from_dict(f) for f in data.get("source_fractals", [])
            ],
            source_timeframes=data.get("source_timeframes", []),
            is_resonance=bool(data.get("is_resonance", False)),
            merged_price=float(data.get("merged_price", 0)),
        )


# ============================================
# 评分配置常量 (可被 config.yaml 覆盖)
# ============================================

# 时间框架权重
DEFAULT_TIMEFRAME_WEIGHTS = {
    "1d":  1.5,   # 趋势层
    "4h":  1.0,   # 战略层
    "15m": 0.6,   # 战术层
}

# 周期基础分
DEFAULT_PERIOD_SCORES = {
    89: 80,  # 长周期
    55: 80,
    34: 50,  # 中周期
    21: 50,
    13: 20,  # 短周期
    8: 20,
}

# 成交量权重
DEFAULT_VOLUME_WEIGHTS = {
    VolumeZone.HVN: 1.3,
    VolumeZone.NORMAL: 1.0,
    VolumeZone.LVN: 0.6,
}

# 心理位权重
DEFAULT_PSYCHOLOGY_WEIGHT = 1.2

# 趋势系数
DEFAULT_TREND_COEFFICIENTS = {
    TrendState.BULLISH: {"support": 1.1, "resistance": 0.9},
    TrendState.BEARISH: {"support": 0.9, "resistance": 1.1},
    TrendState.NEUTRAL: {"support": 1.0, "resistance": 1.0},
}

# MTF 共振系数
DEFAULT_MTF_RESONANCE = {
    frozenset(["1d", "4h", "15m"]): 2.0,  # 三框架共振
    frozenset(["1d", "4h"]):        1.5,  # 趋势+战略
    frozenset(["1d", "15m"]):       1.3,  # 趋势+战术
    frozenset(["4h", "15m"]):       1.2,  # 战略+战术
}


def calculate_mtf_coefficient(source_timeframes: List[str]) -> float:
    """
    计算 MTF 共振系数
    
    Args:
        source_timeframes: 该水位被哪些时间框架识别
    
    Returns:
        共振系数 (1.0 ~ 2.0)
    """
    if len(source_timeframes) <= 1:
        return 1.0
    
    tf_set = frozenset(source_timeframes)
    return DEFAULT_MTF_RESONANCE.get(tf_set, 1.0)


def calculate_base_score(timeframe: str, period: int) -> float:
    """
    计算基础分
    
    Args:
        timeframe: 时间框架 "1d" | "4h" | "15m"
        period: 回溯周期 8/13/21/34/55/89
    
    Returns:
        基础分 = 时间框架权重 × 周期权重
    
    示例:
    - 1d 55周期: 1.5 × 80 = 120
    - 4h 21周期: 1.0 × 50 = 50
    - 15m 13周期: 0.6 × 20 = 12
    """
    tf_weight = DEFAULT_TIMEFRAME_WEIGHTS.get(timeframe, 1.0)
    period_score = DEFAULT_PERIOD_SCORES.get(period, 20)
    return tf_weight * period_score
