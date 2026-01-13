"""
仓位管理模块 (V2.3 简化版)

基于支撑/阻力位的网格仓位管理
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from key_level_grid.utils.logger import get_logger
from key_level_grid.resistance import (
    PriceLevel,
)


# ============================================
# 配置数据类
# ============================================

@dataclass
class GridConfig:
    """网格配置"""
    # 区间设置
    range_mode: str = "auto"          # auto | manual
    manual_upper: float = 0.0         # 手动上边界
    manual_lower: float = 0.0         # 手动下边界
    
    # 网格数量
    count_mode: str = "by_levels"     # by_levels | fixed
    fixed_count: int = 5              # fixed 模式的网格数量
    max_grids: int = 10               # 最大网格数量
    
    # 网格底线
    floor_buffer: float = 0.005       # 最低支撑下方 0.5%
    
    # 网格重建 (价格大幅偏离锚点时自动重建)
    rebuild_enabled: bool = True      # 是否启用自动重建
    rebuild_threshold_pct: float = 0.02  # 价格偏离阈值 2%
    rebuild_cooldown_sec: int = 900   # 重建冷却时间 15分钟
    rebuild_cooldown_on_fill_sec: int = 600  # 因成交触发重建的冷却时间（秒）


@dataclass
class PositionConfig:
    """仓位配置 (V2.3 简化版)"""
    total_capital: float = 5000.0     # 账户总金额 (USDT)
    max_leverage: float = 3.0         # 最大杠杆
    max_capital_usage: float = 0.8    # 使用 80% 资金
    
    # 仓位分配
    allocation_mode: str = "equal"    # equal | weighted
    
    # 手续费假设
    taker_fee: float = 0.0004         # 0.04%
    slippage: float = 0.001           # 0.1%

    @property
    def max_position_usdt(self) -> float:
        """最大仓位 = 总资金 × 杠杆 × 使用率"""
        return self.total_capital * self.max_leverage * self.max_capital_usage


@dataclass
class StopLossConfig:
    """止损配置 (V2.3 简化版)"""
    mode: str = "total"               # total: 统一止损
    trigger: str = "grid_floor"       # grid_floor | fixed_pct
    fixed_pct: float = 0.10           # 固定止损 10%


@dataclass
class TakeProfitConfig:
    """止盈配置 (V2.3 简化版)"""
    mode: str = "by_resistance"       # by_resistance | fixed_pct
    fixed_pct: float = 0.05           # 固定止盈 5%


@dataclass
class ResistanceConfig:
    """支撑/阻力位配置"""
    min_strength: int = 80            # 最低强度阈值
    swing_lookbacks: List[int] = field(default_factory=lambda: [5, 13, 34])
    fib_ratios: List[float] = field(default_factory=lambda: [0.382, 0.5, 0.618, 1.0, 1.618])
    merge_tolerance: float = 0.005


# ============================================
# 网格订单数据类
# ============================================

@dataclass
class GridOrder:
    """网格订单 (BTC 等量分配)"""
    grid_id: int                      # 网格编号
    price: float                      # 挂单价格
    amount_usdt: float                # 挂单金额 (USDT) - 用于显示
    amount_btc: float = 0.0           # 挂单数量 (BTC) - 实际下单使用
    strength: float = 0.0             # 支撑/阻力位强度
    source: str = ""                  # 来源 (SW, VOL, FIB, PSY)
    
    # 状态
    is_filled: bool = False
    fill_price: Optional[float] = None
    fill_time: Optional[int] = None
    
    def to_dict(self) -> dict:
        return {
            "grid_id": self.grid_id,
            "price": self.price,
            "amount_usdt": self.amount_usdt,
            "amount_btc": self.amount_btc,
            "strength": self.strength,
            "source": self.source,
            "is_filled": self.is_filled,
            "fill_price": self.fill_price,
        }


@dataclass
class GridState:
    """网格状态"""
    symbol: str
    direction: str = "long"           # 只做多
    
    # 网格区间
    upper_price: float = 0.0          # 上边界 (阻力位)
    lower_price: float = 0.0          # 下边界 (支撑位)
    grid_floor: float = 0.0           # 网格底线 (止损线)
    
    # 网格订单
    buy_orders: List[GridOrder] = field(default_factory=list)   # 买入挂单 (支撑位)
    sell_orders: List[GridOrder] = field(default_factory=list)  # 卖出挂单 (阻力位)
    
    # 网格配置 (初始化时计算，重启后恢复)
    per_grid_contracts: int = 0       # 每格张数（整数）
    contract_size: float = 0.0001     # 合约大小
    num_grids: int = 0                # 网格总数

    # 网格锚点（用于判断是否需要重建网格）
    anchor_price: float = 0.0         # 创建/重建网格时的参考价格
    anchor_ts: int = 0                # 创建/重建网格时间戳（秒）
    
    # 持仓
    total_position_usdt: float = 0.0  # 总持仓
    avg_entry_price: float = 0.0      # 平均入场价
    unrealized_pnl: float = 0.0       # 未实现盈亏
    
    # 兼容属性 (空列表)
    resistance_levels: List = field(default_factory=list)
    support_levels: List = field(default_factory=list)
    
    @property
    def position_usdt(self) -> float:
        """兼容: 返回 total_position_usdt"""
        return self.total_position_usdt
    
    @property
    def entry_price(self) -> float:
        """兼容: 返回 avg_entry_price"""
        return self.avg_entry_price
    
    @property
    def stop_loss(self):
        """兼容: 返回止损信息"""
        return None  # 网格模式不使用传统止损
    
    @property
    def take_profit_plan(self):
        """兼容: 返回止盈计划"""
        return None  # 网格模式按阻力位止盈
    
    @property
    def batches(self) -> List:
        """兼容: 返回空列表"""
        return []
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "upper_price": self.upper_price,
            "lower_price": self.lower_price,
            "grid_floor": self.grid_floor,
            "buy_orders": [o.to_dict() for o in self.buy_orders],
            "sell_orders": [o.to_dict() for o in self.sell_orders],
            # 网格配置 (初始化时计算，重启后恢复)
            "per_grid_contracts": self.per_grid_contracts,
            "contract_size": self.contract_size,
            "num_grids": self.num_grids,
            "anchor_price": self.anchor_price,
            "anchor_ts": self.anchor_ts,
            # 持仓
            "total_position_usdt": self.total_position_usdt,
            "avg_entry_price": self.avg_entry_price,
            "unrealized_pnl": self.unrealized_pnl,
            "resistance_levels": self.resistance_levels,
            "support_levels": self.support_levels,
        }


# ============================================
# 网格仓位管理器
# ============================================

class GridPositionManager:
    """
    网格仓位管理器 (V2.3 简化版)
    
    核心逻辑:
    1. 根据支撑位生成买入挂单
    2. 根据阻力位生成卖出挂单 (止盈)
    3. 统一止损 (跌破网格底线)
    """
    
    def __init__(
        self,
        grid_config: Optional[GridConfig] = None,
        position_config: Optional[PositionConfig] = None,
        stop_loss_config: Optional[StopLossConfig] = None,
        take_profit_config: Optional[TakeProfitConfig] = None,
        resistance_config: Optional[ResistanceConfig] = None,
        symbol: str = "",
        exchange: str = "",
    ):
        self.grid_config = grid_config or GridConfig()
        self.position_config = position_config or PositionConfig()
        self.stop_loss_config = stop_loss_config or StopLossConfig()
        self.take_profit_config = take_profit_config or TakeProfitConfig()
        self.resistance_config = resistance_config or ResistanceConfig()
        self.symbol = symbol
        self.exchange = exchange
        self.logger = get_logger(__name__)
        
        # 当前网格状态
        self.state: Optional[GridState] = None
        
        # 交易历史记录
        self.trade_history: List[Dict] = []
        
        # 持久化
        base_dir = Path(__file__).resolve().parents[3]  # 项目根目录
        self.state_dir = base_dir / "state" / "key_level_grid"
        if self.exchange:
            self.state_dir = self.state_dir / self.exchange.lower()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / f"{self.symbol.lower()}_state.json"
    
    def create_grid(
        self,
        current_price: float,
        support_levels: List[PriceLevel],
        resistance_levels: List[PriceLevel]
    ) -> GridState:
        """
        创建网格
        
        Args:
            current_price: 当前价格
            support_levels: 支撑位列表 (已按强度排序)
            resistance_levels: 阻力位列表 (已按强度排序)
            
        Returns:
            GridState
        """
        # 过滤强支撑/阻力位 (>= min_strength)
        min_strength = self.resistance_config.min_strength
        strong_supports = [
            s for s in support_levels 
            if s.strength >= min_strength and s.price < current_price
        ]
        strong_resistances = [
            r for r in resistance_levels 
            if r.strength >= min_strength and r.price > current_price
        ]
        
        # 限制网格数量
        max_grids = self.grid_config.max_grids
        strong_supports = strong_supports[:max_grids]
        strong_resistances = strong_resistances[:max_grids]
        
        if not strong_supports:
            self.logger.warning(f"没有找到 >= {min_strength} 分的支撑位")
            return None
        
        # 计算网格区间
        if self.grid_config.range_mode == "manual":
            upper_price = self.grid_config.manual_upper
            lower_price = self.grid_config.manual_lower
        else:
            # auto 模式: 基于 S/R
            upper_price = strong_resistances[0].price if strong_resistances else current_price * 1.1
            lower_price = strong_supports[-1].price  # 最低支撑
        
        # 网格底线 (止损线)
        grid_floor = lower_price * (1 - self.grid_config.floor_buffer)
        
        # ============================================
        # 每格名义金额（等额或按强度加权）
        # ============================================
        num_grids = len(strong_supports)
        max_position_usdt = self.position_config.max_position_usdt

        if self.position_config.allocation_mode == "weighted":
            total_strength = sum(max(s.strength, 0) for s in strong_supports)
            buy_orders = []
            for i, s in enumerate(strong_supports):
                if total_strength > 0:
                    amount_usdt = max_position_usdt * (s.strength / total_strength)
                else:
                    amount_usdt = max_position_usdt / num_grids
                amount_btc = amount_usdt / s.price
                buy_orders.append(
                    GridOrder(
                        grid_id=i + 1,
                        price=s.price,
                        amount_usdt=amount_usdt,
                        amount_btc=amount_btc,
                        strength=s.strength,
                        source=getattr(s, 'source', 'unknown'),
                    )
                )
                self.logger.debug(
                    f"  网格#{i+1}: {amount_btc:.6f} BTC @ {s.price:.2f} = {amount_usdt:.0f}U (权重)"
                )
        else:
            per_grid_usdt = max_position_usdt / num_grids
            buy_orders = []
            for i, s in enumerate(strong_supports):
                amount_usdt = per_grid_usdt
                amount_btc = amount_usdt / s.price
                buy_orders.append(
                    GridOrder(
                        grid_id=i + 1,
                        price=s.price,
                        amount_usdt=amount_usdt,
                        amount_btc=amount_btc,
                        strength=s.strength,
                        source=getattr(s, 'source', 'unknown'),
                    )
                )
                self.logger.debug(
                    f"  网格#{i+1}: {amount_btc:.6f} BTC @ {s.price:.2f} = {amount_usdt:.0f}U"
                )
        
        # 生成卖出挂单 (止盈) - BTC 数量在实际提交时根据持仓计算
        sell_orders = []
        if strong_resistances:
            for i, r in enumerate(strong_resistances):
                sell_orders.append(
                    GridOrder(
                        grid_id=i + 1,
                        price=r.price,
                        amount_usdt=0,  # 止盈金额在持仓后计算
                        amount_btc=0,   # 止盈 BTC 在持仓后计算
                        strength=r.strength,
                        source=getattr(r, 'source', 'unknown'),
                    )
                )
        
        # 创建网格状态
        import time
        self.state = GridState(
            symbol=self.symbol,
            direction="long",
            upper_price=upper_price,
            lower_price=lower_price,
            grid_floor=grid_floor,
            buy_orders=buy_orders,
            sell_orders=sell_orders,
            # 锚点（用于重建判断）
            anchor_price=current_price,
            anchor_ts=int(time.time()),
            resistance_levels=[
                {
                    "price": r.price,
                    "strength": r.strength,
                    "source": getattr(r, "source", ""),
                    "timeframe": getattr(r, "timeframe", ""),
                } for r in strong_resistances
            ],
            support_levels=[
                {
                    "price": s.price,
                    "strength": s.strength,
                    "source": getattr(s, "source", ""),
                    "timeframe": getattr(s, "timeframe", ""),
                } for s in strong_supports
            ],
        )
        
        # 保存状态
        self._save_state()
        
        self.logger.info(
            f"创建网格: {self.symbol}, "
            f"区间=[{lower_price:.2f}, {upper_price:.2f}], "
            f"底线={grid_floor:.2f}, "
            f"买单={len(buy_orders)}档, "
            f"卖单={len(sell_orders)}档, "
            f"最大仓位={max_position_usdt:.2f} USDT"
        )
        
        return self.state
    
    def check_buy_trigger(self, current_price: float) -> Optional[GridOrder]:
        """
        检查是否触发买入
        
        Returns:
            触发的 GridOrder，或 None
        """
        if self.state is None:
            return None
        
        for order in self.state.buy_orders:
            if order.is_filled:
                continue
            
            # 价格触及支撑位 (允许一定偏差)
            tolerance = order.price * 0.003  # 0.3% 容差
            if current_price <= order.price + tolerance:
                return order
        
        return None
    
    def execute_buy(self, order: GridOrder, fill_price: float, fill_time: int = None) -> dict:
        """
        执行买入
            
        Returns:
            执行结果
        """
        order.is_filled = True
        order.fill_price = fill_price
        order.fill_time = fill_time
        
        # 更新持仓
        old_position = self.state.total_position_usdt
        old_avg = self.state.avg_entry_price
        
        new_position = old_position + order.amount_usdt
        if new_position > 0:
            self.state.avg_entry_price = (
                old_avg * old_position + fill_price * order.amount_usdt
            ) / new_position
        self.state.total_position_usdt = new_position
        
        # 更新卖出挂单金额 (等额止盈)
        if self.state.sell_orders:
            per_tp = new_position / len(self.state.sell_orders)
            for sell_order in self.state.sell_orders:
                sell_order.amount_usdt = per_tp
        
        self.logger.info(
            f"网格买入: #{order.grid_id} @ {fill_price:.2f}, "
            f"金额={order.amount_usdt:.2f} USDT, "
            f"总持仓={new_position:.2f} USDT, "
            f"均价={self.state.avg_entry_price:.2f}"
        )
        
        # 记录交易历史
        import time
        trade_record = {
            "time": fill_time or int(time.time() * 1000),
            "side": "buy",
            "grid_id": order.grid_id,
            "price": fill_price,
            "amount_usdt": order.amount_usdt,
            "source": order.source,
            "pnl_usdt": 0,
            "pnl_pct": 0,
        }
        self.trade_history.append(trade_record)
        # 只保留最近 50 条
        if len(self.trade_history) > 50:
            self.trade_history = self.trade_history[-50:]
        
        self._save_state()
        
        return {
            "action": "buy",
            "grid_id": order.grid_id,
            "price": fill_price,
            "amount_usdt": order.amount_usdt,
            "total_position": new_position,
            "avg_entry": self.state.avg_entry_price,
        }
    
    def check_sell_trigger(self, current_price: float) -> Optional[GridOrder]:
        """
        检查是否触发卖出 (止盈)
        
        Returns:
            触发的 GridOrder，或 None
        """
        if self.state is None or self.state.total_position_usdt <= 0:
            return None
        
        for order in self.state.sell_orders:
            if order.is_filled:
                continue
            
            if current_price >= order.price:
                return order
        
        return None
    
    def execute_sell(self, order: GridOrder, fill_price: float, fill_time: int = None) -> dict:
        """
        执行卖出 (止盈)
        
        Returns:
            执行结果
        """
        order.is_filled = True
        order.fill_price = fill_price
        order.fill_time = fill_time
        
        # 计算盈亏
        pnl_pct = (fill_price - self.state.avg_entry_price) / self.state.avg_entry_price
        pnl_usdt = order.amount_usdt * pnl_pct
        
        # 更新持仓
        self.state.total_position_usdt -= order.amount_usdt
        
        self.logger.info(
            f"网格止盈: #{order.grid_id} @ {fill_price:.2f}, "
            f"金额={order.amount_usdt:.2f} USDT, "
            f"盈亏={pnl_usdt:.2f} USDT ({pnl_pct:.2%}), "
            f"剩余持仓={self.state.total_position_usdt:.2f} USDT"
        )
                
        # 记录交易历史
        import time
        trade_record = {
            "time": fill_time or int(time.time() * 1000),
            "side": "sell",
            "grid_id": order.grid_id,
            "price": fill_price,
            "amount_usdt": order.amount_usdt,
            "source": order.source,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct * 100,  # 转为百分比
        }
        self.trade_history.append(trade_record)
        if len(self.trade_history) > 50:
            self.trade_history = self.trade_history[-50:]
        
        self._save_state()
        
        return {
            "action": "sell",
            "grid_id": order.grid_id,
            "price": fill_price,
            "amount_usdt": order.amount_usdt,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct,
            "remaining_position": self.state.total_position_usdt,
        }
    
    def check_stop_loss(self, current_price: float) -> bool:
        """
        检查是否触发止损 (跌破网格底线)
        
        Returns:
            是否触发止损
        """
        if self.state is None:
            return False
        
        return current_price <= self.state.grid_floor
    
    def execute_stop_loss(self, fill_price: float) -> dict:
        """
        执行止损 (全部平仓)
            
        Returns:
            执行结果
        """
        if self.state is None or self.state.total_position_usdt <= 0:
            return {"action": "stop_loss", "status": "no_position"}
        
        # 计算盈亏
        pnl_pct = (fill_price - self.state.avg_entry_price) / self.state.avg_entry_price
        pnl_usdt = self.state.total_position_usdt * pnl_pct
        
        result = {
            "action": "stop_loss",
            "price": fill_price,
            "amount_usdt": self.state.total_position_usdt,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct,
            "grid_floor": self.state.grid_floor,
        }
        
        self.logger.warning(
            f"网格止损: 跌破底线 {self.state.grid_floor:.2f}, "
            f"平仓价={fill_price:.2f}, "
            f"平仓金额={self.state.total_position_usdt:.2f} USDT, "
            f"亏损={pnl_usdt:.2f} USDT ({pnl_pct:.2%})"
        )
        
        # 记录交易历史
        import time
        trade_record = {
            "time": int(time.time() * 1000),
            "side": "stop_loss",
            "grid_id": 0,
            "price": fill_price,
            "amount_usdt": self.state.total_position_usdt,
            "source": "grid_floor",
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct * 100,
        }
        self.trade_history.append(trade_record)
        
        # 重置状态
        self.state.total_position_usdt = 0
        self.state.avg_entry_price = 0
        
        self._save_state()
        
        return result
    
    def update_pnl(self, current_price: float):
        """更新未实现盈亏"""
        if self.state is None or self.state.total_position_usdt <= 0:
            return
        
        pnl_pct = (current_price - self.state.avg_entry_price) / self.state.avg_entry_price
        self.state.unrealized_pnl = self.state.total_position_usdt * pnl_pct
    
    def get_summary(self, current_price: float) -> dict:
        """获取网格摘要"""
        if self.state is None:
            return {"has_grid": False}
        
        self.update_pnl(current_price)
        
        filled_buys = sum(1 for o in self.state.buy_orders if o.is_filled)
        filled_sells = sum(1 for o in self.state.sell_orders if o.is_filled)
        
        return {
            "has_grid": True,
            "symbol": self.state.symbol,
            "current_price": current_price,
            "upper_price": self.state.upper_price,
            "lower_price": self.state.lower_price,
            "grid_floor": self.state.grid_floor,
            "total_position_usdt": self.state.total_position_usdt,
            "avg_entry_price": self.state.avg_entry_price,
            "unrealized_pnl": self.state.unrealized_pnl,
            "buy_orders_filled": f"{filled_buys}/{len(self.state.buy_orders)}",
            "sell_orders_filled": f"{filled_sells}/{len(self.state.sell_orders)}",
            "distance_to_floor": (current_price - self.state.grid_floor) / current_price,
        }
    
    def reset(self):
        """重置网格"""
        self.state = None
        self.logger.info("网格已重置")
        self._save_state()
    
    # ============================================
    # 持久化
    # ============================================
    
    def _save_state(self) -> None:
        """持久化当前网格状态和历史成交"""
        try:
            payload: Dict = {"trade_history": self.trade_history}
            if self.state:
                payload["grid_state"] = self.state.to_dict()
            else:
                payload["grid_state"] = None
            
            with self.state_file.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存网格状态失败: {e}", exc_info=True)
    
    def restore_state(self, current_price: float, price_tolerance: float = 0.02) -> bool:
        """
        恢复网格状态
        
        Args:
            current_price: 当前市场价格
            price_tolerance: 价格偏离容忍度 (默认 2%)
        """
        if not self.state_file.exists():
            return False
        
        try:
            with self.state_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.logger.error(f"读取网格状态失败: {e}", exc_info=True)
            return False
        
        try:
            grid_data = data.get("grid_state")
            self.trade_history = data.get("trade_history", [])
            
            if not grid_data:
                self.logger.info("无网格状态可恢复")
                return False
            
            # 重建 GridState
            buy_orders = [
                GridOrder(
                    grid_id=o.get("grid_id", 0),
                    price=o.get("price", 0.0),
                    amount_usdt=o.get("amount_usdt", 0.0),
                    strength=o.get("strength", 0.0),
                    source=o.get("source", ""),
                    is_filled=o.get("is_filled", False),
                    fill_price=o.get("fill_price"),
                    fill_time=o.get("fill_time"),
                )
                for o in grid_data.get("buy_orders", [])
            ]
            sell_orders = [
                GridOrder(
                    grid_id=o.get("grid_id", 0),
                    price=o.get("price", 0.0),
                    amount_usdt=o.get("amount_usdt", 0.0),
                    strength=o.get("strength", 0.0),
                    source=o.get("source", ""),
                    is_filled=o.get("is_filled", False),
                    fill_price=o.get("fill_price"),
                    fill_time=o.get("fill_time"),
                )
                for o in grid_data.get("sell_orders", [])
            ]
            
            restored_state = GridState(
                symbol=grid_data.get("symbol", self.symbol),
                direction=grid_data.get("direction", "long"),
                upper_price=grid_data.get("upper_price", 0.0),
                lower_price=grid_data.get("lower_price", 0.0),
                grid_floor=grid_data.get("grid_floor", 0.0),
                buy_orders=buy_orders,
                sell_orders=sell_orders,
                # 恢复网格配置
                per_grid_contracts=grid_data.get("per_grid_contracts", 0),
                contract_size=grid_data.get("contract_size", 0.0001),
                num_grids=grid_data.get("num_grids", 0),
                # 恢复锚点
                anchor_price=grid_data.get("anchor_price", 0.0),
                anchor_ts=grid_data.get("anchor_ts", 0),
                # 持仓
                total_position_usdt=grid_data.get("total_position_usdt", 0.0),
                avg_entry_price=grid_data.get("avg_entry_price", 0.0),
                unrealized_pnl=grid_data.get("unrealized_pnl", 0.0),
                resistance_levels=grid_data.get("resistance_levels", []),
                support_levels=grid_data.get("support_levels", []),
            )
            
            # 日志打印恢复的网格配置
            if restored_state.per_grid_contracts > 0:
                self.logger.info(
                    f"📊 恢复网格配置: per_grid_contracts={restored_state.per_grid_contracts}张, "
                    f"contract_size={restored_state.contract_size}, num_grids={restored_state.num_grids}"
                )
            
            # 价格校验，防止过期状态
            if current_price > 0 and restored_state.lower_price > 0 and restored_state.upper_price > 0:
                below_ok = current_price >= restored_state.lower_price * (1 - price_tolerance)
                above_ok = current_price <= restored_state.upper_price * (1 + price_tolerance)
                if not (below_ok and above_ok):
                    self.logger.warning(
                        f"恢复状态失败: 当前价偏离网格区间 ({restored_state.lower_price:.2f}~{restored_state.upper_price:.2f}), "
                        f"current={current_price:.2f}"
                    )
                    return False
            
            self.state = restored_state
            self.logger.info("已恢复网格状态和交易历史")           
            return True
        except Exception as e:
            self.logger.error(f"恢复网格状态失败: {e}", exc_info=True)
            return False
    
    def clear_state_file(self) -> None:
        """删除持久化文件"""
        try:
            if self.state_file.exists():
                self.state_file.unlink()
        except Exception:
            self.logger.warning("删除状态文件失败", exc_info=True)
    
    # ============================================
    # 兼容层 - 供 strategy.py 调用
    # ============================================
    
    @property
    def resistance_calc(self):
        """兼容: 返回阻力计算器"""
        from key_level_grid.resistance import ResistanceCalculator
        if not hasattr(self, '_resistance_calc'):
            self._resistance_calc = ResistanceCalculator()
        return self._resistance_calc
    
    def update_position(self, current_price: float, market_state=None) -> dict:
        """兼容: 更新仓位状态"""
        result = {"status": "ok", "actions": []}
        
        # 检查止损
        if self.check_stop_loss(current_price):
            result["status"] = "stop_loss_triggered"
            result["actions"].append({
                "action": "close_all",
                "price": current_price,
                "reason": "grid_floor_breach"
            })
        
        # 更新未实现盈亏
        self.update_pnl(current_price)
        
        return result
    
    def open_position(self, entry_price: float, stop_loss_price: float = 0, 
                      direction: str = "long", market_state=None, klines=None):
        """兼容: 开仓 (实际由网格触发)"""
        # 简化实现: 返回当前状态
        return self.state
    
    def close_position(self, price: float, reason: str = "") -> dict:
        """兼容: 平仓"""
        return self.execute_stop_loss(price)
    
    def get_position_summary(self, current_price: float) -> dict:
        """兼容: 获取仓位摘要"""
        summary = self.get_summary(current_price)
        
        # 转换为旧版格式
        if not summary.get("has_grid"):
            return {
                "has_position": False,
                "direction": "none",
                "position_usdt": 0,
            }
        
        return {
            "has_position": summary["total_position_usdt"] > 0,
            "direction": self.state.direction if self.state else "none",
            "position_usdt": summary["total_position_usdt"],
            "entry_price": summary["avg_entry_price"],
            "unrealized_pnl": summary["unrealized_pnl"],
            "grid_floor": summary["grid_floor"],
        }


# ============================================
# 保留旧版兼容 (后续可移除)
# ============================================

# 旧版 EntryTrigger, EntryBatch, PositionState, KeyLevelPositionManager
# 已注释，如需恢复可取消注释

# from enum import Enum
# class EntryTrigger(Enum):
#     """入场触发类型 (旧版)"""
#     SIGNAL = "signal"
#     PULLBACK = "pullback"
#     BREAKOUT_CONFIRM = "breakout_confirm"

# @dataclass
# class EntryBatch:
#     """分批入场配置 (旧版)"""
#     trigger: EntryTrigger
#     size_pct: float
#     price_offset: float = 0.0
#     is_filled: bool = False
#     fill_price: Optional[float] = None
#     fill_usdt: float = 0.0

# 别名 - 保持向后兼容
KeyLevelPositionManager = GridPositionManager
