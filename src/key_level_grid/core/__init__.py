"""
核心基础模块 (LEVEL_GENERATION.md v3.1.0)

包含数据模型、配置、类型定义和状态管理

V3.0 新增:
- scoring: 水位评分数据结构
- triggers: 触发器与日志数据结构
"""

from .models import (
    Kline,
    KlineFeedConfig,
    Timeframe,
    KeyLevelGridState,
    TimeframeTrend,
)
from .types import (
    LevelStatus,
    LevelLifecycleStatus,
    SignalType,
    SignalGrade,
    StopLossType,
    LevelType,
    FalseBreakoutType,
)
from .config import (
    GridConfig,
    PositionConfig,
    StopLossConfig,
    TakeProfitConfig,
    ResistanceConfig,
    SignalConfig,
    FilterConfig,
    IndicatorConfig,
    BreakoutFilterConfig,
)
from .state import (
    GridLevelState,
    GridOrder,
    GridState,
    ActiveFill,
    STATE_VERSION,
)
# V3.0 新增
from .scoring import (
    LevelScore,
    FractalPoint,
    VPVRData,
    MTFLevelCandidate,
    VolumeZone,
    TrendState,
    calculate_mtf_coefficient,
    calculate_base_score,
)
from .triggers import (
    RebuildTrigger,
    RebuildPhase,
    RebuildLog,
    ManualBoundary,
    PendingMigration,
    KlineSyncStatus,
    should_rebuild_grid,
    can_refresh_score,
    analyze_rebuild_logs,
)

__all__ = [
    # Models
    "Kline",
    "KlineFeedConfig",
    "Timeframe",
    "KeyLevelGridState",
    "TimeframeTrend",
    # Types
    "LevelStatus",
    "LevelLifecycleStatus",
    "SignalType",
    "SignalGrade",
    "StopLossType",
    "LevelType",
    "FalseBreakoutType",
    # Config
    "GridConfig",
    "PositionConfig",
    "StopLossConfig",
    "TakeProfitConfig",
    "ResistanceConfig",
    "SignalConfig",
    "FilterConfig",
    "IndicatorConfig",
    "BreakoutFilterConfig",
    # State
    "GridLevelState",
    "GridOrder",
    "GridState",
    "ActiveFill",
    "STATE_VERSION",
    # V3.0 Scoring
    "LevelScore",
    "FractalPoint",
    "VPVRData",
    "MTFLevelCandidate",
    "VolumeZone",
    "TrendState",
    "calculate_mtf_coefficient",
    "calculate_base_score",
    # V3.0 Triggers
    "RebuildTrigger",
    "RebuildPhase",
    "RebuildLog",
    "ManualBoundary",
    "PendingMigration",
    "KlineSyncStatus",
    "should_rebuild_grid",
    "can_refresh_score",
    "analyze_rebuild_logs",
]
