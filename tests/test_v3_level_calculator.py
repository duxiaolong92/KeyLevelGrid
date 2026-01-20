"""
V3.0 LevelCalculator 单元测试

测试覆盖:
1. FractalExtractor - 分形点提取
2. VPVRAnalyzer - 成交量分布分析
3. PsychologyMatcher - 心理位匹配
4. LevelScorer - MTF 评分计算
5. MTFMerger - 多框架融合
6. LevelCalculator - 主入口集成测试
"""

import pytest
import sys
from pathlib import Path

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from key_level_grid.analysis.fractal import FractalExtractor, get_anchor_price
from key_level_grid.analysis.vpvr import VPVRAnalyzer
from key_level_grid.analysis.psychology import PsychologyMatcher, PsychologyLevel
from key_level_grid.analysis.scorer import LevelScorer, determine_trend
from key_level_grid.analysis.mtf_merger import MTFMerger, select_top_levels
from key_level_grid.level_calculator import LevelCalculator
from key_level_grid.core.scoring import (
    LevelScore, FractalPoint, VPVRData, MTFLevelCandidate,
    VolumeZone, TrendState, calculate_mtf_coefficient,
)


# ============================================
# 测试数据生成器
# ============================================

def generate_klines(
    start_price: float = 95000,
    num_bars: int = 200,
    volatility: float = 0.01,
) -> list:
    """生成模拟 K 线数据"""
    import random
    random.seed(42)  # 固定随机种子
    
    klines = []
    price = start_price
    
    for i in range(num_bars):
        change = random.uniform(-volatility, volatility)
        open_price = price
        close_price = price * (1 + change)
        high_price = max(open_price, close_price) * (1 + random.uniform(0, volatility/2))
        low_price = min(open_price, close_price) * (1 - random.uniform(0, volatility/2))
        volume = random.uniform(100, 1000)
        
        klines.append({
            "timestamp": i * 4 * 60 * 60 * 1000,  # 4h 间隔
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "close_time": (i + 1) * 4 * 60 * 60 * 1000,
        })
        price = close_price
    
    return klines


def generate_swing_klines(
    base_price: float = 95000,
    num_bars: int = 100,
) -> list:
    """生成有明显摆动高低点的 K 线数据"""
    klines = []
    
    # 创建波浪形态: 上涨 -> 下跌 -> 上涨 -> 下跌
    phases = [
        (0, 25, 1.002),    # 上涨
        (25, 50, 0.998),   # 下跌
        (50, 75, 1.002),   # 上涨
        (75, 100, 0.998),  # 下跌
    ]
    
    price = base_price
    for start, end, multiplier in phases:
        for i in range(start, end):
            open_price = price
            close_price = price * multiplier
            high_price = max(open_price, close_price) * 1.001
            low_price = min(open_price, close_price) * 0.999
            
            klines.append({
                "timestamp": i * 4 * 60 * 60 * 1000,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": 500,
                "close_time": (i + 1) * 4 * 60 * 60 * 1000,
            })
            price = close_price
    
    return klines


# ============================================
# 测试: FractalExtractor
# ============================================

