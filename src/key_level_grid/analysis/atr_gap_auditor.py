"""
ATR 空间硬约束审计器 (LEVEL_GENERATION.md v3.2.5)

系统的最高物理准则，所有生成的候选水位必须通过此审计器。

核心功能:
1. 密度审计: 间距 < 0.5×ATR → 能量优先裁剪 (保留 POC/HVN，剔除 LVN)
2. 稀疏审计: 间距 > 3.0×ATR → 递归补全

补全优先级:
1. 战术种子召回 (L4 分形池)
2. VPVR 能量锚点 (POC/HVN)
3. 斐波那契数学兜底 (0.618)
"""

import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from key_level_grid.core.triggers import ATRConfig, FilledLevel
from key_level_grid.core.scoring import (
    FractalPoint,
    VPVRData,
    MTFLevelCandidate,
    VolumeZone,
)


logger = logging.getLogger(__name__)


@dataclass
class AuditResult:
    """
    ATR 审计结果
    
    记录审计过程中的裁剪和补全操作，
    便于诊断和回测分析。
    """
    original_count: int                   # 原始水位数
    final_count: int                      # 最终水位数
    trimmed_count: int                    # 裁剪数量
    filled_count: int                     # 补全数量
    trimmed_prices: List[float] = field(default_factory=list)   # 被裁剪的价格
    filled_levels: List[FilledLevel] = field(default_factory=list)  # 补全的水位
    atr_value: float = 0.0                # 使用的 ATR 值
    
    def to_dict(self) -> dict:
        return {
            "original_count": self.original_count,
            "final_count": self.final_count,
            "trimmed_count": self.trimmed_count,
            "filled_count": self.filled_count,
            "trimmed_prices": self.trimmed_prices,
            "filled_levels": [f.to_dict() for f in self.filled_levels],
            "atr_value": self.atr_value,
        }


