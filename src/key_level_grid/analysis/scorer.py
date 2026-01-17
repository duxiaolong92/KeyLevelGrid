"""
MTF 水位评分计算器 (LEVEL_GENERATION.md v3.1.0)

评分公式: Final_Score = S_base × W_volume × W_psychology × T_env × M_mtf

其中:
- S_base: 基础分 = 时间框架权重 × 周期基础分
- W_volume: 成交量权重 (HVN=1.3, Normal=1.0, LVN=0.6)
- W_psychology: 心理位权重 (对齐=1.2, 无=1.0)
- T_env: 趋势系数 (顺势=1.1, 逆势=0.9)
- M_mtf: MTF 共振系数 (三框架=2.0, 双框架=1.2~1.5, 单框架=1.0)
"""

from typing import List, Dict, Optional, Tuple
from key_level_grid.core.scoring import (
    LevelScore,
    FractalPoint,
    VPVRData,
    MTFLevelCandidate,
    VolumeZone,
    TrendState,
    calculate_base_score,
    calculate_mtf_coefficient,
    DEFAULT_TIMEFRAME_WEIGHTS,
    DEFAULT_PERIOD_SCORES,
    DEFAULT_VOLUME_WEIGHTS,
    DEFAULT_TREND_COEFFICIENTS,
)


