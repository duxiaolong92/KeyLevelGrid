"""
分析模块 (LEVEL_GENERATION.md v3.2.5)

包含技术指标计算、支撑阻力位识别和多周期分析

V3.0 新增:
- fractal: MTF 分形点提取
- vpvr: 成交量分布分析
- psychology: 心理位匹配
- scorer: MTF 评分计算
- mtf_merger: 多框架融合

V3.2.5 新增:
- atr_gap_auditor: ATR 空间硬约束审计器
"""

from .indicator import KeyLevelGridIndicator
from .resistance import ResistanceCalculator, PriceLevel
from .mtf import MultiTimeframeManager

# 从 core 模块重新导出常用类型
from key_level_grid.core.config import IndicatorConfig, ResistanceConfig
from key_level_grid.core.types import LevelType

# V3.0 新增模块
from .fractal import FractalExtractor, get_anchor_price, get_anchor_by_layer
from .vpvr import VPVRAnalyzer
from .psychology import PsychologyMatcher, PsychologyLevel
from .scorer import LevelScorer, determine_trend
from .mtf_merger import MTFMerger, select_top_levels

# V3.2.5 新增模块
from .atr_gap_auditor import ATRGapAuditor, AuditResult, create_auditor_from_config

__all__ = [
    # 原有模块
    "KeyLevelGridIndicator",
    "IndicatorConfig",
    "ResistanceCalculator",
    "ResistanceConfig",
    "PriceLevel",
    "LevelType",
    "MultiTimeframeManager",
    # V3.0 新增
    "FractalExtractor",
    "get_anchor_price",
    "get_anchor_by_layer",
    "VPVRAnalyzer",
    "PsychologyMatcher",
    "PsychologyLevel",
    "LevelScorer",
    "determine_trend",
    "MTFMerger",
    "select_top_levels",
    # V3.2.5 新增
    "ATRGapAuditor",
    "AuditResult",
    "create_auditor_from_config",
]