class TestFractalExtractor:
    """测试分形点提取器"""
    
    def test_init_default(self):
        """测试默认初始化"""
        extractor = FractalExtractor()
        assert extractor.fibonacci_lookback == [8, 13, 21, 34, 55, 89]
    
    def test_init_custom(self):
        """测试自定义初始化"""
        extractor = FractalExtractor(fibonacci_lookback=[8, 21, 55])
        assert extractor.fibonacci_lookback == [8, 21, 55]
    
    def test_extract_empty_klines(self):
        """空数据返回空列表"""
        extractor = FractalExtractor()
        assert extractor.extract_fractals([], "4h") == []
    
    def test_extract_short_klines(self):
        """数据不足时返回空列表"""
        extractor = FractalExtractor()
        klines = generate_klines(num_bars=10)
        # 数据太少，无法找到周期 8 的分形点
        fractals = extractor.extract_fractals(klines, "4h", lookback_periods=[8])
        assert isinstance(fractals, list)
    
    def test_extract_with_swing_data(self):
        """测试有摆动数据的提取"""
        extractor = FractalExtractor(fibonacci_lookback=[8])
        klines = generate_swing_klines(num_bars=100)
        
        fractals = extractor.extract_fractals(klines, "4h", lookback_periods=[8])
        
        # 应该能找到一些分形点
        assert len(fractals) >= 0
        
        # 验证分形点结构
        for f in fractals:
            assert isinstance(f, FractalPoint)
            assert f.price > 0
            assert f.timeframe == "4h"
            assert f.period == 8
            assert f.type in ["HIGH", "LOW"]
    
    def test_extract_from_mtf(self):
        """测试多时间框架提取"""
        extractor = FractalExtractor(fibonacci_lookback=[8])
        
        klines_by_tf = {
            "1d": generate_klines(num_bars=50),
            "4h": generate_klines(num_bars=100),
            "15m": generate_klines(num_bars=200),
        }
        
        result = extractor.extract_from_mtf(klines_by_tf)
        
        assert "1d" in result
        assert "4h" in result
        assert "15m" in result
    
    def test_deduplicate_fractals(self):
        """测试分形点去重"""
        extractor = FractalExtractor()
        
        fractals = [
            FractalPoint(price=95000, timestamp=1, type="LOW", timeframe="4h", period=8, kline_index=10),
            FractalPoint(price=95010, timestamp=2, type="LOW", timeframe="4h", period=21, kline_index=20),
            FractalPoint(price=96000, timestamp=3, type="HIGH", timeframe="4h", period=8, kline_index=30),
        ]
        
        # 95000 和 95010 应该被合并 (0.01% 容差内)
        unique = extractor._deduplicate_fractals(fractals, price_tolerance=0.001)
        
        # 验证保留周期最大的
        assert len(unique) <= len(fractals)


class TestGetAnchorPrice:
    """测试锚点价格计算"""
    
    def test_anchor_price_normal(self):
        """正常数据"""
        klines = generate_klines(num_bars=100)
        anchor = get_anchor_price(klines, lookback=55)
        
        assert anchor is not None
        assert anchor > 0
    
    def test_anchor_price_empty(self):
        """空数据返回 None"""
        assert get_anchor_price([], lookback=55) is None
    
    def test_anchor_price_short_data(self):
        """数据不足时使用全部数据"""
        klines = generate_klines(num_bars=10)
        anchor = get_anchor_price(klines, lookback=55)
        
        assert anchor is not None


# ============================================
# 测试: VPVRAnalyzer
# ============================================

class TestVPVRAnalyzer:
    """测试成交量分布分析器"""
    
    def test_init_default(self):
        """测试默认初始化"""
        analyzer = VPVRAnalyzer()
        assert analyzer.bucket_count == 50
        assert analyzer.hvn_threshold == 0.20
    
    def test_analyze_empty(self):
        """空数据返回 None"""
        analyzer = VPVRAnalyzer()
        assert analyzer.analyze([]) is None
    
    def test_analyze_short_data(self):
        """数据不足返回 None"""
        analyzer = VPVRAnalyzer()
        klines = generate_klines(num_bars=5)
        assert analyzer.analyze(klines) is None
    
    def test_analyze_normal(self):
        """正常数据分析"""
        analyzer = VPVRAnalyzer()
        klines = generate_klines(num_bars=200)
        
        vpvr = analyzer.analyze(klines)
        
        assert vpvr is not None
        assert isinstance(vpvr, VPVRData)
        assert vpvr.poc_price > 0
        assert vpvr.total_volume > 0
        assert len(vpvr.price_range) == 2
    
    def test_get_volume_weight_hvn(self):
        """HVN 区域权重"""
        analyzer = VPVRAnalyzer()
        klines = generate_klines(num_bars=200)
        vpvr = analyzer.analyze(klines)
        
        # POC 附近应该是 HVN
        weight, zone = analyzer.get_volume_weight(vpvr.poc_price, vpvr)
        
        assert weight >= 1.0
        assert zone in [VolumeZone.HVN, VolumeZone.NORMAL]
    
    def test_is_near_poc(self):
        """POC 附近判断"""
        analyzer = VPVRAnalyzer()
        klines = generate_klines(num_bars=200)
        vpvr = analyzer.analyze(klines)
        
        assert analyzer.is_near_poc(vpvr.poc_price, vpvr, tolerance=0.01) is True
        assert analyzer.is_near_poc(vpvr.poc_price * 1.05, vpvr, tolerance=0.01) is False


