"""
ATR 空间硬约束审计器测试 (LEVEL_GENERATION.md v3.2.5)

测试覆盖:
1. ATR 计算
2. 密度审计 (裁剪过密)
3. 稀疏审计 (补全过稀)
4. 补全优先级 (战术种子 → VPVR → 斐波那契)
5. 边界情况
"""

import pytest
from typing import List, Dict

from key_level_grid.core.triggers import ATRConfig, FilledLevel
from key_level_grid.core.scoring import (
    FractalPoint,
    VPVRData,
    MTFLevelCandidate,
    VolumeZone,
)
from key_level_grid.analysis.atr_gap_auditor import (
    ATRGapAuditor,
    AuditResult,
    create_auditor_from_config,
)


# ============================================
# 测试数据生成
# ============================================

def create_klines(prices: List[float], volatility: float = 0.02) -> List[Dict]:
    """生成测试 K 线数据"""
    klines = []
    for i, close in enumerate(prices):
        high = close * (1 + volatility)
        low = close * (1 - volatility)
        klines.append({
            "open": close * (1 - volatility / 2),
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000,
            "timestamp": 1000000 + i * 3600000,
        })
    return klines


def create_candidates(prices: List[float]) -> List[MTFLevelCandidate]:
    """生成测试候选水位"""
    candidates = []
    for price in sorted(prices, reverse=True):
        candidates.append(MTFLevelCandidate(
            price=price,
            source_fractals=[FractalPoint(
                price=price,
                timestamp=1000000,
                type="LOW",
                timeframe="4h",
                period=21,
                kline_index=10,
            )],
            source_timeframes=["4h"],
            merged_price=price,
        ))
    return candidates


def create_vpvr(
    poc_price: float,
    hvn_zones: List[tuple] = None,
    lvn_zones: List[tuple] = None,
) -> VPVRData:
    """生成测试 VPVR 数据"""
    return VPVRData(
        poc_price=poc_price,
        hvn_zones=hvn_zones or [],
        lvn_zones=lvn_zones or [],
        total_volume=1000000,
        price_range=(90000, 100000),
    )


# ============================================
# ATR 计算测试
# ============================================

class TestATRCalculation:
    """ATR 计算测试"""
    
    def test_basic_atr_calculation(self):
        """测试基本 ATR 计算"""
        # 创建稳定波动的 K 线
        prices = [100 + i * 0.5 for i in range(20)]
        klines = create_klines(prices, volatility=0.02)
        
        auditor = ATRGapAuditor()
        atr = auditor.calculate_atr(klines, period=14)
        
        assert atr > 0
        # 2% 波动率，ATR 应该约为 price * 0.04 (high - low)
        assert 3 < atr < 6
    
    def test_atr_with_insufficient_data(self):
        """测试数据不足时的 ATR 计算"""
        klines = create_klines([100, 101, 102])
        
        auditor = ATRGapAuditor()
        atr = auditor.calculate_atr(klines, period=14)
        
        assert atr == 0.0
    
    def test_atr_caching(self):
        """测试 ATR 缓存"""
        prices = [100 + i * 0.5 for i in range(20)]
        klines = create_klines(prices)
        
        auditor = ATRGapAuditor()
        atr1 = auditor.calculate_atr(klines)
        atr2 = auditor._cached_atr
        
        assert atr1 == atr2


# ============================================
# 密度审计测试
# ============================================

