"""
MTF 水位生成引擎 (LEVEL_GENERATION.md v3.1.0)

主入口类，集成所有子模块:
- FractalExtractor: 分形点提取
- VPVRAnalyzer: 成交量分布分析
- PsychologyMatcher: 心理位匹配
- LevelScorer: 评分计算
- MTFMerger: 多框架融合

核心流程:
1. 从 MTF K 线数据提取分形点
2. 合并相近价位，识别共振水位
3. 对齐心理位 (斐波那契/整数位)
4. 计算综合评分
5. 按评分筛选最终水位
"""

import logging
from typing import List, Dict, Optional, Tuple

from key_level_grid.core.scoring import (
    LevelScore,
    FractalPoint,
    VPVRData,
    MTFLevelCandidate,
    TrendState,
)
from key_level_grid.core.triggers import ManualBoundary
from key_level_grid.analysis.fractal import FractalExtractor, get_anchor_price
from key_level_grid.analysis.vpvr import VPVRAnalyzer
from key_level_grid.analysis.psychology import PsychologyMatcher
from key_level_grid.analysis.scorer import LevelScorer, determine_trend
from key_level_grid.analysis.mtf_merger import MTFMerger, select_top_levels


logger = logging.getLogger(__name__)


class LevelCalculator:
    """
    MTF 水位生成引擎
    
    V3.0 核心模块，负责:
    - 从多时间框架 K 线数据生成目标水位
    - 计算每个水位的综合评分
    - 支持手动边界和距离过滤
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """
        初始化水位计算器
        
        Args:
            config: 配置字典 (从 config.yaml 加载)
        """
        self.config = config or {}
        level_gen_config = self.config.get("level_generation", {})
        
        # 初始化子模块
        self.fractal_extractor = FractalExtractor(
            fibonacci_lookback=level_gen_config.get("fibonacci_lookback"),
            config=level_gen_config,
        )
        
        self.vpvr_analyzer = VPVRAnalyzer(
            config=level_gen_config,
        )
        
        self.psychology_matcher = PsychologyMatcher(
            config=level_gen_config,
        )
        
        self.scorer = LevelScorer(config=level_gen_config)
        
        self.mtf_merger = MTFMerger(
            merge_tolerance=float(
                self.config.get("resistance", {}).get("merge_tolerance", 0.005)
            ),
            timeframe_priority=level_gen_config.get("timeframes", ["1d", "4h", "15m"]),
            config=level_gen_config,
        )
        
        # 距离过滤配置
        resistance_config = self.config.get("resistance", {})
        self.min_distance_pct = float(resistance_config.get("min_distance_pct", 0.001))
        self.max_distance_pct = float(resistance_config.get("max_distance_pct", 0.30))
        
        # 手动边界
        boundary_config = level_gen_config.get("manual_boundary", {})
        self.manual_boundary = ManualBoundary(
            enabled=boundary_config.get("enabled", False),
            upper_price=boundary_config.get("upper_price"),
            lower_price=boundary_config.get("lower_price"),
            mode=boundary_config.get("mode", "strict"),
            buffer_pct=float(boundary_config.get("buffer_pct", 0.0)),
        )
        
        # 评分阈值
        scoring_config = level_gen_config.get("scoring", {})
        self.min_score_threshold = float(scoring_config.get("min_score_threshold", 30))
        self.display_score_threshold = float(scoring_config.get("display_score_threshold", 0))
    
    def generate_target_levels(
        self,
        klines_by_tf: Dict[str, List[Dict]],
        current_price: float,
        role: str = "support",
        max_levels: int = 10,
    ) -> Optional[List[Tuple[float, LevelScore]]]:
        """
        生成目标水位列表
        
        Args:
            klines_by_tf: 多时间框架 K 线数据 {"1d": [...], "4h": [...], "15m": [...]}
            current_price: 当前价格
            role: "support" | "resistance"
            max_levels: 最大水位数
        
        Returns:
            [(price, LevelScore), ...] 按价格降序排列
            如果数据不足或出错，返回 None
        """
        # 验证输入
        if not klines_by_tf or current_price <= 0:
            logger.warning("Invalid input: empty klines or invalid price")
            return None
        
        # 1. 提取分形点
        fractals_by_tf = self.fractal_extractor.extract_from_mtf(klines_by_tf)
        
        total_fractals = sum(len(f) for f in fractals_by_tf.values())
        if total_fractals == 0:
            logger.warning("No fractals extracted from klines")
            return None
        
        logger.debug(f"Extracted {total_fractals} fractals from MTF data")
        
        # 2. 合并多框架分形点
        candidates = self.mtf_merger.merge_fractals(fractals_by_tf)
        
        # 3. 按角色过滤 (支撑位取低点，阻力位取高点)
        if role == "support":
            candidates = self.mtf_merger.filter_by_type(candidates, "LOW")
            # 支撑位只取低于当前价的
            candidates = [c for c in candidates if c.merged_price < current_price]
        else:
            candidates = self.mtf_merger.filter_by_type(candidates, "HIGH")
            # 阻力位只取高于当前价的
            candidates = [c for c in candidates if c.merged_price > current_price]
        
        # 4. 距离过滤
        candidates = self.mtf_merger.filter_by_distance(
            candidates,
            current_price,
            self.min_distance_pct,
            self.max_distance_pct,
        )
        
        if not candidates:
            logger.warning(f"No candidates after filtering for role={role}")
            return None
        
        logger.debug(f"After filtering: {len(candidates)} candidates")
        
        # 5. VPVR 分析 (使用最长周期的 K 线)
        main_tf = self._get_main_timeframe(klines_by_tf)
        vpvr = self.vpvr_analyzer.analyze(klines_by_tf.get(main_tf, []))
        
        # 6. 获取心理位
        psychology_levels = self.psychology_matcher.find_all_psychology_levels(
            klines_by_tf.get(main_tf, [])
        )
        
        # 7. 判断趋势
        trend_state = determine_trend(klines_by_tf.get(main_tf, []))
        logger.debug(f"Trend state: {trend_state}")
        
        # 8. 计算评分
        scores: Dict[float, LevelScore] = {}
        
        for candidate in candidates:
            # 尝试心理位匹配 (仅用于评分加成，不吸附价格)
            snapped_price, psy_match = self.psychology_matcher.snap_to_psychology(
                candidate.merged_price,
                psychology_levels,
            )
            
            # 注意: 不再覆盖 candidate.merged_price
            # 保留原始分形价格，仅在评分时给予心理位加成
            
            # 计算评分
            score = self.scorer.calculate_score(
                candidate=candidate,
                vpvr=vpvr,
                trend_state=trend_state,
                role=role,
                psychology_anchor=snapped_price if psy_match else None,
            )
            
            scores[candidate.merged_price] = score
        
        # 9. 过滤低评分水位
        filtered_candidates = []
        for candidate in candidates:
            price = candidate.merged_price
            if price in scores:
                score = scores[price]
                if score.final_score >= self.min_score_threshold:
                    filtered_candidates.append(candidate)
                else:
                    logger.debug(f"Filtered level {price:.2f} with score {score.final_score:.1f} < {self.min_score_threshold}")
        
        if not filtered_candidates:
            logger.warning(f"No candidates above min_score_threshold={self.min_score_threshold}")
            return None
        
        # 10. 应用手动边界过滤
        if self.manual_boundary.enabled:
            prices_before = [c.merged_price for c in filtered_candidates]
            prices_after = self.manual_boundary.filter_levels(prices_before)
            
            # 更新候选列表
            filtered_candidates = [
                c for c in filtered_candidates 
                if c.merged_price in prices_after
            ]
            
            if not filtered_candidates:
                logger.warning("No candidates after manual boundary filter")
                return None
            
            logger.debug(f"After boundary filter: {len(prices_before)} -> {len(filtered_candidates)} levels")
        
        # 11. 选择评分最高的水位
        top_levels = select_top_levels(filtered_candidates, scores, max_levels)
        
        # 12. 按价格降序排列
        top_levels.sort(key=lambda x: x[0], reverse=True)
        
        logger.info(f"Generated {len(top_levels)} target levels for role={role}")
        return top_levels
    
    def _get_main_timeframe(self, klines_by_tf: Dict[str, List[Dict]]) -> str:
        """获取主时间框架 (数据最多的)"""
        if not klines_by_tf:
            return "4h"
        
        # 优先使用 4h
        if "4h" in klines_by_tf:
            return "4h"
        
        # 否则使用数据最多的
        return max(klines_by_tf, key=lambda k: len(klines_by_tf[k]))
    
    def _create_default_score(self, price: float, role: str) -> LevelScore:
        """为手动边界创建默认评分"""
        return LevelScore(
            base_score=30,
            source_timeframes=["manual"],
            source_periods=[],
            final_score=30,
        )
    
    def get_anchor_price(self, klines: List[Dict], lookback: int = 55) -> Optional[float]:
        """
        获取锚点价格
        
        Args:
            klines: K 线数据
            lookback: 回溯周期
        
        Returns:
            锚点价格
        """
        return get_anchor_price(klines, lookback)
    
    def refresh_scores(
        self,
        existing_levels: List[Tuple[float, LevelScore]],
        klines_by_tf: Dict[str, List[Dict]],
        current_price: float,
        role: str = "support",
    ) -> List[Tuple[float, LevelScore]]:
        """
        刷新现有水位的评分 (不改变水位价格)
        
        用于 15m 战术层更新，只更新评分不触发重构。
        
        Args:
            existing_levels: 现有水位列表
            klines_by_tf: 最新 K 线数据
            current_price: 当前价格
            role: "support" | "resistance"
        
        Returns:
            更新评分后的水位列表
        """
        if not existing_levels:
            return []
        
        # 重新提取分形点
        fractals_by_tf = self.fractal_extractor.extract_from_mtf(klines_by_tf)
        
        # VPVR 分析
        main_tf = self._get_main_timeframe(klines_by_tf)
        vpvr = self.vpvr_analyzer.analyze(klines_by_tf.get(main_tf, []))
        
        # 判断趋势
        trend_state = determine_trend(klines_by_tf.get(main_tf, []))
        
        # 重新计算评分
        result = []
        
        for price, old_score in existing_levels:
            # 找到最近的分形点
            candidate = self._find_nearest_candidate(
                price, fractals_by_tf, old_score.source_timeframes
            )
            
            if candidate:
                new_score = self.scorer.calculate_score(
                    candidate=candidate,
                    vpvr=vpvr,
                    trend_state=trend_state,
                    role=role,
                    psychology_anchor=old_score.psychology_anchor,
                )
            else:
                # 保持旧评分但更新趋势系数
                new_score = old_score
                new_score.trend_state = trend_state
            
            result.append((price, new_score))
        
        return result
    
    def _find_nearest_candidate(
        self,
        price: float,
        fractals_by_tf: Dict[str, List[FractalPoint]],
        source_timeframes: List[str],
        tolerance: float = 0.01,
    ) -> Optional[MTFLevelCandidate]:
        """找到最近的分形点候选"""
        all_fractals = []
        for tf in source_timeframes:
            if tf in fractals_by_tf:
                all_fractals.extend(fractals_by_tf[tf])
        
        if not all_fractals:
            return None
        
        # 找最近的分形点
        nearest = min(
            all_fractals,
            key=lambda f: abs(f.price - price) / price,
        )
        
        if abs(nearest.price - price) / price > tolerance:
            return None
        
        return MTFLevelCandidate(
            price=price,
            source_fractals=[nearest],
            source_timeframes=[nearest.timeframe],
            merged_price=price,
        )
