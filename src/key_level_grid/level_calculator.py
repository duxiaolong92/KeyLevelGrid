"""
MTF 水位生成引擎 (LEVEL_GENERATION.md v3.2.5)

主入口类，集成所有子模块:
- FractalExtractor: 分形点提取 (四层级)
- VPVRAnalyzer: 成交量分布分析
- PsychologyMatcher: 心理位匹配
- LevelScorer: 评分计算 (V3.2.5 权重)
- MTFMerger: 多框架融合
- ATRGapAuditor: ATR 空间硬约束 (V3.2.5 核心)

核心流程:
1. 从四层级 K 线数据提取分形点
2. 合并相近价位，识别共振水位
3. ATR 空间审计 (密度裁剪 + 稀疏补全)
4. 对齐心理位 (斐波那契/整数位)
5. 计算综合评分
6. 按评分筛选最终水位
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
from key_level_grid.core.triggers import (
    ManualBoundary,
    ATRConfig,
)
from key_level_grid.analysis.fractal import (
    FractalExtractor,
    get_anchor_price,
    get_anchor_by_layer,
)
from key_level_grid.analysis.vpvr import VPVRAnalyzer
from key_level_grid.analysis.psychology import PsychologyMatcher
from key_level_grid.analysis.scorer import LevelScorer, determine_trend
from key_level_grid.analysis.mtf_merger import MTFMerger, select_top_levels
from key_level_grid.analysis.atr_gap_auditor import ATRGapAuditor, AuditResult


logger = logging.getLogger(__name__)


class LevelCalculator:
    """
    MTF 水位生成引擎 (V3.2.5)
    
    核心模块，负责:
    - 从四层级时间框架 K 线数据生成目标水位
    - 执行 ATR 空间硬约束审计
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
            timeframe_priority=self._get_timeframe_priority(level_gen_config),
            config=level_gen_config,
        )
        
        # V3.2.5: ATR 空间审计器
        atr_config_dict = level_gen_config.get("atr_constraint", {})
        self.atr_config = ATRConfig.from_dict(atr_config_dict)
        self.atr_auditor = ATRGapAuditor(config=self.atr_config)
        
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
        
        # 缓存最近的审计结果
        self._last_audit_result: Optional[AuditResult] = None
    
    def _get_timeframe_priority(self, config: Dict) -> List[str]:
        """获取时间框架优先级列表"""
        tf_config = config.get("timeframes", {})
        
        priority = []
        
        # L1 战略层
        l1 = tf_config.get("l1_strategy", {})
        if l1.get("enabled", True):
            priority.append(l1.get("interval", "1w"))
        
        # L2 骨架层
        l2 = tf_config.get("l2_skeleton", {})
        priority.append(l2.get("interval", "1d"))
        
        # L3 中继层
        l3 = tf_config.get("l3_relay", {})
        priority.append(l3.get("interval", "4h"))
        
        # L4 战术层
        l4 = tf_config.get("l4_tactical", {})
        if l4.get("enabled", True):
            priority.append(l4.get("interval", "15m"))
        
        return priority or ["1d", "4h", "15m"]
    
    def generate_target_levels(
        self,
        klines_by_tf: Dict[str, List[Dict]],
        current_price: float,
        role: str = "support",
        max_levels: int = 10,
        use_atr_audit: bool = True,
    ) -> Optional[List[Tuple[float, LevelScore]]]:
        """
        生成目标水位列表 (V3.2.5)
        
        Args:
            klines_by_tf: 多时间框架 K 线数据 {"1w": [...], "1d": [...], "4h": [...], "15m": [...]}
            current_price: 当前价格
            role: "support" | "resistance"
            max_levels: 最大水位数
            use_atr_audit: 是否启用 ATR 空间审计
        
        Returns:
            [(price, LevelScore), ...] 按价格降序排列
            如果数据不足或出错，返回 None
        """
        # 验证输入
        if not klines_by_tf or current_price <= 0:
            logger.warning("Invalid input: empty klines or invalid price")
            return None
        
        # 1. 提取分形点 (四层级)
        fractals_by_tf = self.fractal_extractor.extract_from_mtf(klines_by_tf)
        
        total_fractals = sum(len(f) for f in fractals_by_tf.values())
        if total_fractals == 0:
            logger.warning("No fractals extracted from klines")
            return None
        
        # 详细日志：分形点统计
        for tf, fractals in fractals_by_tf.items():
            highs = [f for f in fractals if f.type == "HIGH"]
            lows = [f for f in fractals if f.type == "LOW"]
            logger.debug(f"[{tf}] 分形点: {len(fractals)} 个 (HIGH={len(highs)}, LOW={len(lows)})")
            if role == "resistance" and highs:
                high_prices = [f.price for f in highs[:5]]
                logger.debug(f"[{tf}] 前5个 HIGH 价格: {high_prices}")
        
        logger.debug(f"Extracted {total_fractals} fractals from MTF data")
        
        # 2. 合并多框架分形点
        candidates = self.mtf_merger.merge_fractals(fractals_by_tf)
        logger.debug(f"合并后候选数: {len(candidates)}")
        
        # 3. 按角色过滤 (支撑位取低点，阻力位取高点)
        if role == "support":
            candidates = self.mtf_merger.filter_by_type(candidates, "LOW")
            # 支撑位只取低于当前价的
            candidates = [c for c in candidates if c.merged_price < current_price]
        else:
            before_type_filter = len(candidates)
            candidates = self.mtf_merger.filter_by_type(candidates, "HIGH")
            logger.debug(f"类型过滤 (HIGH): {before_type_filter} -> {len(candidates)}")
            
            # 阻力位只取高于当前价的
            before_price_filter = len(candidates)
            candidates = [c for c in candidates if c.merged_price > current_price]
            logger.debug(f"价格过滤 (>{current_price:.2f}): {before_price_filter} -> {len(candidates)}")
            
            if before_price_filter > 0 and len(candidates) == 0:
                # 所有 HIGH 都低于当前价
                all_high_prices = [c.merged_price for c in self.mtf_merger.filter_by_type(
                    self.mtf_merger.merge_fractals(fractals_by_tf), "HIGH"
                )]
                if all_high_prices:
                    logger.warning(
                        f"所有 HIGH 分形点 ({len(all_high_prices)} 个) 都低于当前价 {current_price:.2f}, "
                        f"最高: {max(all_high_prices):.2f}"
                    )
        
        # 4. 距离过滤
        before_distance = len(candidates)
        candidates = self.mtf_merger.filter_by_distance(
            candidates,
            current_price,
            self.min_distance_pct,
            self.max_distance_pct,
        )
        logger.debug(f"距离过滤 ({self.min_distance_pct*100:.1f}%-{self.max_distance_pct*100:.1f}%): {before_distance} -> {len(candidates)}")
        
        if not candidates:
            logger.warning(f"No candidates after filtering for role={role}")
            
            # V3.2.5: 阻力位备选方案 - 使用心理位
            if role == "resistance":
                fallback_levels = self._generate_fallback_resistance(
                    klines_by_tf, current_price, max_levels
                )
                if fallback_levels:
                    logger.info(f"[Fallback] 使用心理位生成 {len(fallback_levels)} 个阻力位")
                    return fallback_levels
            
            return None
        
        logger.debug(f"After filtering: {len(candidates)} candidates")
        
        # 5. V3.2.5: ATR 空间审计
        if use_atr_audit and self.atr_config.enabled:
            # 设置 VPVR 数据和战术池
            main_tf = self._get_main_timeframe(klines_by_tf)
            vpvr = self.vpvr_analyzer.analyze(klines_by_tf.get(main_tf, []))
            self.atr_auditor.set_vpvr_data(vpvr)
            
            # 设置 L4 战术池
            tactical_tf = self._get_tactical_timeframe(klines_by_tf)
            if tactical_tf and tactical_tf in fractals_by_tf:
                self.atr_auditor.set_tactical_pool(fractals_by_tf[tactical_tf])
            
            # 计算 ATR
            atr_tf = self.atr_config.atr_timeframe
            atr_klines = klines_by_tf.get(atr_tf, klines_by_tf.get(main_tf, []))
            atr = self.atr_auditor.calculate_atr(atr_klines)
            
            # 执行审计
            candidates, audit_result = self.atr_auditor.audit(candidates, atr)
            self._last_audit_result = audit_result
            
            # ⚠️ 重要: ATR 补全可能产生不符合方向的水位，需要再次过滤
            before_refilter = len(candidates)
            if role == "support":
                candidates = [c for c in candidates if c.merged_price < current_price]
            else:
                candidates = [c for c in candidates if c.merged_price > current_price]
            
            if before_refilter != len(candidates):
                logger.debug(f"ATR 补全后方向过滤: {before_refilter} -> {len(candidates)}")
            
            # 🆕 检查当前价格到最近水位之间是否有大空隙，需要补全
            if candidates:
                max_gap = self.atr_config.gap_max_atr_ratio * atr
                
                if role == "resistance":
                    # 阻力位：检查当前价格到最低阻力位的距离
                    nearest = min(c.merged_price for c in candidates)
                    gap = nearest - current_price
                    
                    if gap > max_gap:
                        logger.info(f"阻力位空隙过大: {current_price:.2f} -> {nearest:.2f} (gap={gap:.2f}, max={max_gap:.2f})")
                        # 在当前价格和最近阻力位之间补全
                        filled = self._fill_gap_to_price(current_price, nearest, atr, "resistance")
                        if filled:
                            candidates.extend(filled)
                            candidates = sorted(candidates, key=lambda c: c.merged_price, reverse=True)
                            logger.info(f"补全了 {len(filled)} 个近距离阻力位")
                else:
                    # 支撑位：检查最高支撑位到当前价格的距离
                    nearest = max(c.merged_price for c in candidates)
                    gap = current_price - nearest
                    
                    if gap > max_gap:
                        logger.info(f"支撑位空隙过大: {nearest:.2f} -> {current_price:.2f} (gap={gap:.2f}, max={max_gap:.2f})")
                        # 在最近支撑位和当前价格之间补全
                        filled = self._fill_gap_to_price(nearest, current_price, atr, "support")
                        if filled:
                            candidates.extend(filled)
                            candidates = sorted(candidates, key=lambda c: c.merged_price, reverse=True)
                            logger.info(f"补全了 {len(filled)} 个近距离支撑位")
            
            if not candidates:
                logger.warning("No candidates after ATR audit")
                return None
        else:
            # 不使用 ATR 审计时，仍需获取 VPVR
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
            
            # V3.2.5: 如果是补全水位，使用补全时的评分
            if hasattr(candidate, 'fill_type') and candidate.fill_type:
                if hasattr(candidate, 'score') and candidate.score:
                    score.final_score = max(score.final_score, candidate.score)
            
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
        """获取主时间框架 (L3 中继层 4h)"""
        if not klines_by_tf:
            return "4h"
        
        # 优先使用 4h (L3 中继层)
        if "4h" in klines_by_tf:
            return "4h"
        
        # 否则使用数据最多的
        return max(klines_by_tf, key=lambda k: len(klines_by_tf[k]))
    
    def _get_tactical_timeframe(self, klines_by_tf: Dict[str, List[Dict]]) -> Optional[str]:
        """获取战术时间框架 (L4 15m)"""
        if "15m" in klines_by_tf:
            return "15m"
        return None
    
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
    
    def get_anchor_by_layer(
        self,
        klines_by_layer: Dict[str, List[Dict]],
        anchor_layer: str = "l2",
        anchor_period: int = 55,
    ) -> Optional[float]:
        """
        按层级获取锚点价格 (V3.2.5)
        
        Args:
            klines_by_layer: {"l1": [...], "l2": [...], ...}
            anchor_layer: 锚点层级 (默认 "l2")
            anchor_period: 锚点回溯周期 (默认 55)
        
        Returns:
            锚点价格
        """
        return get_anchor_by_layer(klines_by_layer, anchor_layer, anchor_period)
    
    def get_last_audit_result(self) -> Optional[AuditResult]:
        """获取最近一次 ATR 审计结果"""
        return self._last_audit_result
    
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
        V3.2.5: 严禁修改挂单价格
        
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
    
    def _fill_gap_to_price(
        self,
        lower: float,
        upper: float,
        atr: float,
        role: str,
    ) -> List[MTFLevelCandidate]:
        """
        在当前价格和最近水位之间补全
        
        Args:
            lower: 区间下界
            upper: 区间上界
            atr: ATR 值
            role: "support" | "resistance"
        
        Returns:
            补全的候选水位列表
        """
        from key_level_grid.core.scoring import MTFLevelCandidate
        
        filled = []
        gap = upper - lower
        max_gap = self.atr_config.gap_max_atr_ratio * atr
        
        if gap <= max_gap:
            return filled
        
        # 计算需要多少个补全点
        # 使用 0.618 黄金分割递归补全
        fib_ratio = self.atr_config.fibonacci_fill_ratio
        fill_score = self.atr_config.fibonacci_fill_score
        
        def recursive_fill(lo: float, hi: float, depth: int = 0):
            if depth > 10:  # 防止无限递归
                return
            
            g = hi - lo
            if g <= max_gap:
                return
            
            # 在 0.618 位置插入
            if role == "resistance":
                # 阻力位：从低向高，在 lo + 0.618 * gap 处插入
                price = lo + fib_ratio * g
            else:
                # 支撑位：从高向低，在 hi - 0.618 * gap 处插入
                price = hi - fib_ratio * g
            
            candidate = MTFLevelCandidate(
                price=price,
                source_fractals=[],
                source_timeframes=["filled"],
                is_resonance=False,
                merged_price=price,
            )
            candidate.score = fill_score
            candidate.fill_type = "gap_to_price"
            filled.append(candidate)
            
            # 递归检查两侧
            recursive_fill(lo, price, depth + 1)
            recursive_fill(price, hi, depth + 1)
        
        recursive_fill(lower, upper)
        
        logger.debug(f"_fill_gap_to_price: 在 {lower:.2f} ~ {upper:.2f} 补全 {len(filled)} 个水位")
        
        return filled
    
    def _generate_fallback_resistance(
        self,
        klines_by_tf: Dict[str, List[Dict]],
        current_price: float,
        max_levels: int,
    ) -> Optional[List[Tuple[float, LevelScore]]]:
        """
        当无法从分形点提取阻力位时，使用备选方案生成
        
        备选策略:
        1. 基于 ATR 向上扩展心理位 (整数位、.500 位)
        2. 使用最近高点 + ATR 偏移
        """
        # 获取主时间框架 K 线计算 ATR
        main_tf = self._get_main_timeframe(klines_by_tf)
        klines = klines_by_tf.get(main_tf, [])
        
        if len(klines) < 14:
            return None
        
        # 计算 ATR
        atr = self.atr_auditor._calculate_atr(klines, 14)
        if atr <= 0:
            return None
        
        # 找到最近历史高点
        recent_high = max(float(k.get("high", 0)) for k in klines[-55:])
        
        # 生成阻力位: 使用心理位
        fallback_prices = []
        
        # 方法1: 从当前价向上找心理位
        base_price = current_price * (1 + 0.005)  # 至少 0.5% 以上
        
        # 根据价格量级确定心理位步长
        if current_price >= 10000:
            step = 1000  # BTC 级别: 每 $1000
        elif current_price >= 1000:
            step = 100   # ETH 级别: 每 $100
        elif current_price >= 100:
            step = 10    # 中等市值: 每 $10
        else:
            step = 1     # 小市值: 每 $1
        
        # 找下一个整数心理位
        next_round = (int(base_price / step) + 1) * step
        
        # 生成一系列心理位
        for i in range(max_levels * 2):
            price = next_round + i * step
            
            # 距离检查
            distance_pct = (price - current_price) / current_price
            if distance_pct < self.min_distance_pct:
                continue
            if distance_pct > self.max_distance_pct:
                break
            
            fallback_prices.append(price)
            if len(fallback_prices) >= max_levels:
                break
        
        # 如果心理位不够，补充 ATR 基础的阻力位
        if len(fallback_prices) < max_levels:
            base = recent_high if recent_high > current_price else current_price
            for i in range(1, max_levels + 1):
                price = base + i * atr * 0.5
                distance_pct = (price - current_price) / current_price
                if self.min_distance_pct <= distance_pct <= self.max_distance_pct:
                    if price not in fallback_prices:
                        fallback_prices.append(price)
                if len(fallback_prices) >= max_levels:
                    break
        
        if not fallback_prices:
            return None
        
        # 生成 LevelScore (备选阻力位固定评分较低)
        result = []
        for price in sorted(set(fallback_prices), reverse=True)[:max_levels]:
            score = LevelScore(
                base_score=35,  # 备选位基础分较低
                volume_weight=1.0,
                psychology_weight=1.2,  # 心理位加成
                trend_coefficient=1.0,
                mtf_coefficient=1.0,
                source_timeframes=["fallback"],
                is_resonance=False,
                psychology_anchor=price,  # 心理锚点
            )
            result.append((price, score))
        
        # 按价格降序排列
        result.sort(key=lambda x: x[0], reverse=True)
        
        logger.info(
            f"[Fallback] 生成 {len(result)} 个备选阻力位, "
            f"ATR={atr:.2f}, recent_high={recent_high:.2f}"
        )
        
        return result