class TestDensityAudit:
    """密度审计测试"""
    
    def test_no_trim_when_gap_sufficient(self):
        """测试间距足够时不裁剪"""
        # 间距 1000，ATR 约 200，0.5*ATR = 100，间距足够
        candidates = create_candidates([95000, 94000, 93000])
        
        config = ATRConfig(
            enabled=True,
            gap_min_atr_ratio=0.5,
            gap_max_atr_ratio=3.0,
        )
        auditor = ATRGapAuditor(config=config)
        auditor._cached_atr = 200  # 模拟 ATR
        
        result, trimmed = auditor._audit_density(candidates, atr=200)
        
        assert len(result) == 3
        assert len(trimmed) == 0
    
    def test_trim_when_too_dense(self):
        """测试过密时裁剪"""
        # 间距 50，ATR 200，0.5*ATR = 100，间距不足
        candidates = create_candidates([95000, 94950, 94900])
        
        config = ATRConfig(
            enabled=True,
            gap_min_atr_ratio=0.5,
        )
        auditor = ATRGapAuditor(config=config)
        
        result, trimmed = auditor._audit_density(candidates, atr=200)
        
        # 应该裁剪一些
        assert len(result) < 3
        assert len(trimmed) > 0
    
    def test_trim_prefers_hvn_over_lvn(self):
        """测试裁剪时优先保留 HVN"""
        # 两个相近水位，一个在 HVN，一个在 LVN
        candidates = create_candidates([95000, 94950])
        
        vpvr = create_vpvr(
            poc_price=95000,
            hvn_zones=[(94900, 95100)],
            lvn_zones=[(94800, 94960)],
        )
        
        config = ATRConfig(
            enabled=True,
            gap_min_atr_ratio=0.5,
        )
        auditor = ATRGapAuditor(config=config, vpvr_data=vpvr)
        
        result, trimmed = auditor._audit_density(candidates, atr=200)
        
        # 应该保留 HVN 中的 95000
        assert len(result) == 1
        assert result[0].merged_price == 95000


# ============================================
# 稀疏审计测试
# ============================================

class TestSparseAudit:
    """稀疏审计测试"""
    
    def test_no_fill_when_gap_acceptable(self):
        """测试间距可接受时不补全"""
        # 间距 500，ATR 200，3*ATR = 600，间距可接受
        candidates = create_candidates([95000, 94500])
        
        config = ATRConfig(
            enabled=True,
            gap_max_atr_ratio=3.0,
        )
        auditor = ATRGapAuditor(config=config)
        
        result, filled = auditor._audit_sparse(candidates, atr=200)
        
        assert len(result) == 2
        assert len(filled) == 0
    
    def test_fill_when_too_sparse(self):
        """测试过稀时补全"""
        # 间距 2000，ATR 200，3*ATR = 600，间距过大
        candidates = create_candidates([95000, 93000])
        
        config = ATRConfig(
            enabled=True,
            gap_max_atr_ratio=3.0,
            fibonacci_enabled=True,
            fibonacci_fill_ratio=0.618,
            fibonacci_fill_score=35,
        )
        auditor = ATRGapAuditor(config=config)
        
        result, filled = auditor._audit_sparse(candidates, atr=200)
        
        # 应该有补全
        assert len(result) > 2
        assert len(filled) > 0
    
    def test_fill_with_tactical_seed(self):
        """测试使用战术种子补全"""
        candidates = create_candidates([95000, 93000])
        
        # 在空隙中添加战术分形点
        tactical_pool = [
            FractalPoint(
                price=94000,
                timestamp=1000000,
                type="LOW",
                timeframe="15m",
                period=55,
                kline_index=10,
                layer="l4",
            )
        ]
        
        config = ATRConfig(
            enabled=True,
            gap_max_atr_ratio=3.0,
            fill_priority=["tactical", "vpvr", "fibonacci"],
        )
        auditor = ATRGapAuditor(config=config, tactical_pool=tactical_pool)
        
        result, filled = auditor._audit_sparse(candidates, atr=200)
        
        # 应该使用战术种子补全
        tactical_fills = [f for f in filled if f.fill_type == "tactical"]
        assert len(tactical_fills) > 0
    
    def test_fill_with_vpvr(self):
        """测试使用 VPVR 补全"""
        candidates = create_candidates([95000, 93000])
        
        vpvr = create_vpvr(
            poc_price=94000,
            hvn_zones=[(93800, 94200)],
        )
        
        config = ATRConfig(
            enabled=True,
            gap_max_atr_ratio=3.0,
            fill_priority=["vpvr", "fibonacci"],
        )
        auditor = ATRGapAuditor(config=config, vpvr_data=vpvr)
        
        result, filled = auditor._audit_sparse(candidates, atr=200)
        
        # 应该使用 VPVR 补全
        vpvr_fills = [f for f in filled if f.fill_type == "vpvr"]
        assert len(vpvr_fills) > 0
    
    def test_fill_with_fibonacci_fallback(self):
        """测试斐波那契兜底补全"""
        candidates = create_candidates([95000, 93000])
        
        config = ATRConfig(
            enabled=True,
            gap_max_atr_ratio=3.0,
            fill_priority=["fibonacci"],
            fibonacci_enabled=True,
            fibonacci_fill_ratio=0.618,
            fibonacci_fill_score=35,
        )
        auditor = ATRGapAuditor(config=config)
        
        result, filled = auditor._audit_sparse(candidates, atr=200)
        
        # 应该使用斐波那契补全
        fib_fills = [f for f in filled if f.fill_type == "fibonacci"]
        assert len(fib_fills) > 0
        
        # 检查第一个补全位置 (递归补全可能产生多个)
        first_fill = fib_fills[0]
        assert first_fill.score == 35
        # 第一个 0.618 位置应该在 95000 - (95000-93000)*0.618 ≈ 93764
        expected_price = 95000 - (95000 - 93000) * 0.618
        assert abs(first_fill.price - expected_price) < 1


