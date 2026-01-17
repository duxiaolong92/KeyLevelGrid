"""
止盈策略模块

基于阻力位的止盈策略
"""

from dataclasses import dataclass
from typing import List

from key_level_grid.utils.logger import get_logger
from key_level_grid.analysis.resistance import PriceLevel


@dataclass
class TakeProfitLevel:
    """止盈级别"""
    price: float
    close_pct: float          # 平仓比例 (0-1)
    rr_multiple: float        # R倍数
    reason: str


@dataclass
class TakeProfitPlan:
    """止盈计划"""
    levels: List[TakeProfitLevel]
    total_position_usdt: float
    entry_price: float
    stop_loss: float
    
    def to_dict(self) -> dict:
        return {
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "levels": [
                {
                    "price": l.price,
                    "close_pct": l.close_pct,
                    "rr_multiple": l.rr_multiple,
                    "reason": l.reason
                }
                for l in self.levels
            ]
        }


class ResistanceBasedTakeProfit:
    """
    基于阻力位的止盈策略
    
    规则:
    1. 第一止盈必须达到 1.5R 以上
    2. 根据阻力位强度决定平仓比例
    3. 最后一个止盈留 10% 仓位跟踪
    """
    
    def __init__(self, min_rr_ratio: float = 1.5):
        self.min_rr_ratio = min_rr_ratio
        self.logger = get_logger(__name__)
    
    def create_take_profit_plan(
        self,
        entry_price: float,
        stop_loss: float,
        resistance_levels: List[PriceLevel],
        direction: str = "long",
        max_levels: int = 4
    ) -> TakeProfitPlan:
        """
        创建止盈计划
        
        Args:
            entry_price: 入场价
            stop_loss: 止损价
            resistance_levels: 阻力位列表
            direction: 交易方向
            max_levels: 最大止盈级别数
            
        Returns:
            TakeProfitPlan
        """
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance == 0:
            return TakeProfitPlan(
                levels=[],
                total_position_usdt=0,
                entry_price=entry_price,
                stop_loss=stop_loss
            )
        
        tp_levels: List[TakeProfitLevel] = []
        remaining_pct = 1.0
        
        for i, resistance in enumerate(resistance_levels[:max_levels]):
            # 计算 R 倍数
            if direction == "long":
                profit_distance = resistance.price - entry_price
            else:
                profit_distance = entry_price - resistance.price
            
            if profit_distance <= 0:
                continue
            
            rr_multiple = profit_distance / risk_distance
            
            # 第一止盈必须 >= min_rr_ratio
            if len(tp_levels) == 0 and rr_multiple < self.min_rr_ratio:
                continue
            
            # 根据阻力位强度决定平仓比例
            close_pct = self._calculate_close_pct(
                resistance.strength,
                remaining_pct,
                is_last=(i == min(len(resistance_levels), max_levels) - 1)
            )
            
            tp_levels.append(TakeProfitLevel(
                price=resistance.price,
                close_pct=close_pct,
                rr_multiple=rr_multiple,
                reason=resistance.description
            ))
            
            remaining_pct -= close_pct
            if remaining_pct <= 0.1:
                break
        
        # 如果没有合适的阻力位，使用默认 R 倍数
        if not tp_levels:
            tp_levels = self._create_default_plan(
                entry_price, stop_loss, risk_distance, direction
            )
        
        return TakeProfitPlan(
            levels=tp_levels,
            total_position_usdt=0,
            entry_price=entry_price,
            stop_loss=stop_loss
        )
    
    def _calculate_close_pct(
        self,
        strength: float,
        remaining_pct: float,
        is_last: bool
    ) -> float:
        """根据阻力位强度计算平仓比例"""
        if is_last:
            return max(0, remaining_pct - 0.1)
        
        if strength >= 80:
            base_pct = 0.40
        elif strength >= 60:
            base_pct = 0.30
        else:
            base_pct = 0.20
        
        return min(base_pct, remaining_pct - 0.1)
    
    def _create_default_plan(
        self,
        entry_price: float,
        stop_loss: float,
        risk_distance: float,
        direction: str
    ) -> List[TakeProfitLevel]:
        """创建默认止盈计划"""
        default_levels: List[TakeProfitLevel] = []
        
        rr_targets = [1.5, 2.5, 4.0]
        close_pcts = [0.40, 0.30, 0.20]
        
        for rr, pct in zip(rr_targets, close_pcts):
            if direction == "long":
                price = entry_price + risk_distance * rr
            else:
                price = entry_price - risk_distance * rr
            
            if price <= 0:
                continue
            
            default_levels.append(TakeProfitLevel(
                price=price,
                close_pct=pct,
                rr_multiple=rr,
                reason=f"默认 {rr}R"
            ))
        
        return default_levels
