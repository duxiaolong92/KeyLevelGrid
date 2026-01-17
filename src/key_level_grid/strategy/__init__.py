"""
策略模块

包含网格策略、仓位管理、止损止盈、展示数据
"""

from .grid import LevelLifecycleManager
from .stop_loss import KeyLevelStopLossManager, StopLossOrder
from .take_profit import (
    ResistanceBasedTakeProfit,
    TakeProfitPlan,
    TakeProfitLevel,
)
from .display import DisplayDataGenerator
from .notifications import NotificationHelper
from .exchange_sync import ExchangeSyncManager
from .recon import ReconEventManager
from .position import LevelMappingManager
from .risk import RiskManager

# 向后兼容：从 strategy_main 导入主策略类
from key_level_grid.strategy_main import KeyLevelGridStrategy, KeyLevelGridConfig

__all__ = [
    # 主策略类
    "KeyLevelGridStrategy",
    "KeyLevelGridConfig",
    # 子模块
    "LevelLifecycleManager",
    "KeyLevelStopLossManager",
    "StopLossOrder",
    "ResistanceBasedTakeProfit",
    "TakeProfitPlan",
    "TakeProfitLevel",
    "DisplayDataGenerator",
    "NotificationHelper",
    "ExchangeSyncManager",
    "ReconEventManager",
    "LevelMappingManager",
    "RiskManager",
]