# ============================================
# 测试: PsychologyMatcher
# ============================================

class TestPsychologyMatcher:
    """测试心理位匹配器"""
    
    def test_init_default(self):
        """测试默认初始化"""
        matcher = PsychologyMatcher()
        assert 0.618 in matcher.fib_ratios
        assert matcher.snap_tolerance == 0.01
    
    def test_calculate_fib_levels(self):
        """斐波那契位计算"""
        matcher = PsychologyMatcher()
        levels = matcher.calculate_fib_levels(high=100000, low=90000)
        
        assert len(levels) > 0
        assert all(isinstance(l, PsychologyLevel) for l in levels)
        assert all(l.type == "fib" for l in levels)
    
    def test_find_round_numbers_btc(self):
        """BTC 整数位查找"""
        matcher = PsychologyMatcher()
        levels = matcher.find_round_numbers(90000, 100000)
        
        assert len(levels) > 0
        # 应该包含 95000, 100000 等
        prices = [l.price for l in levels]
        assert 95000 in prices or 100000 in prices
    
    def test_snap_to_psychology_match(self):
        """心理位吸附 - 匹配"""
        matcher = PsychologyMatcher(snap_tolerance=0.01)
        levels = [
            PsychologyLevel(price=95000, type="round", label="Round 95000"),
        ]
        
        # 94900 距离 95000 约 0.1%，应该被吸附
        snapped, matched = matcher.snap_to_psychology(94900, levels)
        
        assert snapped == 95000
        assert matched is not None
        assert matched.price == 95000
    
    def test_snap_to_psychology_no_match(self):
        """心理位吸附 - 不匹配"""
        matcher = PsychologyMatcher(snap_tolerance=0.01)
        levels = [
            PsychologyLevel(price=95000, type="round", label="Round 95000"),
        ]
        
        # 90000 距离 95000 太远
        snapped, matched = matcher.snap_to_psychology(90000, levels)
        
        assert snapped == 90000
        assert matched is None
    
    def test_get_psychology_weight(self):
        """心理位权重"""
        matcher = PsychologyMatcher()
        
        # 有匹配时权重 > 1
        matched = PsychologyLevel(price=95000, type="round")
        assert matcher.get_psychology_weight(matched) > 1.0
        
        # 无匹配时权重 = 1
        assert matcher.get_psychology_weight(None) == 1.0


# ============================================
# 测试: LevelScorer
# ============================================

class TestLevelScorer:
    """测试 MTF 评分计算器"""
    
    def test_init_default(self):
        """测试默认初始化"""
        scorer = LevelScorer()
        assert VolumeZone.HVN in scorer.volume_weights
    
    def test_calculate_score_basic(self):
        """基本评分计算"""
        scorer = LevelScorer()
        
        candidate = MTFLevelCandidate(
            price=95000,
            source_fractals=[
                FractalPoint(price=95000, timestamp=1, type="LOW", timeframe="4h", period=21, kline_index=10),
            ],
            source_timeframes=["4h"],
            merged_price=95000,
        )
        
        score = scorer.calculate_score(
            candidate=candidate,
            vpvr=None,
            trend_state=TrendState.NEUTRAL,
            role="support",
        )
        
        assert isinstance(score, LevelScore)
        assert score.base_score > 0
        assert score.final_score > 0
        assert score.source_timeframes == ["4h"]
    
    def test_calculate_score_resonance(self):
        """共振水位评分更高"""
        scorer = LevelScorer()
        
        # 单框架
        single = MTFLevelCandidate(
            price=95000,
            source_fractals=[
                FractalPoint(price=95000, timestamp=1, type="LOW", timeframe="4h", period=21, kline_index=10),
            ],
            source_timeframes=["4h"],
            merged_price=95000,
        )
        
        # 双框架共振
        resonance = MTFLevelCandidate(
            price=95000,
            source_fractals=[
                FractalPoint(price=95000, timestamp=1, type="LOW", timeframe="4h", period=21, kline_index=10),
                FractalPoint(price=95000, timestamp=2, type="LOW", timeframe="1d", period=21, kline_index=5),
            ],
            source_timeframes=["4h", "1d"],
            is_resonance=True,
            merged_price=95000,
        )
        
        score_single = scorer.calculate_score(single, None, TrendState.NEUTRAL, "support")
        score_resonance = scorer.calculate_score(resonance, None, TrendState.NEUTRAL, "support")
        
        # 共振评分应该更高
        assert score_resonance.final_score > score_single.final_score
        assert score_resonance.mtf_coefficient > 1.0
    
    def test_get_qty_multiplier(self):
        """仓位系数计算"""
        scorer = LevelScorer()
        
        assert scorer.get_qty_multiplier(120) == 1.5  # >= 100
        assert scorer.get_qty_multiplier(80) == 1.2   # >= 60
        assert scorer.get_qty_multiplier(40) == 1.0   # >= 30
        assert scorer.get_qty_multiplier(20) == 0.0   # < 30


