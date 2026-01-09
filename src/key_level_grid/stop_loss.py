"""
止损策略模块 (简化版)

基于支撑位的网格止损:
- 跌破最低支撑位 (网格底线) 时止损
- 不再依赖 EMA 通道指标
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List

from key_level_grid.utils.logger import get_logger


class StopLossType(Enum):
    """止损类型"""
    FIXED = "fixed"           # 固定止损 (入场时设定)
    GRID_FLOOR = "grid_floor" # 网格底线止损 (最低支撑位)
    TRAILING = "trailing"     # 跟踪止损 (跟随最高价)
    BREAKEVEN = "breakeven"   # 保本止损 (盈利后移动到成本价)


@dataclass
class StopLossConfig:
    """止损配置"""
    # 初始止损
    initial_type: StopLossType = StopLossType.GRID_FLOOR
    fixed_pct: float = 0.10           # 固定止损: 入场价的 10%
    grid_buffer: float = 0.005        # 网格止损: 最低支撑下方 0.5%
    min_distance_pct: float = 0.02    # 最小止损距离 2%
    
    # 保本止损 (简化版可禁用)
    breakeven_enabled: bool = False
    breakeven_activation_rr: float = 0.5   # 0.5R 后激活保本
    breakeven_offset: float = 0.002        # 保本位 + 0.2% (覆盖手续费)
    
    # 跟踪止损 (简化版可禁用)
    trailing_enabled: bool = False
    trailing_activation_rr: float = 1.0    # 1R 后激活跟踪
    trailing_pct: float = 0.03             # 回撤 3% 触发


@dataclass
class StopLossOrder:
    """止损订单"""
    stop_type: StopLossType
    stop_price: float
    position_usdt: float
    trigger_reason: str
    is_active: bool = True
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "stop_type": self.stop_type.value,
            "stop_price": self.stop_price,
            "position_usdt": self.position_usdt,
            "trigger_reason": self.trigger_reason,
            "is_active": self.is_active,
        }


class KeyLevelStopLossManager:
    """
    关键位网格止损管理器 (简化版)
    
    止损逻辑:
    1. 网格底线止损: 跌破最低支撑位时止损
    2. 固定止损: 入场价的固定百分比
    """
    
    def __init__(self, config: Optional[StopLossConfig] = None):
        self.config = config or StopLossConfig()
        self.logger = get_logger(__name__)
        
        # 当前止损状态
        self.current_stop: Optional[StopLossOrder] = None
        self.entry_price: float = 0.0
        self.direction: str = "long"  # "long" | "short"
        
        # 网格底线
        self.grid_floor: float = 0.0
        
        # 跟踪最高/最低价
        self.highest_price: float = 0.0
        self.lowest_price: float = float('inf')
        
        # 初始止损价 (用于计算 R)
        self.initial_stop_price: float = 0.0
    
    def reset(self) -> None:
        """重置状态"""
        self.current_stop = None
        self.entry_price = 0.0
        self.grid_floor = 0.0
        self.highest_price = 0.0
        self.lowest_price = float('inf')
        self.initial_stop_price = 0.0
    
    def set_grid_floor(self, support_levels: List[dict]) -> float:
        """
        设置网格底线 (基于最低支撑位)
        
        Args:
            support_levels: 支撑位列表，每项包含 price 字段
            
        Returns:
            网格底线价格
        """
        if not support_levels:
            self.logger.warning("无支撑位数据，无法设置网格底线")
            return 0.0
        
        # 找到最低支撑位
        min_support = min([s.get("price", 0) for s in support_levels if s.get("price", 0) > 0], default=0)
        
        if min_support > 0:
            # 网格底线 = 最低支撑 - buffer
            self.grid_floor = min_support * (1 - self.config.grid_buffer)
            self.logger.info(f"设置网格底线: {self.grid_floor:.4f} (最低支撑={min_support:.4f})")
        
        return self.grid_floor
    
    def calculate_initial_stop(
        self,
        entry_price: float,
        direction: str,
        support_levels: List[dict] = None,
        position_usdt: float = 0.0
    ) -> StopLossOrder:
        """
        计算初始止损价
        
        做多止损位:
        1. 网格底线 (最低支撑位下方)
        2. 如果无支撑位数据，使用固定比例
        
        做空止损位:
        1. 使用固定比例止损
        
        Args:
            entry_price: 入场价
            direction: 方向 "long" | "short"
            support_levels: 支撑位列表
            position_usdt: 仓位价值 (USDT)
            
        Returns:
            StopLossOrder
        """
        self.entry_price = entry_price
        self.direction = direction
        self.highest_price = entry_price
        self.lowest_price = entry_price
        
        stop_price = 0.0
        reason = ""
        stop_type = self.config.initial_type
        
        if direction == "long":
            if self.config.initial_type == StopLossType.GRID_FLOOR and support_levels:
                # 设置网格底线
                grid_floor = self.set_grid_floor(support_levels)
                
                if grid_floor > 0:
                    distance_pct = (entry_price - grid_floor) / entry_price
                    
                    if distance_pct < self.config.min_distance_pct:
                        # 距离太近，使用固定比例
                        stop_price = entry_price * (1 - self.config.fixed_pct)
                        reason = f"固定止损 {self.config.fixed_pct:.1%} (网格距离 {distance_pct:.1%} 太近)"
                        stop_type = StopLossType.FIXED
                    else:
                        stop_price = grid_floor
                        reason = f"网格底线止损 (最低支撑下方 {self.config.grid_buffer:.1%})"
                        stop_type = StopLossType.GRID_FLOOR
                else:
                    # 无有效支撑位，使用固定止损
                    stop_price = entry_price * (1 - self.config.fixed_pct)
                    reason = f"固定止损 {self.config.fixed_pct:.1%} (无支撑位数据)"
                    stop_type = StopLossType.FIXED
            else:
                # 使用固定止损
                stop_price = entry_price * (1 - self.config.fixed_pct)
                reason = f"固定止损 {self.config.fixed_pct:.1%}"
                stop_type = StopLossType.FIXED
        
        else:  # short
            # 做空使用固定止损
            stop_price = entry_price * (1 + self.config.fixed_pct)
            reason = f"固定止损 {self.config.fixed_pct:.1%}"
            stop_type = StopLossType.FIXED
        
        self.initial_stop_price = stop_price
        
        self.current_stop = StopLossOrder(
            stop_type=stop_type,
            stop_price=stop_price,
            position_usdt=position_usdt,
            trigger_reason=reason
        )
        
        self.logger.info(
            f"设置初始止损: {direction.upper()} @ {entry_price:.4f}, "
            f"止损={stop_price:.4f}, 原因={reason}"
        )
        
        return self.current_stop
    
    def update_stop(
        self,
        current_price: float,
        support_levels: List[dict] = None
    ) -> Optional[StopLossOrder]:
        """
        更新止损价 (每根K线调用)
        
        简化版更新逻辑:
        1. 检查是否激活保本止损 (可选)
        2. 检查是否激活跟踪止损 (可选)
        3. 止损只能单向移动 (做多只能上移，做空只能下移)
        
        Args:
            current_price: 当前价格
            support_levels: 支撑位列表 (可选)
            
        Returns:
            更新后的止损订单
        """
        if self.current_stop is None:
            return None
        
        if self.entry_price == 0 or self.initial_stop_price == 0:
            return self.current_stop
        
        old_stop = self.current_stop.stop_price
        risk_distance = abs(self.entry_price - self.initial_stop_price)
        
        if risk_distance == 0:
            return self.current_stop
        
        if self.direction == "long":
            return self._update_long_stop(current_price, risk_distance)
        else:
            return self._update_short_stop(current_price, risk_distance)
    
    def _update_long_stop(
        self,
        current_price: float,
        risk_distance: float
    ) -> StopLossOrder:
        """更新做多止损"""
        # 更新最高价
        self.highest_price = max(self.highest_price, current_price)
        
        old_stop = self.current_stop.stop_price
        rr_ratio = (current_price - self.entry_price) / risk_distance
        
        new_stop = old_stop
        new_reason = self.current_stop.trigger_reason
        new_type = self.current_stop.stop_type
        
        # 1. 保本止损检查 (可选)
        if (self.config.breakeven_enabled and 
            rr_ratio >= self.config.breakeven_activation_rr and
            self.current_stop.stop_type not in [StopLossType.BREAKEVEN, StopLossType.TRAILING]):
            
            breakeven_price = self.entry_price * (1 + self.config.breakeven_offset)
            if breakeven_price > old_stop:
                new_stop = breakeven_price
                new_reason = f"保本止损 (RR={rr_ratio:.1f})"
                new_type = StopLossType.BREAKEVEN
                self.logger.info(f"激活保本止损: {old_stop:.4f} -> {new_stop:.4f}")
        
        # 2. 跟踪止损检查 (可选)
        if (self.config.trailing_enabled and 
            rr_ratio >= self.config.trailing_activation_rr):
            
            trailing_stop = self.highest_price * (1 - self.config.trailing_pct)
            if trailing_stop > new_stop:
                new_stop = trailing_stop
                new_reason = f"跟踪止损 ({self.config.trailing_pct:.1%}回撤, 最高={self.highest_price:.4f})"
                new_type = StopLossType.TRAILING
        
        # 更新止损 (只能上移)
        if new_stop > old_stop:
            self.current_stop.stop_price = new_stop
            self.current_stop.trigger_reason = new_reason
            self.current_stop.stop_type = new_type
            self.logger.debug(
                f"更新止损: {old_stop:.4f} -> {new_stop:.4f}, {new_reason}"
            )
        
        return self.current_stop
    
    def _update_short_stop(
        self,
        current_price: float,
        risk_distance: float
    ) -> StopLossOrder:
        """更新做空止损"""
        # 更新最低价
        self.lowest_price = min(self.lowest_price, current_price)
        
        old_stop = self.current_stop.stop_price
        rr_ratio = (self.entry_price - current_price) / risk_distance
        
        new_stop = old_stop
        new_reason = self.current_stop.trigger_reason
        new_type = self.current_stop.stop_type
        
        # 1. 保本止损检查 (可选)
        if (self.config.breakeven_enabled and 
            rr_ratio >= self.config.breakeven_activation_rr and
            self.current_stop.stop_type not in [StopLossType.BREAKEVEN, StopLossType.TRAILING]):
            
            breakeven_price = self.entry_price * (1 - self.config.breakeven_offset)
            if breakeven_price < old_stop:
                new_stop = breakeven_price
                new_reason = f"保本止损 (RR={rr_ratio:.1f})"
                new_type = StopLossType.BREAKEVEN
                self.logger.info(f"激活保本止损: {old_stop:.4f} -> {new_stop:.4f}")
        
        # 2. 跟踪止损检查 (可选)
        if (self.config.trailing_enabled and 
            rr_ratio >= self.config.trailing_activation_rr):
            
            trailing_stop = self.lowest_price * (1 + self.config.trailing_pct)
            if trailing_stop < new_stop:
                new_stop = trailing_stop
                new_reason = f"跟踪止损 ({self.config.trailing_pct:.1%}回撤, 最低={self.lowest_price:.4f})"
                new_type = StopLossType.TRAILING
        
        # 更新止损 (只能下移)
        if new_stop < old_stop:
            self.current_stop.stop_price = new_stop
            self.current_stop.trigger_reason = new_reason
            self.current_stop.stop_type = new_type
            self.logger.debug(
                f"更新止损: {old_stop:.4f} -> {new_stop:.4f}, {new_reason}"
            )
        
        return self.current_stop
    
    def check_stop_triggered(self, current_price: float) -> bool:
        """
        检查是否触发止损
        
        Args:
            current_price: 当前价格
            
        Returns:
            True 如果触发止损
        """
        if self.current_stop is None or not self.current_stop.is_active:
            return False
        
        if self.direction == "long":
            triggered = current_price <= self.current_stop.stop_price
        else:
            triggered = current_price >= self.current_stop.stop_price
        
        if triggered:
            self.logger.warning(
                f"止损触发! {self.direction.upper()} @ {current_price:.4f}, "
                f"止损价={self.current_stop.stop_price:.4f}, "
                f"类型={self.current_stop.stop_type.value}"
            )
        
        return triggered
    
    def check_grid_floor_breach(self, current_price: float) -> bool:
        """
        检查是否跌破网格底线
        
        Args:
            current_price: 当前价格
            
        Returns:
            True 如果跌破网格底线
        """
        if self.grid_floor <= 0:
            return False
        
        if current_price <= self.grid_floor:
            self.logger.warning(
                f"跌破网格底线! 当前价={current_price:.4f}, "
                f"网格底线={self.grid_floor:.4f}"
            )
            return True
        
        return False
    
    def get_risk_reward(self, current_price: float) -> float:
        """
        计算当前盈亏比
        
        Returns:
            R 倍数 (正数为盈利，负数为亏损)
        """
        if self.entry_price == 0 or self.initial_stop_price == 0:
            return 0.0
        
        risk_distance = abs(self.entry_price - self.initial_stop_price)
        if risk_distance == 0:
            return 0.0
        
        if self.direction == "long":
            return (current_price - self.entry_price) / risk_distance
        else:
            return (self.entry_price - current_price) / risk_distance
    
    def get_stats(self) -> dict:
        """获取止损统计信息"""
        return {
            "entry_price": self.entry_price,
            "direction": self.direction,
            "initial_stop": self.initial_stop_price,
            "current_stop": self.current_stop.stop_price if self.current_stop else None,
            "stop_type": self.current_stop.stop_type.value if self.current_stop else None,
            "grid_floor": self.grid_floor,
            "highest_price": self.highest_price,
            "lowest_price": self.lowest_price if self.lowest_price != float('inf') else None,
        }