# ============================================
# 完整审计流程测试
# ============================================

class TestFullAudit:
    """完整审计流程测试"""
    
    def test_audit_disabled(self):
        """测试禁用审计"""
        candidates = create_candidates([95000, 94000, 93000])
        
        config = ATRConfig(enabled=False)
        auditor = ATRGapAuditor(config=config)
        
        result, audit_result = auditor.audit(candidates, atr=200)
        
        assert len(result) == 3
        assert audit_result.trimmed_count == 0
        assert audit_result.filled_count == 0
    
    def test_audit_with_klines(self):
        """测试使用 K 线计算 ATR 的审计"""
        candidates = create_candidates([95000, 94000, 93000])
        klines = create_klines([95000 - i * 100 for i in range(20)])
        
        config = ATRConfig(
            enabled=True,
            gap_min_atr_ratio=0.5,
            gap_max_atr_ratio=3.0,
        )
        auditor = ATRGapAuditor(config=config)
        
        result, audit_result = auditor.audit(candidates, klines=klines)
        
        assert audit_result.atr_value > 0
        assert audit_result.original_count == 3
    
    def test_audit_result_structure(self):
        """测试审计结果结构"""
        candidates = create_candidates([95000, 94950, 93000])
        
        config = ATRConfig(
            enabled=True,
            gap_min_atr_ratio=0.5,
            gap_max_atr_ratio=3.0,
            fibonacci_enabled=True,
        )
        auditor = ATRGapAuditor(config=config)
        
        result, audit_result = auditor.audit(candidates, atr=200)
        
        # 验证结果结构
        assert isinstance(audit_result, AuditResult)
        assert audit_result.original_count == 3
        assert audit_result.final_count == len(result)
        assert audit_result.trimmed_count == len(audit_result.trimmed_prices)
        assert audit_result.filled_count == len(audit_result.filled_levels)
        
        # 验证可序列化
        result_dict = audit_result.to_dict()
        assert "original_count" in result_dict
        assert "filled_levels" in result_dict


# ============================================
# 配置测试
# ============================================