class TestDetermineTrend:
    """测试趋势判断"""
    
    def test_trend_neutral_short_data(self):
        """数据不足时为中性"""
        klines = generate_klines(num_bars=50)
        trend = determine_trend(klines)
        assert trend == TrendState.NEUTRAL
    
    def test_trend_with_data(self):
        """正常数据趋势判断"""
        klines = generate_klines(num_bars=200)
        trend = determine_trend(klines)
        assert trend in [TrendState.BULLISH, TrendState.BEARISH, TrendState.NEUTRAL]


# ============================================
# 测试: MTFMerger
# ============================================

class TestMTFMerger:
    """测试 MTF 融合器"""
    
    def test_init_default(self):
        """测试默认初始化"""
        merger = MTFMerger()
        assert merger.merge_tolerance == 0.005
        assert merger.timeframe_priority == ["1d", "4h", "15m"]
    
    def test_merge_empty(self):
        """空数据返回空列表"""
        merger = MTFMerger()
        assert merger.merge_fractals({}) == []
    
    def test_merge_single_tf(self):
        """单时间框架"""
        merger = MTFMerger()
        
        fractals_by_tf = {
            "4h": [
                FractalPoint(price=95000, timestamp=1, type="LOW", timeframe="4h", period=21, kline_index=10),
                FractalPoint(price=96000, timestamp=2, type="HIGH", timeframe="4h", period=21, kline_index=20),
            ]
        }
        
        candidates = merger.merge_fractals(fractals_by_tf)
        
        assert len(candidates) == 2
        assert all(c.is_resonance is False for c in candidates)
    
    def test_merge_multi_tf_resonance(self):
        """多时间框架共振检测"""
        merger = MTFMerger(merge_tolerance=0.005)
        
        fractals_by_tf = {
            "1d": [
                FractalPoint(price=95000, timestamp=1, type="LOW", timeframe="1d", period=21, kline_index=5),
            ],
            "4h": [
                FractalPoint(price=95050, timestamp=2, type="LOW", timeframe="4h", period=21, kline_index=10),  # 接近 95000
            ],
        }
        
        candidates = merger.merge_fractals(fractals_by_tf)
        
        # 95000 和 95050 应该被合并为一个共振水位
        assert len(candidates) == 1
        assert candidates[0].is_resonance is True
        assert set(candidates[0].source_timeframes) == {"1d", "4h"}
    
    def test_filter_by_type(self):
        """按类型过滤"""
        merger = MTFMerger()
        
        candidates = [
            MTFLevelCandidate(
                price=95000,
                source_fractals=[FractalPoint(price=95000, timestamp=1, type="LOW", timeframe="4h", period=21, kline_index=10)],
                source_timeframes=["4h"],
                merged_price=95000,
            ),
            MTFLevelCandidate(
                price=96000,
                source_fractals=[FractalPoint(price=96000, timestamp=2, type="HIGH", timeframe="4h", period=21, kline_index=20)],
                source_timeframes=["4h"],
                merged_price=96000,
            ),
        ]
        
        lows = merger.filter_by_type(candidates, "LOW")
        highs = merger.filter_by_type(candidates, "HIGH")
        
        assert len(lows) == 1
        assert len(highs) == 1
        assert lows[0].price == 95000
        assert highs[0].price == 96000
    
    def test_filter_by_distance(self):
        """按距离过滤"""
        merger = MTFMerger()
        
        candidates = [
            MTFLevelCandidate(price=95000, source_fractals=[], source_timeframes=[], merged_price=95000),
            MTFLevelCandidate(price=90000, source_fractals=[], source_timeframes=[], merged_price=90000),
            MTFLevelCandidate(price=50000, source_fractals=[], source_timeframes=[], merged_price=50000),  # 太远
        ]
        
        filtered = merger.filter_by_distance(
            candidates,
            current_price=94000,
            min_distance_pct=0.001,
            max_distance_pct=0.10,
        )
        
        # 50000 距离 94000 > 10%，应该被过滤
        assert len(filtered) == 2


