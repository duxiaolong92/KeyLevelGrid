"""
MTF 水位融合器 (LEVEL_GENERATION.md v3.1.0)

将多时间框架的分形点合并为统一的水位候选列表，
识别共振水位并计算综合评分。
"""

from typing import List, Dict, Optional, Tuple
from key_level_grid.core.scoring import (
    FractalPoint,
    MTFLevelCandidate,
    LevelScore,
)


class MTFMerger:
    """
    MTF 水位融合器
    
    将 1d, 4h, 15m 的分形点合并，
    识别多框架共振的强水位。
    """
    
    def __init__(
        self,
        merge_tolerance: float = 0.005,  # 0.5% 合并容差
        timeframe_priority: Optional[List[str]] = None,
        config: Optional[Dict] = None,
    ):
        """
        初始化 MTF 融合器
        
        Args:
            merge_tolerance: 价格合并容差
            timeframe_priority: 时间框架优先级 (高优先级的价格作为合并价格)
            config: 配置字典
        """
        self.merge_tolerance = merge_tolerance
        self.timeframe_priority = timeframe_priority or ["1d", "4h", "15m"]
        self.config = config or {}
    
    def merge_fractals(
        self,
        fractals_by_tf: Dict[str, List[FractalPoint]],
    ) -> List[MTFLevelCandidate]:
        """
        合并多时间框架的分形点
        
        Args:
            fractals_by_tf: {"1d": [...], "4h": [...], "15m": [...]}
        
        Returns:
            MTF 水位候选列表 (按价格降序)
        """
        # 收集所有分形点
        all_fractals: List[FractalPoint] = []
        for tf in self.timeframe_priority:
            if tf in fractals_by_tf:
                all_fractals.extend(fractals_by_tf[tf])
        
        if not all_fractals:
            return []
        
        # 按价格降序排序
        all_fractals.sort(key=lambda f: f.price, reverse=True)
        
        # 合并相近价格的分形点
        candidates: List[MTFLevelCandidate] = []
        used = set()  # 已合并的分形点索引
        
        for i, fractal in enumerate(all_fractals):
            if i in used:
                continue
            
            # 收集相近价格的分形点
            group = [fractal]
            used.add(i)
            
            for j, other in enumerate(all_fractals):
                if j in used:
                    continue
                
                # 检查价格是否在容差范围内
                if self._is_price_near(fractal.price, other.price):
                    group.append(other)
                    used.add(j)
            
            # 创建候选
            candidate = self._create_candidate(group)
            candidates.append(candidate)
        
        # 按价格降序排列
        return sorted(candidates, key=lambda c: c.merged_price, reverse=True)
    
    def _is_price_near(self, p1: float, p2: float) -> bool:
        """判断两个价格是否接近"""
        if p1 <= 0 or p2 <= 0:
            return False
        return abs(p1 - p2) / max(p1, p2) <= self.merge_tolerance
    
    def _create_candidate(
        self,
        fractals: List[FractalPoint],
    ) -> MTFLevelCandidate:
        """
        从分形点组创建候选
        
        Args:
            fractals: 同一价位的分形点列表
        
        Returns:
            MTFLevelCandidate
        """
        # 收集来源时间框架
        source_timeframes = list(set(f.timeframe for f in fractals))
        
        # 确定合并后的价格 (优先使用高优先级时间框架的价格)
        merged_price = self._get_priority_price(fractals)
        
        # 判断是否共振
        is_resonance = len(source_timeframes) > 1
        
        return MTFLevelCandidate(
            price=fractals[0].price,
            source_fractals=fractals,
            source_timeframes=source_timeframes,
            is_resonance=is_resonance,
            merged_price=merged_price,
        )
    
    def _get_priority_price(self, fractals: List[FractalPoint]) -> float:
        """
        获取优先级最高的时间框架的价格
        
        Args:
            fractals: 分形点列表
        
        Returns:
            合并后的价格
        """
        for tf in self.timeframe_priority:
            for fractal in fractals:
                if fractal.timeframe == tf:
                    return fractal.price
        
        # 默认返回第一个
        return fractals[0].price if fractals else 0
    
    def filter_by_type(
        self,
        candidates: List[MTFLevelCandidate],
        fractal_type: str,
    ) -> List[MTFLevelCandidate]:
        """
        按分形类型过滤候选
        
        Args:
            candidates: 候选列表
            fractal_type: "HIGH" | "LOW"
        
        Returns:
            过滤后的候选列表
        """
        result = []
        
        for candidate in candidates:
            # 检查是否有指定类型的分形点
            has_type = any(
                f.type == fractal_type 
                for f in candidate.source_fractals
            )
            if has_type:
                result.append(candidate)
        
        return result
    
    def filter_by_distance(
        self,
        candidates: List[MTFLevelCandidate],
        current_price: float,
        min_distance_pct: float = 0.001,  # 0.1%
        max_distance_pct: float = 0.30,   # 30%
    ) -> List[MTFLevelCandidate]:
        """
        按距离当前价格过滤候选
        
        Args:
            candidates: 候选列表
            current_price: 当前价格
            min_distance_pct: 最小距离百分比
            max_distance_pct: 最大距离百分比
        
        Returns:
            过滤后的候选列表
        """
        if current_price <= 0:
            return candidates
        
        result = []
        
        for candidate in candidates:
            distance = abs(candidate.merged_price - current_price) / current_price
            
            if min_distance_pct <= distance <= max_distance_pct:
                result.append(candidate)
        
        return result


def select_top_levels(
    candidates: List[MTFLevelCandidate],
    scores: Dict[float, LevelScore],  # price -> score
    max_levels: int = 10,
) -> List[Tuple[float, LevelScore]]:
    """
    选择评分最高的 N 个水位
    
    Args:
        candidates: 候选列表
        scores: 价格到评分的映射
        max_levels: 最大水位数
    
    Returns:
        [(price, score), ...] 按评分降序
    """
    scored = []
    
    for candidate in candidates:
        price = candidate.merged_price
        if price in scores:
            scored.append((price, scores[price]))
    
    # 按评分降序排列
    scored.sort(key=lambda x: x[1].final_score, reverse=True)
    
    # 取前 N 个
    return scored[:max_levels]