class ATRGapAuditor:
    """
    ATR 空间硬约束审计器 (V3.2.5 核心)
    
    职责:
    1. 计算 ATR 值
    2. 执行密度审计 (裁剪过密水位)
    3. 执行稀疏审计 (补全过稀区间)
    4. 返回审计后的水位列表
    """
    
    def __init__(
        self,
        config: Optional[ATRConfig] = None,
        vpvr_data: Optional[VPVRData] = None,
        tactical_pool: Optional[List[FractalPoint]] = None,
    ):
        """
        初始化 ATR 审计器
        
        Args:
            config: ATR 约束配置
            vpvr_data: VPVR 数据 (用于能量优先裁剪和补全)
            tactical_pool: L4 战术层分形池 (用于补全)
        """
        self.config = config or ATRConfig()
        self.vpvr_data = vpvr_data
        self.tactical_pool = tactical_pool or []
        
        # 缓存 ATR 值
        self._cached_atr: Optional[float] = None
    
    def calculate_atr(
        self,
        klines: List[Dict],
        period: Optional[int] = None,
    ) -> float:
        """
        计算 ATR (Average True Range)
        
        ATR = SMA(TR, period)
        TR = max(high - low, |high - prev_close|, |low - prev_close|)
        
        Args:
            klines: K 线数据
            period: ATR 周期 (默认使用配置值)
        
        Returns:
            ATR 值
        """
        period = period or self.config.atr_period
        
        if not klines or len(klines) < period + 1:
            logger.warning(f"K 线数据不足以计算 ATR: {len(klines)} < {period + 1}")
            return 0.0
        
        true_ranges = []
        
        for i in range(1, len(klines)):
            high = float(klines[i].get("high", 0))
            low = float(klines[i].get("low", 0))
            prev_close = float(klines[i - 1].get("close", 0))
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)
        
        # 取最近 period 个 TR 的平均值
        recent_tr = true_ranges[-period:]
        atr = sum(recent_tr) / len(recent_tr) if recent_tr else 0.0
        
        self._cached_atr = atr
        return atr
    
    def audit(
        self,
        candidates: List[MTFLevelCandidate],
        atr: Optional[float] = None,
        klines: Optional[List[Dict]] = None,
    ) -> Tuple[List[MTFLevelCandidate], AuditResult]:
        """
        执行完整的 ATR 审计流程
        
        流程:
        1. 计算 ATR (如未提供)
        2. 密度审计 (裁剪过密)
        3. 稀疏审计 (补全过稀)
        
        Args:
            candidates: 候选水位列表 (必须按价格降序排列)
            atr: ATR 值 (可选，如未提供则从 klines 计算)
            klines: K 线数据 (用于计算 ATR)
        
        Returns:
            (审计后的水位列表, 审计结果)
        """
        if not self.config.enabled:
            return candidates, AuditResult(
                original_count=len(candidates),
                final_count=len(candidates),
                trimmed_count=0,
                filled_count=0,
            )
        
        # 计算 ATR
        if atr is None:
            if klines:
                atr = self.calculate_atr(klines)
            elif self._cached_atr:
                atr = self._cached_atr
            else:
                logger.warning("无法计算 ATR，跳过审计")
                return candidates, AuditResult(
                    original_count=len(candidates),
                    final_count=len(candidates),
                    trimmed_count=0,
                    filled_count=0,
                )
        
        original_count = len(candidates)
        
        # 1. 密度审计
        trimmed, trimmed_prices = self._audit_density(candidates, atr)
        
        # 2. 稀疏审计
        filled, filled_levels = self._audit_sparse(trimmed, atr)
        
        # 按价格降序排列
        filled.sort(key=lambda c: c.merged_price, reverse=True)
        
        result = AuditResult(
            original_count=original_count,
            final_count=len(filled),
            trimmed_count=len(trimmed_prices),
            filled_count=len(filled_levels),
            trimmed_prices=trimmed_prices,
            filled_levels=filled_levels,
            atr_value=atr,
        )
        
        logger.info(
            f"ATR 审计完成: 原始={original_count}, 裁剪={result.trimmed_count}, "
            f"补全={result.filled_count}, 最终={result.final_count}, ATR={atr:.2f}"
        )
        
        return filled, result
    
    def _audit_density(
        self,
        candidates: List[MTFLevelCandidate],
        atr: float,
    ) -> Tuple[List[MTFLevelCandidate], List[float]]:
        """
        密度审计: 裁剪过密水位
        
        规则:
        - 间距 < 0.5×ATR 时触发裁剪
        - 裁剪准则: 比较 volume_weight，保留 POC/HVN，剔除 LVN
        
        Args:
            candidates: 候选水位列表 (降序)
            atr: ATR 值
        
        Returns:
            (裁剪后的列表, 被裁剪的价格列表)
        """
        if not candidates or atr <= 0:
            return candidates, []
        
        min_gap = self.config.gap_min_atr_ratio * atr
        result = []
        trimmed = []
        
        for candidate in candidates:
            if not result:
                result.append(candidate)
                continue
            
            # 计算与上一个保留水位的间距
            prev = result[-1]
            gap = abs(prev.merged_price - candidate.merged_price)
            
            if gap >= min_gap:
                # 间距足够，保留
                result.append(candidate)
            else:
                # 间距过密，需要裁剪一个
                # 比较能量权重，保留高能量的
                prev_energy = self._get_energy_score(prev)
                curr_energy = self._get_energy_score(candidate)
                
                if curr_energy > prev_energy:
                    # 当前水位能量更高，替换上一个
                    trimmed.append(result.pop().merged_price)
                    result.append(candidate)
                else:
                    # 上一个水位能量更高，裁剪当前
                    trimmed.append(candidate.merged_price)
        
        logger.debug(f"密度审计: 裁剪 {len(trimmed)} 个水位 (min_gap={min_gap:.2f})")
        return result, trimmed
    
    def _audit_sparse(
        self,
        candidates: List[MTFLevelCandidate],
        atr: float,
    ) -> Tuple[List[MTFLevelCandidate], List[FilledLevel]]:
        """
        稀疏审计: 补全过稀区间
        
        规则:
        - 间距 > 3.0×ATR 时触发补全
        - 补全优先级: 战术种子 → VPVR → 斐波那契
        
        Args:
            candidates: 候选水位列表 (降序)
            atr: ATR 值
        
        Returns:
            (补全后的列表, 补全的水位列表)
        """
        if not candidates or len(candidates) < 2 or atr <= 0:
            return candidates, []
        
        max_gap = self.config.gap_max_atr_ratio * atr
        result = list(candidates)
        filled = []
        
        # 递归检查和补全
        changed = True
        max_iterations = 100  # 防止无限循环
        iteration = 0
        
        while changed and iteration < max_iterations:
            changed = False
            iteration += 1
            
            new_result = []
            i = 0
            
            while i < len(result):
                new_result.append(result[i])
                
                if i < len(result) - 1:
                    upper = result[i].merged_price
                    lower = result[i + 1].merged_price
                    gap = upper - lower
                    
                    if gap > max_gap:
                        # 需要补全
                        fill_level = self._fill_gap(upper, lower, atr)
                        if fill_level:
                            # 创建新的候选水位
                            new_candidate = MTFLevelCandidate(
                                price=fill_level.price,
                                source_fractals=[],
                                source_timeframes=["filled"],
                                is_resonance=False,
                                merged_price=fill_level.price,
                            )
                            new_candidate.score = fill_level.score
                            new_candidate.fill_type = fill_level.fill_type
                            
                            new_result.append(new_candidate)
                            filled.append(fill_level)
                            changed = True
                
                i += 1
            
            result = sorted(new_result, key=lambda c: c.merged_price, reverse=True)
        
        logger.debug(f"稀疏审计: 补全 {len(filled)} 个水位 (max_gap={max_gap:.2f})")
        return result, filled
    
    def _fill_gap(
        self,
        upper: float,
        lower: float,
        atr: float,
    ) -> Optional[FilledLevel]:
        """
        填补空隙
        
        优先级:
        1. 战术种子召回 (L4)
        2. VPVR 能量锚点 (POC/HVN)
        3. 斐波那契数学兜底
        
        Args:
            upper: 空隙上界
            lower: 空隙下界
            atr: ATR 值
        
        Returns:
            补全的水位或 None
        """
        for priority in self.config.fill_priority:
            if priority == "tactical":
                fill = self._fill_with_tactical(upper, lower)
                if fill:
                    return fill
            
            elif priority == "vpvr":
                fill = self._fill_with_vpvr(upper, lower)
                if fill:
                    return fill
            
            elif priority == "fibonacci":
                if self.config.fibonacci_enabled:
                    return self._fill_with_fibonacci(upper, lower)
        
        return None
    
    def _fill_with_tactical(
        self,
        upper: float,
        lower: float,
    ) -> Optional[FilledLevel]:
        """
        战术种子召回 (L4 分形池)
        
        在空隙范围内搜索 L4 战术层的分形点，
        选择评分最高的作为补全水位。
        """
        if not self.tactical_pool:
            return None
        
        # 搜索空隙内的战术分形点
        candidates = [
            f for f in self.tactical_pool
            if lower < f.price < upper
        ]
        
        if not candidates:
            return None
        
        # 选择周期最大的 (评分最高)
        best = max(candidates, key=lambda f: f.period)
        
        # 计算评分 (基于周期)
        score = self._calculate_tactical_score(best)
        
        return FilledLevel(
            price=best.price,
            fill_type="tactical",
            score=score,
            source_layer="l4",
            gap_upper=upper,
            gap_lower=lower,
        )
    
    def _fill_with_vpvr(
        self,
        upper: float,
        lower: float,
    ) -> Optional[FilledLevel]:
        """
        VPVR 能量锚点召回
        
        在空隙范围内搜索 POC 或 HVN 节点。
        """
        if not self.vpvr_data:
            return None
        
        # 检查 POC 是否在空隙内
        if lower < self.vpvr_data.poc_price < upper:
            return FilledLevel(
                price=self.vpvr_data.poc_price,
                fill_type="vpvr",
                score=80,  # POC 高评分
                vpvr_zone="POC",
                gap_upper=upper,
                gap_lower=lower,
            )
        
        # 搜索 HVN 区域
        for hvn_low, hvn_high in self.vpvr_data.hvn_zones:
            hvn_mid = (hvn_low + hvn_high) / 2
            if lower < hvn_mid < upper:
                return FilledLevel(
                    price=hvn_mid,
                    fill_type="vpvr",
                    score=60,  # HVN 中等评分
                    vpvr_zone="HVN",
                    gap_upper=upper,
                    gap_lower=lower,
                )
        
        return None
    
    def _fill_with_fibonacci(
        self,
        upper: float,
        lower: float,
    ) -> FilledLevel:
        """
        斐波那契数学兜底
        
        在空隙的 0.618 位置插入补全水位。
        """
        fill_price = self.config.get_fibonacci_fill_price(upper, lower)
        
        return FilledLevel(
            price=fill_price,
            fill_type="fibonacci",
            score=self.config.fibonacci_fill_score,
            gap_upper=upper,
            gap_lower=lower,
        )
    
    def _get_energy_score(self, candidate: MTFLevelCandidate) -> float:
        """
        获取水位的能量评分
        
        用于密度裁剪时的比较。
        """
        # 基础分
        score = getattr(candidate, 'score', 0) or 0
        
        # VPVR 加成
        if self.vpvr_data:
            zone = self.vpvr_data.get_zone_type(candidate.merged_price)
            if zone == VolumeZone.HVN:
                score *= 1.5
            elif zone == VolumeZone.LVN:
                score *= 0.4
            
            # POC 特殊加成
            poc_tolerance = candidate.merged_price * 0.003  # 0.3%
            if abs(candidate.merged_price - self.vpvr_data.poc_price) < poc_tolerance:
                score *= 1.8
        
        # 共振加成
        if candidate.is_resonance:
            score *= 1.2
        
        return score
    
    def _calculate_tactical_score(self, fractal: FractalPoint) -> int:
        """
        计算战术分形点的评分
        """
        # 基于周期的基础分
        period_scores = {
            144: 60,
            55: 50,
            34: 40,
        }
        base = period_scores.get(fractal.period, 35)
        
        # VPVR 调整
        if self.vpvr_data:
            zone = self.vpvr_data.get_zone_type(fractal.price)
            if zone == VolumeZone.HVN:
                base = int(base * 1.3)
            elif zone == VolumeZone.LVN:
                base = int(base * 0.7)
        
        return base
    
    def set_tactical_pool(self, pool: List[FractalPoint]) -> None:
        """设置 L4 战术层分形池"""
        self.tactical_pool = pool
    
    def set_vpvr_data(self, vpvr: VPVRData) -> None:
        """设置 VPVR 数据"""
        self.vpvr_data = vpvr


def create_auditor_from_config(config: Dict) -> ATRGapAuditor:
    """
    从配置创建 ATR 审计器
    
    Args:
        config: 完整配置字典
    
    Returns:
        ATRGapAuditor 实例
    """
    atr_config_dict = config.get("level_generation", {}).get("atr_constraint", {})
    atr_config = ATRConfig.from_dict(atr_config_dict)
    
    return ATRGapAuditor(config=atr_config)