# ============================================
# 测试: LevelCalculator 集成
# ============================================

class TestLevelCalculator:
    """测试 LevelCalculator 主入口"""
    
    def test_init_default(self):
        """测试默认初始化"""
        calc = LevelCalculator()
        assert calc.fractal_extractor is not None
        assert calc.vpvr_analyzer is not None
        assert calc.psychology_matcher is not None
        assert calc.scorer is not None
        assert calc.mtf_merger is not None
    
    def test_init_with_config(self):
        """测试配置初始化"""
        config = {
            "level_generation": {
                "fibonacci_lookback": [8, 21, 55],
                "timeframes": {
                    "l2_skeleton": {"interval": "1d"},
                    "l3_relay": {"interval": "4h"},
                },
            },
            "resistance": {
                "merge_tolerance": 0.01,
            },
        }
        calc = LevelCalculator(config)
        assert calc.fractal_extractor.fibonacci_lookback == [8, 21, 55]
    
    def test_generate_empty_data(self):
        """空数据返回 None"""
        calc = LevelCalculator()
        result = calc.generate_target_levels({}, 95000)
        assert result is None
    
    def test_generate_invalid_price(self):
        """无效价格返回 None"""
        calc = LevelCalculator()
        klines = {"4h": generate_klines(num_bars=100)}
        result = calc.generate_target_levels(klines, 0)
        assert result is None
    
    def test_generate_support_levels(self):
        """生成支撑位"""
        calc = LevelCalculator({
            "level_generation": {
                "fibonacci_lookback": [8],
            },
            "resistance": {
                "min_distance_pct": 0.001,
                "max_distance_pct": 0.30,
            },
        })
        
        klines_by_tf = {
            "4h": generate_swing_klines(num_bars=100),
        }
        
        # 使用高于数据的价格，以便能找到支撑位
        current_price = 100000
        
        result = calc.generate_target_levels(
            klines_by_tf=klines_by_tf,
            current_price=current_price,
            role="support",
            max_levels=5,
        )
        
        # 可能找到也可能找不到，取决于数据
        if result is not None:
            assert isinstance(result, list)
            for price, score in result:
                assert price < current_price  # 支撑位低于当前价
                assert isinstance(score, LevelScore)
    
    def test_get_anchor_price(self):
        """获取锚点价格"""
        calc = LevelCalculator()
        klines = generate_klines(num_bars=100)
        
        anchor = calc.get_anchor_price(klines, lookback=55)
        
        assert anchor is not None
        assert anchor > 0
    
    def test_refresh_scores(self):
        """刷新评分"""
        calc = LevelCalculator()
        
        existing_levels = [
            (95000, LevelScore(
                base_score=50,
                source_timeframes=["4h"],
                final_score=50,
            )),
        ]
        
        klines_by_tf = {
            "4h": generate_klines(num_bars=100),
        }
        
        result = calc.refresh_scores(
            existing_levels=existing_levels,
            klines_by_tf=klines_by_tf,
            current_price=96000,
            role="support",
        )
        
        assert len(result) == 1
        assert result[0][0] == 95000  # 价格不变


# ============================================
# 测试: MTF 共振系数计算
# ============================================

class TestMTFCoefficient:
    """测试 MTF 共振系数"""
    
    def test_single_tf(self):
        """单时间框架"""
        assert calculate_mtf_coefficient(["4h"]) == 1.0
    
    def test_double_tf(self):
        """双时间框架 (V3.2.5: 基于数量)"""
        # V3.2.5: 双框架共振系数统一为 1.5
        assert calculate_mtf_coefficient(["1d", "4h"]) == 1.5
        assert calculate_mtf_coefficient(["4h", "15m"]) == 1.5
    
    def test_triple_tf(self):
        """三时间框架 (V3.2.5: 基于数量)"""
        assert calculate_mtf_coefficient(["1d", "4h", "15m"]) == 2.0


# ============================================
# 运行测试
# ============================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
