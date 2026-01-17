"""
关键位网格趋势交易策略

Key Level Grid Strategy - 基于支撑阻力位的网格策略

目录结构:
- core/: 核心基础 (models, config, types, state)
- data/: 数据层 (feeds, store)
- analysis/: 分析 (indicator, resistance, mtf)
- signal/: 信号 (generator, filters)
- strategy/: 策略 (grid, stop_loss, take_profit)
- executor/: 执行器 (交易所接口)
- telegram/: 通知模块
- utils/: 工具函数
"""

# ============================================
# 核心模块 (core)
# ============================================

from key_level_grid.core import (
    # Models
    Timeframe,
    Kline,
    KlineFeedConfig,
    KeyLevelGridState,
    TimeframeTrend,
    # Types
    LevelStatus,
    LevelLifecycleStatus,
    SignalType,
    SignalGrade,
    StopLossType,
    LevelType,
    FalseBreakoutType,
    # Config
    GridConfig,
    PositionConfig,
    StopLossConfig,
    TakeProfitConfig,
    ResistanceConfig,
    SignalConfig,
    FilterConfig,
    IndicatorConfig,
    BreakoutFilterConfig,
    # State
    GridLevelState,
    GridOrder,
    GridState,
    ActiveFill,
    STATE_VERSION,
)

# ============================================
# 数据层 (data)
# ============================================

from key_level_grid.data import (
    BinanceKlineFeed,
    GateKlineFeed,
    PolygonKlineFeed,
    BacktestKlineFeed,
    TradeStore,
)

# ============================================
# 分析模块 (analysis)
# ============================================

from key_level_grid.analysis import (
    KeyLevelGridIndicator,
    ResistanceCalculator,
    PriceLevel,
    MultiTimeframeManager,
)

# ============================================
# 信号模块 (signal)
# ============================================

from key_level_grid.signal import (
    KeyLevelSignalGenerator,
    KeyLevelSignal,
    SignalFilterChain,
)
from key_level_grid.signal.filters import (
    BreakoutFilter,
    BreakoutResult,
    MACDTrendFilter,
    RSIFilter,
    ADXFilter,
    VolumeFilter,
)

# ============================================
# 策略模块 (strategy)
# ============================================

from key_level_grid.strategy import (
    LevelLifecycleManager,
    KeyLevelStopLossManager,
    StopLossOrder,
    ResistanceBasedTakeProfit,
    TakeProfitPlan,
    TakeProfitLevel,
)
from key_level_grid.strategy.grid import (
    InheritanceResult,
    OrderRequest,
    inherit_levels_by_index,
    generate_level_id,
)

# ============================================
# 向后兼容导入 (保持旧 API)
# ============================================

# 旧的模块保持可导入
try:
    from key_level_grid.position import GridPositionManager
    KeyLevelPositionManager = GridPositionManager
except ImportError:
    KeyLevelPositionManager = None

try:
    from key_level_grid.strategy_main import KeyLevelGridStrategy, KeyLevelGridConfig
except ImportError:
    KeyLevelGridStrategy = None
    KeyLevelGridConfig = None


__all__ = [
    # Core - Models
    "Timeframe",
    "Kline",
    "KlineFeedConfig",
    "KeyLevelGridState",
    "TimeframeTrend",
    # Core - Types
    "LevelStatus",
    "LevelLifecycleStatus",
    "SignalType",
    "SignalGrade",
    "StopLossType",
    "LevelType",
    "FalseBreakoutType",
    # Core - Config
    "GridConfig",
    "PositionConfig",
    "StopLossConfig",
    "TakeProfitConfig",
    "ResistanceConfig",
    "SignalConfig",
    "FilterConfig",
    "IndicatorConfig",
    "BreakoutFilterConfig",
    # Core - State
    "GridLevelState",
    "GridOrder",
    "GridState",
    "ActiveFill",
    "STATE_VERSION",
    # Data
    "BinanceKlineFeed",
    "GateKlineFeed",
    "PolygonKlineFeed",
    "BacktestKlineFeed",
    "TradeStore",
    # Analysis
    "KeyLevelGridIndicator",
    "ResistanceCalculator",
    "PriceLevel",
    "MultiTimeframeManager",
    # Signal
    "KeyLevelSignalGenerator",
    "KeyLevelSignal",
    "SignalFilterChain",
    "BreakoutFilter",
    "BreakoutResult",
    "MACDTrendFilter",
    "RSIFilter",
    "ADXFilter",
    "VolumeFilter",
    # Strategy
    "LevelLifecycleManager",
    "KeyLevelStopLossManager",
    "StopLossOrder",
    "ResistanceBasedTakeProfit",
    "TakeProfitPlan",
    "TakeProfitLevel",
    "InheritanceResult",
    "OrderRequest",
    "inherit_levels_by_index",
    "generate_level_id",
    # 向后兼容
    "KeyLevelPositionManager",
    "KeyLevelGridStrategy",
    "KeyLevelGridConfig",
]