class TestATRConfig:
    """ATR 配置测试"""
    
    def test_config_from_dict(self):
        """测试从字典加载配置"""
        config_dict = {
            "enabled": True,
            "atr_period": 20,
            "gap_min_atr_ratio": 0.6,
            "gap_max_atr_ratio": 2.5,
            "fibonacci_fill_ratio": 0.5,
            "fibonacci_fill_score": 40,
        }
        
        config = ATRConfig.from_dict(config_dict)
        
        assert config.enabled is True
        assert config.atr_period == 20
        assert config.gap_min_atr_ratio == 0.6
        assert config.gap_max_atr_ratio == 2.5
        assert config.fibonacci_fill_ratio == 0.5
        assert config.fibonacci_fill_score == 40
    
    def test_config_defaults(self):
        """测试默认配置"""
        config = ATRConfig()
        
        assert config.enabled is True
        assert config.atr_period == 14
        assert config.gap_min_atr_ratio == 0.5
        assert config.gap_max_atr_ratio == 3.0
        assert config.fibonacci_fill_ratio == 0.618
    
    def test_is_too_dense(self):
        """测试过密判断"""
        config = ATRConfig(gap_min_atr_ratio=0.5)
        
        assert config.is_too_dense(gap=50, atr=200) is True   # 50 < 100
        assert config.is_too_dense(gap=150, atr=200) is False  # 150 > 100
    
    def test_is_too_sparse(self):
        """测试过稀判断"""
        config = ATRConfig(gap_max_atr_ratio=3.0)
        
        assert config.is_too_sparse(gap=700, atr=200) is True   # 700 > 600
        assert config.is_too_sparse(gap=500, atr=200) is False  # 500 < 600
    
    def test_get_fibonacci_fill_price(self):
        """测试斐波那契填充价格计算"""
        config = ATRConfig(fibonacci_fill_ratio=0.618)
        
        price = config.get_fibonacci_fill_price(upper=100, lower=0)
        assert abs(price - 38.2) < 0.1  # 100 - 100*0.618 = 38.2


# ============================================
# 工厂函数测试
# ============================================

class TestFactory:
    """工厂函数测试"""
    
    def test_create_from_config(self):
        """测试从完整配置创建审计器"""
        config = {
            "level_generation": {
                "atr_constraint": {
                    "enabled": True,
                    "atr_period": 21,
                    "gap_min_atr_ratio": 0.4,
                }
            }
        }
        
        auditor = create_auditor_from_config(config)
        
        assert auditor.config.enabled is True
        assert auditor.config.atr_period == 21
        assert auditor.config.gap_min_atr_ratio == 0.4


# ============================================
# 边界情况测试
# ============================================

class TestEdgeCases:
    """边界情况测试"""
    
    def test_empty_candidates(self):
        """测试空候选列表"""
        auditor = ATRGapAuditor()
        
        result, audit_result = auditor.audit([], atr=200)
        
        assert len(result) == 0
        assert audit_result.original_count == 0
    
    def test_single_candidate(self):
        """测试单个候选"""
        candidates = create_candidates([95000])
        auditor = ATRGapAuditor()
        
        result, audit_result = auditor.audit(candidates, atr=200)
        
        assert len(result) == 1
        assert audit_result.trimmed_count == 0
        assert audit_result.filled_count == 0
    
    def test_zero_atr(self):
        """测试 ATR 为零"""
        candidates = create_candidates([95000, 94000])
        auditor = ATRGapAuditor()
        
        result, audit_result = auditor.audit(candidates, atr=0)
        
        # ATR 为零时不应该裁剪或补全
        assert len(result) == 2
    
    def test_fibonacci_disabled(self):
        """测试禁用斐波那契兜底"""
        candidates = create_candidates([95000, 93000])
        
        config = ATRConfig(
            enabled=True,
            gap_max_atr_ratio=3.0,
            fill_priority=["fibonacci"],
            fibonacci_enabled=False,
        )
        auditor = ATRGapAuditor(config=config)
        
        result, filled = auditor._audit_sparse(candidates, atr=200)
        
        # 禁用后不应该有斐波那契补全
        fib_fills = [f for f in filled if f.fill_type == "fibonacci"]
        assert len(fib_fills) == 0