class LevelScorer:
    """
    MTF 水位评分器
    
    基于多维度因素计算水位的综合评分，
    用于决定是否开仓以及开仓数量。
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """
        初始化评分器
        
        Args:
            config: 配置字典 (从 config.yaml 加载)
        """
        self.config = config or {}
        self._load_weights()
    
    def _load_weights(self):
        """从配置加载权重参数"""
        scoring_config = self.config.get("scoring", {})
        
        # 时间框架权重
        self.tf_weights = scoring_config.get(
            "timeframe_weights", 
            DEFAULT_TIMEFRAME_WEIGHTS
        )
        
        # 周期基础分
        self.period_scores = {
            int(k): v for k, v in scoring_config.get(
                "period_scores", 
                DEFAULT_PERIOD_SCORES
            ).items()
        }
        
        # 成交量权重
        vol_weights = scoring_config.get("volume_weights", {})
        self.volume_weights = {
            VolumeZone.HVN: float(vol_weights.get("hvn", 1.3)),
            VolumeZone.NORMAL: float(vol_weights.get("normal", 1.0)),
            VolumeZone.LVN: float(vol_weights.get("lvn", 0.6)),
        }
        
        # 心理位权重
        self.psychology_weight = float(scoring_config.get("psychology_weight", 1.2))
        
        # 趋势系数
        trend_config = scoring_config.get("trend_coefficients", {})
        self.trend_coefficients = {}
        for trend in ["bullish", "bearish", "neutral"]:
            self.trend_coefficients[trend] = {
                "support": float(trend_config.get(trend, {}).get("support", 1.0)),
                "resistance": float(trend_config.get(trend, {}).get("resistance", 1.0)),
            }
        
        # MTF 共振系数
        mtf_config = scoring_config.get("mtf_resonance", {})
        self.mtf_resonance = {}
        for tf_str, coef in mtf_config.items():
            tf_set = frozenset(tf_str.split(","))
            self.mtf_resonance[tf_set] = float(coef)
    
    def calculate_score(
        self,
        candidate: MTFLevelCandidate,
        vpvr: Optional[VPVRData],
        trend_state: TrendState,
        role: str,
        psychology_anchor: Optional[float] = None,
    ) -> LevelScore:
        """
        计算水位综合评分
        
        Args:
            candidate: MTF 水位候选
            vpvr: VPVR 分析结果 (可选)
            trend_state: 趋势状态
            role: "support" | "resistance"
            psychology_anchor: 吸附的心理位价格 (可选)
        
        Returns:
            LevelScore 评分详情
        """
        # 1. 计算基础分 (取所有来源分形的最高基础分)
        base_scores = []
        for fractal in candidate.source_fractals:
            tf_weight = self.tf_weights.get(fractal.timeframe, 1.0)
            period_score = self.period_scores.get(fractal.period, 20)
            base_scores.append(tf_weight * period_score)
        
        base_score = max(base_scores) if base_scores else 20
        
        # 若多框架共振，基础分取和
        if candidate.is_resonance:
            base_score = sum(base_scores)
        
        # 2. 成交量权重
        volume_weight = 1.0
        volume_zone = VolumeZone.NORMAL
        
        if vpvr:
            volume_zone = vpvr.get_zone_type(candidate.merged_price)
            volume_weight = self.volume_weights.get(volume_zone, 1.0)
        
        # 3. 心理位权重
        psychology_weight = 1.0
        if psychology_anchor is not None:
            psychology_weight = self.psychology_weight
        
        # 4. 趋势系数
        trend_key = trend_state.value.lower()
        trend_coef = self.trend_coefficients.get(trend_key, {}).get(role, 1.0)
        
        # 5. MTF 共振系数
        mtf_coef = self._calculate_mtf_coefficient(candidate.source_timeframes)
        
        # 6. 最终评分
        final_score = (
            base_score 
            * volume_weight 
            * psychology_weight 
            * trend_coef 
            * mtf_coef
        )
        
        # 提取来源周期
        source_periods = list(set(f.period for f in candidate.source_fractals))
        
        return LevelScore(
            base_score=base_score,
            source_timeframes=candidate.source_timeframes,
            source_periods=source_periods,
            volume_weight=volume_weight,
            volume_zone=volume_zone,
            psychology_weight=psychology_weight,
            psychology_anchor=psychology_anchor,
            trend_coefficient=trend_coef,
            trend_state=trend_state,
            mtf_coefficient=mtf_coef,
            is_resonance=candidate.is_resonance,
            final_score=final_score,
        )
    
    def _calculate_mtf_coefficient(self, source_timeframes: List[str]) -> float:
        """
        计算 MTF 共振系数
        
        Args:
            source_timeframes: 水位来源时间框架列表
        
        Returns:
            共振系数 (1.0 ~ 2.0)
        """
        if len(source_timeframes) <= 1:
            return 1.0
        
        tf_set = frozenset(source_timeframes)
        
        # 先从配置查找
        if tf_set in self.mtf_resonance:
            return self.mtf_resonance[tf_set]
        
        # 使用默认计算
        return calculate_mtf_coefficient(source_timeframes)
    
    def get_qty_multiplier(self, final_score: float) -> float:
        """
        根据评分计算仓位系数
        
        Args:
            final_score: 最终评分
        
        Returns:
            仓位系数 (0.0 / 1.0 / 1.2 / 1.5)
        
        规则:
        - >= 100: MTF 共振级, 1.5x
        - >= 60: 强支撑级, 1.2x
        - >= 30: 基准级, 1.0x
        - < 30: 不开仓, 0.0x
        """
        thresholds = self.config.get("score_thresholds", {})
        mtf_threshold = float(thresholds.get("mtf_resonance", 100))
        strong_threshold = float(thresholds.get("strong", 60))
        normal_threshold = float(thresholds.get("normal", 30))
        
        if final_score >= mtf_threshold:
            return 1.5
        elif final_score >= strong_threshold:
            return 1.2
        elif final_score >= normal_threshold:
            return 1.0
        else:
            return 0.0


def determine_trend(
    klines: List[Dict],
    ema_fast: int = 144,
    ema_slow: int = 169,
) -> TrendState:
    """
    判断趋势状态 (基于 EMA 144/169 隧道)
    
    Args:
        klines: K 线数据
        ema_fast: 快线周期 (默认 144)
        ema_slow: 慢线周期 (默认 169)
    
    Returns:
        趋势状态
    """
    if not klines or len(klines) < max(ema_fast, ema_slow):
        return TrendState.NEUTRAL
    
    # 计算 EMA
    closes = [float(k.get("close", 0)) for k in klines]
    
    ema_fast_val = _calculate_ema(closes, ema_fast)
    ema_slow_val = _calculate_ema(closes, ema_slow)
    current_price = closes[-1]
    
    # 判断趋势
    ema_mid = (ema_fast_val + ema_slow_val) / 2
    
    if current_price > ema_mid * 1.01:  # 1% 容差
        return TrendState.BULLISH
    elif current_price < ema_mid * 0.99:
        return TrendState.BEARISH
    else:
        return TrendState.NEUTRAL


def _calculate_ema(data: List[float], period: int) -> float:
    """
    计算 EMA
    
    Args:
        data: 价格数据
        period: EMA 周期
    
    Returns:
        EMA 值
    """
    if len(data) < period:
        return sum(data) / len(data) if data else 0
    
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period  # 初始 SMA
    
    for price in data[period:]:
        ema = (price - ema) * multiplier + ema
    
    return ema
