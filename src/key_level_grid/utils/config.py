"""
配置数据模型
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SafetyConfig:
    """安全配置（实盘交易保护）"""
    require_confirmation: bool = True  # 启动时需要二次确认
    max_daily_trades: int = 500        # 每日最大交易次数
    max_position_value: float = 100.0  # 单笔最大金额（USD）
    emergency_stop_loss: float = 500.0 # 紧急止损金额（USD）


