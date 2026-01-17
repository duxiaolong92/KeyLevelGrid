"""
止损策略模块

基于支撑位的网格止损:
- 跌破最低支撑位 (网格底线) 时止损
"""

from dataclasses import dataclass
from typing import Optional, List

from key_level_grid.utils.logger import get_logger
from key_level_grid.core.types import StopLossType
from key_level_grid.core.config import StopLossConfig


@dataclass
class StopLossOrder:
    """止损订单"""
    stop_type: StopLossType
    stop_price: float
    position_usdt: float
    trigger_reason: str
    is_active: bool = True
    
    def to_dict(self) -> dict:
        return {
            "stop_type": self.stop_type.value,
            "stop_price": self.stop_price,
            "position_usdt": self.position_usdt,
            "trigger_reason": self.trigger_reason,
            "is_active": self.is_active,
        }


class KeyLevelStopLossManager:
    """
    关键位网格止损管理器
    
    止损逻辑:
    1. 网格底线止损: 跌破最低支撑位时止损
    2. 固定止损: 入场价的固定百分比
    """
    
    def __init__(self, config: Optional[StopLossConfig] = None):
        self.config = config or StopLossConfig()
        self.logger = get_logger(__name__)
        
        self.current_stop: Optional[StopLossOrder] = None
        self.entry_price: float = 0.0
        self.direction: str = "long"
        
        self.grid_floor: float = 0.0
        
        self.highest_price: float = 0.0
        self.lowest_price: float = float('inf')
        
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
        """设置网格底线"""
        if not support_levels:
            self.logger.warning("无支撑位数据，无法设置网格底线")
            return 0.0
        
        min_support = min([s.get("price", 0) for s in support_levels if s.get("price", 0) > 0], default=0)
        
        if min_support > 0:
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
        """计算初始止损价"""
        self.entry_price = entry_price
        self.direction = direction
        self.highest_price = entry_price
        self.lowest_price = entry_price
        
        stop_price = 0.0
        reason = ""
        stop_type = StopLossType.GRID_FLOOR
        
        if direction == "long":
            if support_levels:
                grid_floor = self.set_grid_floor(support_levels)
                
                if grid_floor > 0:
                    distance_pct = (entry_price - grid_floor) / entry_price
                    
                    if distance_pct < self.config.min_distance_pct:
                        stop_price = entry_price * (1 - self.config.fixed_pct)
                        reason = f"固定止损 {self.config.fixed_pct:.1%}"
                        stop_type = StopLossType.FIXED
                    else:
                        stop_price = grid_floor
                        reason = f"网格底线止损"
                        stop_type = StopLossType.GRID_FLOOR
                else:
                    stop_price = entry_price * (1 - self.config.fixed_pct)
                    reason = f"固定止损 {self.config.fixed_pct:.1%}"
                    stop_type = StopLossType.FIXED
            else:
                stop_price = entry_price * (1 - self.config.fixed_pct)
                reason = f"固定止损 {self.config.fixed_pct:.1%}"
                stop_type = StopLossType.FIXED
        else:
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
        """更新止损价"""
        if self.current_stop is None:
            return None
        
        if self.entry_price == 0 or self.initial_stop_price == 0:
            return self.current_stop
        
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
        self.highest_price = max(self.highest_price, current_price)
        
        old_stop = self.current_stop.stop_price
        rr_ratio = (current_price - self.entry_price) / risk_distance
        
        new_stop = old_stop
        new_reason = self.current_stop.trigger_reason
        new_type = self.current_stop.stop_type
        
        # 保本止损
        if (self.config.breakeven_enabled and 
            rr_ratio >= self.config.breakeven_activation_rr and
            self.current_stop.stop_type not in [StopLossType.BREAKEVEN, StopLossType.TRAILING]):
            
            breakeven_price = self.entry_price * (1 + self.config.breakeven_offset)
            if breakeven_price > old_stop:
                new_stop = breakeven_price
                new_reason = f"保本止损 (RR={rr_ratio:.1f})"
                new_type = StopLossType.BREAKEVEN
        
        # 跟踪止损
        if (self.config.trailing_enabled and 
            rr_ratio >= self.config.trailing_activation_rr):
            
            trailing_stop = self.highest_price * (1 - self.config.trailing_pct)
            if trailing_stop > new_stop:
                new_stop = trailing_stop
                new_reason = f"跟踪止损 ({self.config.trailing_pct:.1%}回撤)"
                new_type = StopLossType.TRAILING
        
        if new_stop > old_stop:
            self.current_stop.stop_price = new_stop
            self.current_stop.trigger_reason = new_reason
            self.current_stop.stop_type = new_type
        
        return self.current_stop
    
    def _update_short_stop(
        self,
        current_price: float,
        risk_distance: float
    ) -> StopLossOrder:
        """更新做空止损"""
        self.lowest_price = min(self.lowest_price, current_price)
        
        old_stop = self.current_stop.stop_price
        rr_ratio = (self.entry_price - current_price) / risk_distance
        
        new_stop = old_stop
        new_reason = self.current_stop.trigger_reason
        new_type = self.current_stop.stop_type
        
        if (self.config.breakeven_enabled and 
            rr_ratio >= self.config.breakeven_activation_rr and
            self.current_stop.stop_type not in [StopLossType.BREAKEVEN, StopLossType.TRAILING]):
            
            breakeven_price = self.entry_price * (1 - self.config.breakeven_offset)
            if breakeven_price < old_stop:
                new_stop = breakeven_price
                new_reason = f"保本止损 (RR={rr_ratio:.1f})"
                new_type = StopLossType.BREAKEVEN
        
        if (self.config.trailing_enabled and 
            rr_ratio >= self.config.trailing_activation_rr):
            
            trailing_stop = self.lowest_price * (1 + self.config.trailing_pct)
            if trailing_stop < new_stop:
                new_stop = trailing_stop
                new_reason = f"跟踪止损 ({self.config.trailing_pct:.1%}回撤)"
                new_type = StopLossType.TRAILING
        
        if new_stop < old_stop:
            self.current_stop.stop_price = new_stop
            self.current_stop.trigger_reason = new_reason
            self.current_stop.stop_type = new_type
        
        return self.current_stop
    
    def check_stop_triggered(self, current_price: float) -> bool:
        """检查是否触发止损"""
        if self.current_stop is None or not self.current_stop.is_active:
            return False
        
        if self.direction == "long":
            triggered = current_price <= self.current_stop.stop_price
        else:
            triggered = current_price >= self.current_stop.stop_price
        
        if triggered:
            self.logger.warning(
                f"止损触发! {self.direction.upper()} @ {current_price:.4f}, "
                f"止损价={self.current_stop.stop_price:.4f}"
            )
        
        return triggered
    
    def check_grid_floor_breach(self, current_price: float) -> bool:
        """检查是否跌破网格底线"""
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
        """计算当前盈亏比"""
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
