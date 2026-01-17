"""
仓位管理模块 (V2.3 简化版)

基于支撑/阻力位的网格仓位管理
"""

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    
    # ============================================
    # Spec2.0 核心策略参数
    # ============================================
    sell_quota_ratio: float = 0.7        # 动态止盈比例
    min_profit_pct: float = 0.005        # 均价利润保护阈值
    buy_price_buffer_pct: float = 0.002   # 买单空间缓冲
    sell_price_buffer_pct: float = 0.002  # 卖单空间缓冲
    max_fill_per_level: int = 1           # 单水位最大补买次数
    base_amount_per_grid: float = 1.0    # 标准网格单位（BTC数量）
    base_position_locked: float = 0.0    # 固定底仓数量（BTC数量）
    recon_interval_sec: int = 30         # Recon 周期
    order_action_timeout_sec: int = 10   # 挂/撤单超时
    restore_state_enabled: bool = True   # 是否从持久化恢复网格


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
    min_distance_pct: float = 0.005   # 最小距离 0.5% (过滤太近的价位)
    max_distance_pct: float = 0.30    # 最大距离 30% (过滤太远的价位)


# ============================================
# 水位状态机
# ============================================

class LevelStatus(str, Enum):
    IDLE = "IDLE"
    PLACING = "PLACING"
    ACTIVE = "ACTIVE"
    FILLED = "FILLED"
    CANCELING = "CANCELING"


@dataclass
class GridLevelState:
    """网格水位状态"""
    level_id: int
    price: float
    side: str  # buy | sell
    role: str = "support"  # support | resistance
    status: LevelStatus = LevelStatus.IDLE
    active_order_id: str = ""
    order_id: str = ""
    target_qty: float = 0.0          # 目标数量（合约张数）
    open_qty: float = 0.0            # 实际挂单数量（合约张数）
    filled_qty: float = 0.0          # 已成交数量（合约张数）
    fill_counter: int = 0            # 水位补买计数
    last_action_ts: int = 0
    last_error: str = ""

    def to_dict(self) -> dict:
        return {
            "level_id": self.level_id,
            "price": self.price,
            "side": self.side,
            "role": self.role,
            "status": self.status.value if isinstance(self.status, LevelStatus) else str(self.status),
            "active_order_id": self.active_order_id,
            "order_id": self.order_id,
            "target_qty": self.target_qty,
            "open_qty": self.open_qty,
            "filled_qty": self.filled_qty,
            "fill_counter": self.fill_counter,
            "last_action_ts": self.last_action_ts,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GridLevelState":
        status = data.get("status", LevelStatus.IDLE)
        try:
            status = LevelStatus(status)
        except Exception:
            status = LevelStatus.IDLE
        return cls(
            level_id=int(data.get("level_id", 0)),
            price=float(data.get("price", 0)),
            side=data.get("side", "buy"),
            role=data.get("role", "support" if data.get("side") == "buy" else "resistance"),
            status=status,
            active_order_id=data.get("active_order_id", ""),
            order_id=data.get("order_id", ""),
            target_qty=float(data.get("target_qty", 0) or 0),
            open_qty=float(data.get("open_qty", 0) or 0),
            filled_qty=float(data.get("filled_qty", 0) or 0),
            fill_counter=int(data.get("fill_counter", 0) or 0),
            last_action_ts=int(data.get("last_action_ts", 0) or 0),
            last_error=data.get("last_error", ""),
        )

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
class ActiveFill:
    """正在持仓中的买入成交记录"""
    order_id: str
    price: float
    qty: float
    level_id: int
    timestamp: int
    
    # T1.2: 逐级邻位映射追踪字段
    target_sell_level_id: Optional[int] = None  # 止盈应挂在哪个水位
    sell_order_id: Optional[str] = None         # 已挂卖单的订单 ID
    sell_qty: float = 0.0                        # 已挂卖单数量

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "price": self.price,
            "qty": self.qty,
            "level_id": self.level_id,
            "timestamp": self.timestamp,
            # T1.2: 映射追踪字段
            "target_sell_level_id": self.target_sell_level_id,
            "sell_order_id": self.sell_order_id,
            "sell_qty": self.sell_qty,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActiveFill":
        # T1.3: 兼容性处理 - 旧版数据可能缺少新字段
        return cls(
            order_id=data.get("order_id", ""),
            price=float(data.get("price", 0)),
            qty=float(data.get("qty", 0)),
            level_id=int(data.get("level_id", 0)),
            timestamp=int(data.get("timestamp", 0)),
            # T1.3: 新字段使用默认值（兼容旧版）
            target_sell_level_id=data.get("target_sell_level_id"),  # None if missing
            sell_order_id=data.get("sell_order_id"),                # None if missing
            sell_qty=float(data.get("sell_qty", 0)),                # 0 if missing
        )


@dataclass
class GridState:
    """网格状态"""
    symbol: str
    direction: str = "long"           # 只做多
    
    # 网格区间
    upper_price: float = 0.0          # 上边界 (阻力位)
    lower_price: float = 0.0          # 下边界 (支撑位)
    grid_floor: float = 0.0           # 网格底线 (止损线)
    
    # 网格订单（旧结构，保留兼容）
    buy_orders: List[GridOrder] = field(default_factory=list)   # 买入挂单 (支撑位)
    sell_orders: List[GridOrder] = field(default_factory=list)  # 卖出挂单 (阻力位)

    # 水位状态机
    support_levels_state: List[GridLevelState] = field(default_factory=list)
    resistance_levels_state: List[GridLevelState] = field(default_factory=list)
    
    # 精确仓位清单 (Spec 3.3+)
    active_inventory: List[ActiveFill] = field(default_factory=list)
    settled_inventory: List[ActiveFill] = field(default_factory=list) # 最近平仓记录
    
    # T1.1: 逐级邻位映射表 {support_level_id: adjacent_sell_level_id}
    level_mapping: Dict[int, int] = field(default_factory=dict)
    
    # 网格配置 (初始化时计算，重启后恢复)
    per_grid_contracts: int = 0       # 每格张数（整数）
    contract_size: float = 0.0001     # 合约大小
    num_grids: int = 0                # 网格总数

    # Spec2.0 参数快照
    sell_quota_ratio: float = 0.7
    min_profit_pct: float = 0.005
    buy_price_buffer_pct: float = 0.002
    sell_price_buffer_pct: float = 0.002
    base_amount_per_grid: float = 1.0  # BTC数量
    base_position_locked: float = 0.0  # BTC数量
    max_fill_per_level: int = 1
    recon_interval_sec: int = 30
    order_action_timeout_sec: int = 10

    # 网格锚点（用于判断是否需要重建网格）
    anchor_price: float = 0.0         # 创建/重建网格时的参考价格
    anchor_ts: int = 0                # 创建/重建网格时间戳（秒）
    
    # 持仓
    total_position_usdt: float = 0.0  # 总持仓（展示用）
    avg_entry_price: float = 0.0      # 平均入场价
    unrealized_pnl: float = 0.0       # 未实现盈亏
    total_position_contracts: float = 0.0  # 合约张数（内部口径）
    
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
            "support_levels_state": [s.to_dict() for s in self.support_levels_state],
            "resistance_levels_state": [r.to_dict() for r in self.resistance_levels_state],
            "active_inventory": [f.to_dict() for f in self.active_inventory],
            "settled_inventory": [f.to_dict() for f in self.settled_inventory],
            # T1.1: 逐级邻位映射表
            "level_mapping": self.level_mapping,
            # 网格配置 (初始化时计算，重启后恢复)
            "per_grid_contracts": self.per_grid_contracts,
            "contract_size": self.contract_size,
            "num_grids": self.num_grids,
            "sell_quota_ratio": self.sell_quota_ratio,
            "min_profit_pct": self.min_profit_pct,
            "buy_price_buffer_pct": self.buy_price_buffer_pct,
            "sell_price_buffer_pct": self.sell_price_buffer_pct,
            "base_amount_per_grid": self.base_amount_per_grid,
            "base_position_locked": self.base_position_locked,
            "max_fill_per_level": self.max_fill_per_level,
            "recon_interval_sec": self.recon_interval_sec,
            "order_action_timeout_sec": self.order_action_timeout_sec,
            "anchor_price": self.anchor_price,
            "anchor_ts": self.anchor_ts,
            # 持仓
            "total_position_usdt": self.total_position_usdt,
            "avg_entry_price": self.avg_entry_price,
            "unrealized_pnl": self.unrealized_pnl,
            "total_position_contracts": self.total_position_contracts,
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
        # 1. 汇总所有原始价位，统一进行全局去重
        all_raw_levels = support_levels + resistance_levels
        
        # 过滤强度
        min_strength = self.resistance_config.min_strength
        qualified_levels = [l for l in all_raw_levels if l.strength >= min_strength]
        
        # 全局去重：相近价位保留强度更高者
        def _deduplicate_all(levels: List[PriceLevel]) -> List[PriceLevel]:
            if not levels:
                return []
            sorted_lvls = sorted(levels, key=lambda x: x.price)
            deduped: List[PriceLevel] = []
            tolerance = self.resistance_config.merge_tolerance or 0.005
            for lvl in sorted_lvls:
                if not deduped:
                    deduped.append(lvl)
                    continue
                last = deduped[-1]
                if last.price > 0 and abs(lvl.price - last.price) / last.price <= tolerance:
                    if lvl.strength > last.strength:
                        deduped[-1] = lvl
                else:
                    deduped.append(lvl)
            return deduped

        final_pool = _deduplicate_all(qualified_levels)
        
        # 2. 根据现价将去重后的池子划分为支撑和阻力
        strong_supports = [l for l in final_pool if l.price < current_price]
        strong_resistances = [l for l in final_pool if l.price > current_price]

        # 排序：支撑从高到低（近到远），阻力从低到高（近到远）
        strong_supports = sorted(strong_supports, key=lambda x: x.price, reverse=True)
        strong_resistances = sorted(strong_resistances, key=lambda x: x.price)
        
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

        # 手动区间过滤（确保支撑/阻力位在区间内）
        if self.grid_config.range_mode == "manual" and upper_price > 0 and lower_price > 0:
            strong_supports = [
                s for s in strong_supports if lower_price <= s.price <= upper_price
            ]
            strong_resistances = [
                r for r in strong_resistances if lower_price <= r.price <= upper_price
            ]
        
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
            # Spec2.0 参数快照
            sell_quota_ratio=self.grid_config.sell_quota_ratio,
            min_profit_pct=self.grid_config.min_profit_pct,
            buy_price_buffer_pct=self.grid_config.buy_price_buffer_pct,
            sell_price_buffer_pct=self.grid_config.sell_price_buffer_pct,
            base_amount_per_grid=self.grid_config.base_amount_per_grid,
            base_position_locked=self.grid_config.base_position_locked,
            max_fill_per_level=self.grid_config.max_fill_per_level,
            recon_interval_sec=self.grid_config.recon_interval_sec,
            order_action_timeout_sec=self.grid_config.order_action_timeout_sec,
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

        # 初始化水位状态机（使用全局唯一 level_id）
        # 支撑位 ID: 1, 2, 3, ...
        # 阻力位 ID: 1001, 1002, 1003, ... (避免与支撑位 ID 重叠)
        RESISTANCE_ID_OFFSET = 1000
        
        self.state.support_levels_state = [
            GridLevelState(
                level_id=i + 1,
                price=s.price,
                side="buy",
                role="support",
                status=LevelStatus.IDLE,
            )
            for i, s in enumerate(strong_supports)
        ]
        self.state.resistance_levels_state = [
            GridLevelState(
                level_id=RESISTANCE_ID_OFFSET + i + 1,
                price=r.price,
                side="sell",
                role="resistance",
                status=LevelStatus.IDLE,
            )
            for i, r in enumerate(strong_resistances)
        ]
        
        # T2.2: 构建逐级邻位映射
        self.state.level_mapping = self.build_level_mapping()
        
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

    def get_base_amount_contracts(self, exchange_min_qty: float = 0.0) -> float:
        """将 base_amount_per_grid (BTC) 转为合约张数"""
        if not self.state:
            return 0.0
        base_btc = float(self.state.base_amount_per_grid or 0)
        return self._btc_to_contracts(base_btc, exchange_min_qty)
    
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

    # ============================================
    # Spec2.0 核心算法辅助方法
    # ============================================

    def update_position_snapshot(self, holdings_contracts: float, avg_entry_price: float) -> None:
        if not self.state:
            return
        # holdings_contracts 语义改为币数量 (BTC)
        self.state.total_position_contracts = max(holdings_contracts, 0.0)
        self.state.avg_entry_price = max(avg_entry_price, 0.0)

    # ============================================
    # T2.1: 逐级邻位映射构建
    # ============================================
    
    def build_level_mapping(self) -> Dict[int, int]:
        """
        构建逐级邻位映射表
        
        规则：每个支撑位映射到其物理价格上方的第一个水位（邻位）
        
        Returns:
            {support_level_id: adjacent_level_id}
        """
        if not self.state:
            return {}
        
        # 合并所有水位并按价格升序排列
        all_levels: List[GridLevelState] = (
            self.state.support_levels_state + self.state.resistance_levels_state
        )
        sorted_levels = sorted(all_levels, key=lambda x: x.price)
        
        mapping: Dict[int, int] = {}
        min_profit_pct = float(self.state.min_profit_pct or 0)
        missing_adjacent_levels: List[float] = []  # 记录无邻位的支撑位价格
        
        for i, level in enumerate(sorted_levels):
            # 只为支撑位建立映射
            if level.role != "support":
                continue
            
            # 最小利润价格阈值
            min_sell_price = level.price * (1 + min_profit_pct)
            
            # 查找上方第一个有效水位（邻位）
            target_level = None
            for j in range(i + 1, len(sorted_levels)):
                candidate = sorted_levels[j]
                if candidate.price > min_sell_price:
                    target_level = candidate
                    break
            
            if target_level:
                mapping[level.level_id] = target_level.level_id
                self.logger.debug(
                    f"📍 映射: L_{level.level_id}({level.price:.2f}) → L_{target_level.level_id}({target_level.price:.2f})"
                )
            else:
                # 边界情况：最高支撑位无上方邻位
                missing_adjacent_levels.append(level.price)
        
        # 边界告警：有支撑位无邻位
        if missing_adjacent_levels:
            self.logger.warning(
                f"⚠️ [Mapping] 以下支撑位无上方邻位，止盈单无法自动挂出: {missing_adjacent_levels}"
            )
        
        self.logger.info(
            f"📍 [Mapping] 构建完成: {len(mapping)} 个映射, "
            f"{len(missing_adjacent_levels)} 个无邻位"
        )
        
        return mapping
    
    def rebuild_level_mapping(self) -> None:
        """重建邻位映射（网格重建后调用）"""
        if not self.state:
            return
        self.state.level_mapping = self.build_level_mapping()
        self._save_state()
        self.logger.info("📍 [Mapping] 已重建邻位映射")
    
    def _normalize_level_ids_and_rebuild_mapping(self) -> None:
        """
        规范化 level_id 并重建映射（兼容旧版状态文件）
        
        旧版状态文件中，支撑位和阻力位的 level_id 可能重叠（都从 1 开始）。
        新版要求全局唯一：支撑位 1-999，阻力位 1001+。
        
        此方法在 restore_state 后调用，确保 ID 唯一并重建映射。
        """
        if not self.state:
            return
        
        RESISTANCE_ID_OFFSET = 1000
        needs_rebuild = False
        
        # 检查是否有 ID 冲突
        support_ids = {lvl.level_id for lvl in self.state.support_levels_state}
        resistance_ids = {lvl.level_id for lvl in self.state.resistance_levels_state}
        
        # 如果阻力位 ID 都小于 1000，说明是旧版格式，需要重新分配
        if self.state.resistance_levels_state:
            max_resistance_id = max(lvl.level_id for lvl in self.state.resistance_levels_state)
            if max_resistance_id < RESISTANCE_ID_OFFSET:
                self.logger.info("📍 [Mapping] 检测到旧版 level_id 格式，正在规范化...")
                
                # 重新分配阻力位 ID
                for i, lvl in enumerate(self.state.resistance_levels_state):
                    old_id = lvl.level_id
                    lvl.level_id = RESISTANCE_ID_OFFSET + i + 1
                    self.logger.debug(f"📍 阻力位 ID 重分配: {old_id} → {lvl.level_id}")
                
                needs_rebuild = True
        
        # 检查是否有 ID 重叠
        overlap = support_ids & resistance_ids
        if overlap:
            self.logger.warning(f"📍 [Mapping] 检测到 ID 重叠: {overlap}，正在修复...")
            for i, lvl in enumerate(self.state.resistance_levels_state):
                lvl.level_id = RESISTANCE_ID_OFFSET + i + 1
            needs_rebuild = True
        
        # 如果映射为空或需要重建，则重建映射
        if needs_rebuild or not self.state.level_mapping:
            self.state.level_mapping = self.build_level_mapping()
            self.logger.info(f"📍 [Mapping] 已重建邻位映射: {len(self.state.level_mapping)} 个映射")

    # ============================================
    # T3.1 & T3.2: 逐级邻位同步
    # ============================================
    
    # 价格容差常量（0.01%）
    PRICE_TOLERANCE = 0.0001
    
    @staticmethod
    def price_matches(p1: float, p2: float, tolerance: float = PRICE_TOLERANCE) -> bool:
        """判断两个价格是否匹配（考虑容差）"""
        if p2 == 0:
            return False
        return abs(p1 - p2) / p2 < tolerance
    
    def _get_level_by_id(self, level_id: int) -> Optional[GridLevelState]:
        """通过 level_id 查找水位"""
        if not self.state:
            return None
        for lvl in self.state.support_levels_state:
            if lvl.level_id == level_id:
                return lvl
        for lvl in self.state.resistance_levels_state:
            if lvl.level_id == level_id:
                return lvl
        return None
    
    def _index_orders_by_level(
        self,
        open_orders: List[Dict],
        side: str = "sell",
    ) -> Dict[int, List[Dict]]:
        """
        T3.2: 按水位索引交易所挂单
        
        Args:
            open_orders: 交易所挂单列表
            side: 订单方向 ("buy" | "sell")
        
        Returns:
            {level_id: [orders]}
        """
        if not self.state:
            return {}
        
        # 构建水位索引（支撑位 + 阻力位）
        all_levels = self.state.support_levels_state + self.state.resistance_levels_state
        
        result: Dict[int, List[Dict]] = {}
        
        for order in open_orders:
            if order.get("side", "") != side:
                continue
            
            order_price = float(order.get("price", 0) or 0)
            if order_price <= 0:
                continue
            
            # 使用容差匹配找到对应水位
            matched_level = None
            for lvl in all_levels:
                if self.price_matches(order_price, lvl.price):
                    matched_level = lvl
                    break
            
            if matched_level:
                result.setdefault(matched_level.level_id, []).append(order)
        
        return result
    
    def sync_mapping(
        self,
        current_price: float,
        open_orders: List[Dict],
        exchange_min_qty: float,
    ) -> List[Dict[str, Any]]:
        """
        T3.1: 逐级邻位映射同步
        
        遍历每个有成交的支撑位，根据邻位映射计算应挂卖单配额，
        与实盘挂单对比，生成补单/撤单动作。
        
        Args:
            current_price: 当前价格
            open_orders: 交易所挂单列表
            exchange_min_qty: 交易所最小下单量
        
        Returns:
            卖单动作列表 [{"action": "place"|"cancel", ...}]
        """
        if not self.state:
            return []
        
        actions: List[Dict[str, Any]] = []
        base_qty = float(self.state.base_amount_per_grid or 0)
        sell_quota_ratio = float(self.state.sell_quota_ratio or 0.7)
        
        # 索引交易所卖单
        sell_orders_by_level = self._index_orders_by_level(open_orders, side="sell")
        
        # 汇总每个目标水位的期望卖单量
        # {target_level_id: expected_qty}
        expected_sell_by_level: Dict[int, float] = {}
        
        for support_lvl in self.state.support_levels_state:
            fill_count = int(support_lvl.fill_counter or 0)
            if fill_count <= 0:
                continue
            
            # 查找邻位映射
            target_level_id = self.state.level_mapping.get(support_lvl.level_id)
            if not target_level_id:
                # 无邻位映射（最高支撑位无上方水位）
                self.logger.warning(
                    f"⚠️ [SyncMapping] 支撑位 L_{support_lvl.level_id}({support_lvl.price:.2f}) "
                    f"无邻位映射，跳过卖单同步"
                )
                continue
            
            # 计算该支撑位贡献的卖单量
            contrib_qty = fill_count * base_qty * sell_quota_ratio
            expected_sell_by_level[target_level_id] = (
                expected_sell_by_level.get(target_level_id, 0) + contrib_qty
            )
        
        # 获取所有目标水位（包括阻力位和可能的高位支撑位）
        all_levels = self.state.support_levels_state + self.state.resistance_levels_state
        level_by_id = {lvl.level_id: lvl for lvl in all_levels}
        
        # 收集所有涉及的目标水位
        all_target_level_ids = set(expected_sell_by_level.keys()) | set(sell_orders_by_level.keys())
        
        for target_level_id in all_target_level_ids:
            target_lvl = level_by_id.get(target_level_id)
            if not target_lvl:
                continue
            
            expected_qty = expected_sell_by_level.get(target_level_id, 0)
            existing_orders = sell_orders_by_level.get(target_level_id, [])
            
            # 计算实盘已挂量
            open_qty = sum(
                float(o.get("base_amount", 0) or 0) or 
                float(o.get("contracts", 0) or 0) * float(self.state.contract_size or 0)
                for o in existing_orders
            )
            
            # 计算 PLACING 状态的待挂单量（冲突防御）
            placing_qty = 0.0
            if target_lvl.status == LevelStatus.PLACING:
                placing_qty = float(target_lvl.target_qty or 0)
            
            # 有效已挂量 = 实盘挂单 + 待挂单
            effective_pending = open_qty + placing_qty
            
            # 计算缺口
            deficit = expected_qty - effective_pending
            
            # 精度处理：向下取整到最小单位
            deficit = max(0, deficit)
            if deficit > 0 and deficit < exchange_min_qty:
                deficit = 0  # 低于最小单位，丢弃
            
            # 5% 容差判断
            tolerance_threshold = max(exchange_min_qty, expected_qty * 0.05)
            
            if deficit >= tolerance_threshold:
                # 需要补单
                place_qty = max(deficit, exchange_min_qty)
                actions.append({
                    "action": "place",
                    "side": "sell",
                    "price": target_lvl.price,
                    "qty": place_qty,
                    "level_id": target_level_id,
                    "reason": "sync_mapping_deficit",
                    "expected_qty": expected_qty,
                    "open_qty": open_qty,
                    "placing_qty": placing_qty,
                })
                target_lvl.status = LevelStatus.PLACING
                target_lvl.target_qty = place_qty
                target_lvl.last_action_ts = int(time.time())
                self.logger.info(
                    f"📈 [SyncMapping] 补卖单: L_{target_level_id}({target_lvl.price:.2f}), "
                    f"expected={expected_qty:.6f}, open={open_qty:.6f}, placing={placing_qty:.6f}, "
                    f"deficit={deficit:.6f}"
                )
            
            elif expected_qty <= 0 and open_qty > 0:
                # 期望量为 0 但有挂单，需要撤单
                for order in existing_orders:
                    actions.append({
                        "action": "cancel",
                        "side": "sell",
                        "price": target_lvl.price,
                        "order_id": order.get("id", ""),
                        "level_id": target_level_id,
                        "reason": "sync_mapping_no_target",
                    })
                target_lvl.status = LevelStatus.CANCELING
                target_lvl.last_action_ts = int(time.time())
                self.logger.info(
                    f"📉 [SyncMapping] 撤卖单: L_{target_level_id}({target_lvl.price:.2f}), "
                    f"expected=0, open={open_qty:.6f}"
                )
            
            elif expected_qty > 0 and abs(open_qty - expected_qty) > tolerance_threshold:
                # 数量偏差过大，撤单后重挂
                for order in existing_orders:
                    actions.append({
                        "action": "cancel",
                        "side": "sell",
                        "price": target_lvl.price,
                        "order_id": order.get("id", ""),
                        "level_id": target_level_id,
                        "reason": "sync_mapping_rebalance",
                        "expected_qty": expected_qty,
                        "open_qty": open_qty,
                    })
                target_lvl.status = LevelStatus.CANCELING
                target_lvl.last_action_ts = int(time.time())
                self.logger.info(
                    f"🔄 [SyncMapping] 重平衡: L_{target_level_id}({target_lvl.price:.2f}), "
                    f"expected={expected_qty:.6f}, open={open_qty:.6f}"
                )
            
            else:
                # 数量匹配，无需操作
                if existing_orders:
                    target_lvl.status = LevelStatus.ACTIVE
                    target_lvl.active_order_id = existing_orders[0].get("id", "")
                    target_lvl.open_qty = open_qty
        
        return actions

    def clear_fill_counters(self, reason: str = "manual") -> None:
        if not self.state:
            return
        self.state.active_inventory = []
        self.state.settled_inventory = [] # 同时也清理最近平仓，保持视图干净
        for lvl in self.state.support_levels_state:
            lvl.fill_counter = 0
        self.logger.info("🧹 fill_counter & Inventory 清零: reason=%s", reason)
        self._save_state()

    def reconcile_counters_with_position(
        self,
        current_price: float,
        holdings_btc: float,
        recent_trades: Optional[List[Dict]] = None,
    ) -> Optional[Dict[str, str]]:
        if not self.state:
            return None
        base_qty = float(self.state.base_amount_per_grid or 0)
        if base_qty <= 0:
            return None
        
        holdings_btc = max(float(holdings_btc or 0), 0.0)
        # 计算网格部分持仓（扣除底仓）
        locked_qty = float(self.state.base_position_locked or 0)
        grid_holdings = max(holdings_btc - locked_qty, 0.0)
        
        expected = int(round(grid_holdings / base_qty))
        current = len(self.state.active_inventory)
        
        if holdings_btc == 0:
            if current > 0:
                self.clear_fill_counters("auto_clear_zero_position")
                return {"action": "auto_clear", "detail": "持仓为 0，已清空清单"}
            return None
            
        if expected == current:
            return None
            
        self.logger.warning(
            "⚠️ [Inventory] 持仓清单不一致，启动同步: expected=%d, current=%d, grid_holdings=%.6f",
            expected, current, grid_holdings
        )

        # 情况 A: 清单记录少于实际持仓 -> 补齐清单
        if current < expected:
            diff = expected - current
            added = 0
            
            # A1. 尝试从真实的成交记录补齐 (精确匹配)
            if recent_trades:
                # 已有记录的 order_id 集合
                existing_ids = {f.order_id for f in self.state.active_inventory if f.order_id}
                
                # 按时间倒序尝试认领
                for t in recent_trades:
                    if added >= diff:
                        break
                    
                    order_id = str(t.get("order_id") or t.get("id", ""))
                    if order_id in existing_ids:
                        continue
                        
                    price = float(t.get("price", 0) or 0)
                    
                    # 优先使用记录中的 level_id
                    lvl = None
                    trade_level_id = t.get("level_id")
                    if trade_level_id is not None:
                        # 在当前网格中寻找该 level_id
                        for l in self.state.support_levels_state:
                            if l.level_id == trade_level_id:
                                lvl = l
                                break
                    
                    # 如果记录中没有 level_id 或当前网格没匹配到，再按价格匹配
                    if not lvl:
                        lvl = self._find_support_level_for_price(price)
                        
                    if lvl:
                        # 检查该水位是否已满
                        lvl_count = sum(1 for f in self.state.active_inventory if f.level_id == lvl.level_id)
                        if lvl_count < int(self.state.max_fill_per_level or 1):
                            new_fill = ActiveFill(
                                order_id=order_id,
                                price=price,
                                qty=float(t.get("amount", base_qty)),
                                level_id=lvl.level_id,
                                timestamp=int(t.get("timestamp", time.time()*1000) / 1000)
                            )
                            self.state.active_inventory.append(new_fill)
                            existing_ids.add(order_id)
                            added += 1
            
            # A2. 兜底补齐：按价格由近及远填入清单 (模拟填充)
            if added < diff:
                price_ceiling = max(float(current_price or 0), float(self.state.avg_entry_price or 0))
                supports = sorted(
                    [lvl for lvl in self.state.support_levels_state if lvl.price <= price_ceiling * 1.01],
                    key=lambda x: x.price, reverse=True
                )
                
                for lvl in supports:
                    while added < diff:
                        lvl_count = sum(1 for f in self.state.active_inventory if f.level_id == lvl.level_id)
                        if lvl_count < int(self.state.max_fill_per_level or 1):
                            new_fill = ActiveFill(
                                order_id=f"recon_{int(time.time())}_{added}",
                                price=lvl.price,
                                qty=base_qty,
                                level_id=lvl.level_id,
                                timestamp=int(time.time())
                            )
                            self.state.active_inventory.append(new_fill)
                            added += 1
                        else:
                            break
            
            self.logger.info("🧱 [Inventory] 补齐了 %d 条持仓记录", added)

        # 情况 B: 清单记录多于实际持仓 -> 移除清单记录 (FIFO)
        elif current > expected:
            diff = current - expected
            removed = 0
            for _ in range(diff):
                if self.state.active_inventory:
                    self.state.active_inventory.pop(0) # 销账最早的
                    removed += 1
            self.logger.info("🧱 [Inventory] 移除了 %d 条多余记录", removed)

        # 最后同步视图
        self._update_fill_counters_from_inventory()
        self._save_state()
        
        return {
            "action": "reconcile",
            "detail": f"synced_inventory, final_count={len(self.state.active_inventory)}, expected={expected}",
        }

    def _btc_to_contracts(self, btc_qty: float, exchange_min_qty: float = 0.0) -> float:
        if not self.state:
            return 0.0
        if btc_qty <= 0:
            return 0.0
        contract_size = float(getattr(self.state, "contract_size", 0) or 0)
        if contract_size > 0:
            import math
            contracts = math.ceil(btc_qty / contract_size)
        else:
            contracts = btc_qty
        if exchange_min_qty:
            import math
            contracts = max(contracts, math.ceil(exchange_min_qty))
        return float(contracts)

    def compute_total_sell_qty(self, current_holdings: float) -> float:
        if not self.state:
            return 0.0
        # 当前口径为币数量
        base_locked = max(self.state.base_position_locked, 0.0)
        tradable = max(current_holdings - base_locked, 0.0)
        total_sell = tradable * self.state.sell_quota_ratio
        
        self.logger.info(
            "🧮 止盈总量计算: holdings=%.6f, locked=%.6f, tradable=%.6f, ratio=%.2f, total_sell=%.6f",
            current_holdings,
            base_locked,
            tradable,
            self.state.sell_quota_ratio,
            total_sell,
        )
        return total_sell

    def allocate_sell_targets(
        self,
        total_sell_qty: float,
        base_amount_per_grid: float,
        min_order_qty: float,
        levels_count: Optional[int] = None,
    ) -> List[float]:
        """瀑布流分配，返回每层目标数量列表（币数量）"""
        if total_sell_qty <= 0 or not self.state:
            return []
        targets: List[float] = []
        q_rem = total_sell_qty
        max_levels = levels_count if levels_count is not None else len(self.state.resistance_levels_state)
        while q_rem > 0 and len(targets) < max_levels:
            q = min(q_rem, base_amount_per_grid)
            targets.append(q)
            q_rem -= q
        if q_rem > 0 and targets:
            targets[-1] += q_rem

        # 最小订单校验：向下合并
        for i in range(len(targets) - 1, -1, -1):
            if targets[i] < min_order_qty:
                if i > 0:
                    targets[i - 1] += targets[i]
                targets[i] = 0.0
        # 总量校正：避免合并后总量不足/过量
        if targets:
            total_after = sum(targets)
            if total_after < total_sell_qty:
                targets[-1] += (total_sell_qty - total_after)
            elif total_after > total_sell_qty:
                targets[-1] = max(targets[-1] - (total_after - total_sell_qty), 0.0)
        return targets

    def build_recon_actions(
        self,
        current_price: float,
        open_orders: List[Dict],
        exchange_min_qty_btc: float,
    ) -> List[Dict[str, Any]]:
        """生成 Recon 需要执行的挂/撤单动作（数量口径=币数量）"""
        if not self.state:
            return []

        actions: List[Dict[str, Any]] = []
        # 严格匹配容差：从 0.1% 降低到 0.01%，防止相近水位互相“抢夺”订单
        price_tol = 0.0001 

        # 构建 open orders 索引（按 side + 价格分组）
        order_by_price: Dict[str, Dict[float, List[Dict]]] = {}
        for o in open_orders:
            price = float(o.get("price", 0) or 0)
            if price <= 0:
                continue
            side = o.get("side", "")
            order_by_price.setdefault(side, {}).setdefault(price, []).append(o)

        def _match_orders(side: str, price: float) -> List[Dict]:
            matches: List[Dict] = []
            for p, orders in order_by_price.get(side, {}).items():
                if abs(p - price) <= price * price_tol:
                    matches.extend(orders)
            return matches

        def _sum_open_qty(orders: List[Dict]) -> float:
            total_qty = 0.0
            for o in orders:
                qty = float(o.get("base_amount", 0) or 0)
                if qty <= 0:
                    qty = float(o.get("contracts", 0) or 0) * float(self.state.contract_size or 0)
                total_qty += qty
            return total_qty

        # 动态角色判定：基于现价上下
        all_levels = self.state.support_levels_state + self.state.resistance_levels_state
        for lvl in all_levels:
            if lvl.price < current_price:
                lvl.role = "support"
                lvl.side = "buy"
            elif lvl.price > current_price:
                lvl.role = "resistance"
                lvl.side = "sell"
            else:
                lvl.role = "neutral"

        buy_levels = [lvl for lvl in all_levels if lvl.role == "support"]
        sell_levels = [lvl for lvl in all_levels if lvl.role == "resistance"]

        for lvl in buy_levels:
            existing_orders = _match_orders("buy", lvl.price)
            if existing_orders:
                lvl.status = LevelStatus.ACTIVE
                lvl.order_id = existing_orders[0].get("id", "")
                lvl.active_order_id = lvl.order_id
                lvl.open_qty = _sum_open_qty(existing_orders)
                if int(lvl.fill_counter or 0) >= int(self.state.max_fill_per_level or 1):
                    for existing in existing_orders:
                        actions.append({
                            "action": "cancel",
                            "side": "buy",
                            "price": lvl.price,
                            "order_id": existing.get("id", ""),
                            "level_id": lvl.level_id,
                            "reason": "fill_counter_limit",
                        })
                    lvl.status = LevelStatus.CANCELING
                    lvl.last_action_ts = int(time.time())
                    continue
                target_qty = max(self.state.base_amount_per_grid, exchange_min_qty_btc)
                # 增加 5% 的数量容差，防止浮点数计算或交易所微小差异导致的频繁撤单 (rebalance_qty)
                diff = abs(lvl.open_qty - target_qty)
                is_diff_significant = diff >= exchange_min_qty_btc and (diff / target_qty > 0.05 if target_qty > 0 else True)
                
                if is_diff_significant:
                    for existing in existing_orders:
                        actions.append({
                            "action": "cancel",
                            "side": "buy",
                            "price": lvl.price,
                            "order_id": existing.get("id", ""),
                            "level_id": lvl.level_id,
                            "reason": "rebalance_qty",
                        })
                    lvl.status = LevelStatus.CANCELING
                    lvl.last_action_ts = int(time.time())
                continue
            # 如果角色切换为 support 但存在卖单，先撤卖单
            existing_sells = _match_orders("sell", lvl.price)
            if existing_sells:
                for existing_sell in existing_sells:
                    actions.append({
                        "action": "cancel",
                        "side": "sell",
                        "price": lvl.price,
                        "order_id": existing_sell.get("id", ""),
                        "level_id": lvl.level_id,
                        "reason": "polarity_flip_cancel_sell",
                    })
                lvl.status = LevelStatus.CANCELING
                lvl.last_action_ts = int(time.time())
                continue
            # 实盘无单但状态为 ACTIVE，纠正为 IDLE
            if lvl.status == LevelStatus.ACTIVE:
                lvl.status = LevelStatus.IDLE
                lvl.order_id = ""
                lvl.open_qty = 0.0

            # 状态回收
            if lvl.status in (LevelStatus.PLACING, LevelStatus.CANCELING) and lvl.last_action_ts:
                if time.time() - (lvl.last_action_ts or 0) > self.state.order_action_timeout_sec:
                    lvl.status = LevelStatus.IDLE
                    lvl.last_error = "action_timeout"

            if lvl.status == LevelStatus.IDLE:
                if lvl.fill_counter >= self.state.max_fill_per_level:
                    self.logger.debug(
                        f"🧱 填充上限: price={lvl.price:.2f}, fill_counter={lvl.fill_counter}, "
                        f"max={self.state.max_fill_per_level}"
                    )
                elif current_price > lvl.price * (1 + self.state.buy_price_buffer_pct):
                    qty = max(self.state.base_amount_per_grid, exchange_min_qty_btc)
                    actions.append({
                        "action": "place",
                        "side": "buy",
                        "price": lvl.price,
                        "qty": qty,
                        "level_id": lvl.level_id,
                        "reason": "recon_buy_sync",
                    })
                    lvl.status = LevelStatus.PLACING
                    lvl.target_qty = qty
                    lvl.last_action_ts = int(time.time())
                    self.logger.debug(
                        f"🧾 Recon补买: price={lvl.price:.2f}, qty={qty:.6f}"
                    )
            # 僵尸状态回收
            elif lvl.status in (LevelStatus.PLACING, LevelStatus.CANCELING):
                if lvl.last_action_ts and (time.time() - lvl.last_action_ts) > self.state.order_action_timeout_sec:
                    lvl.status = LevelStatus.IDLE
                    lvl.last_error = "action_timeout"

        # ============================================
        # 孤儿买单清理：撤销不在当前水位列表中的买单
        # ============================================
        buy_level_prices = {lvl.price for lvl in buy_levels}
        
        for order_price, orders in order_by_price.get("buy", {}).items():
            # 检查该价格是否匹配任何支撑位
            is_matched = any(
                abs(order_price - lvl_price) <= lvl_price * price_tol
                for lvl_price in buy_level_prices
            )
            
            if not is_matched:
                # 孤儿订单：不在任何支撑位，需要撤销
                for orphan_order in orders:
                    actions.append({
                        "action": "cancel",
                        "side": "buy",
                        "price": order_price,
                        "order_id": orphan_order.get("id", ""),
                        "level_id": 0,  # 无对应水位
                        "reason": "orphan_order_cleanup",
                    })
                    self.logger.warning(
                        f"🧹 [Recon] 孤儿买单撤销: price={order_price:.2f}, "
                        f"order_id={orphan_order.get('id', '')}"
                    )

        # ============================================
        # T3.3: 使用逐级邻位映射同步卖单
        # ============================================
        # 旧逻辑（已移除）：基于 avg_entry_price 的 min_profit_guard 和 allocate_sell_targets
        # 新逻辑：基于 fill_counter 和 level_mapping 的逐级对冲
        
        sell_actions = self.sync_mapping(
            current_price=current_price,
            open_orders=open_orders,
            exchange_min_qty=exchange_min_qty_btc,
        )
        actions.extend(sell_actions)
        
        # ============================================
        # 孤儿卖单清理：撤销不在当前水位列表中的卖单
        # ============================================
        all_level_prices = {lvl.price for lvl in all_levels}
        
        for order_price, orders in order_by_price.get("sell", {}).items():
            # 检查该价格是否匹配任何水位
            is_matched = any(
                abs(order_price - lvl_price) <= lvl_price * price_tol
                for lvl_price in all_level_prices
            )
            
            if not is_matched:
                # 孤儿订单：不在任何水位，需要撤销
                for orphan_order in orders:
                    actions.append({
                        "action": "cancel",
                        "side": "sell",
                        "price": order_price,
                        "order_id": orphan_order.get("id", ""),
                        "level_id": 0,
                        "reason": "orphan_order_cleanup",
                    })
                    self.logger.warning(
                        f"🧹 [Recon] 孤儿卖单撤销: price={order_price:.2f}, "
                        f"order_id={orphan_order.get('id', '')}"
                    )
        
        # 统计
        buy_actions_count = len([a for a in actions if a.get('side') == 'buy'])
        sell_actions_count = len([a for a in actions if a.get('side') == 'sell'])
        orphan_cleanup_count = len([a for a in actions if a.get('reason') == 'orphan_order_cleanup'])
        
        self.logger.info(
            f"📊 [Recon] 买单动作: {buy_actions_count}, 卖单动作: {sell_actions_count}, "
            f"孤儿清理: {orphan_cleanup_count}"
        )

        return actions

    def build_event_sell_increment(
        self,
        delta_buy_qty: float,
        exchange_min_qty_btc: float,
        current_price: float,
        filled_support_level_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        T4.1: 买单成交后，基于逐级邻位映射增量补卖单
        
        Args:
            delta_buy_qty: 买入数量
            exchange_min_qty_btc: 交易所最小下单量
            current_price: 当前价格
            filled_support_level_id: 成交的支撑位 ID（可选，用于精确映射）
        
        Returns:
            卖单动作列表
        """
        if not self.state or delta_buy_qty <= 0:
            return []
        
        delta_sell = delta_buy_qty * self.state.sell_quota_ratio
        if delta_sell < exchange_min_qty_btc:
            self.logger.warning(
                f"⚠️ 最小卖单量不足: delta_sell={delta_sell:.6f}, "
                f"min={exchange_min_qty_btc:.6f}"
            )
            return []

        # ============================================
        # T3.4 & T4.1: 基于逐级邻位映射确定卖单目标
        # 不再使用 avg_entry_price
        # ============================================
        
        # 1. 确定目标卖单水位
        target_level = None
        
        if filled_support_level_id:
            # 有明确的支撑位 ID，使用映射查找
            target_level_id = self.state.level_mapping.get(filled_support_level_id)
            if target_level_id:
                target_level = self._get_level_by_id(target_level_id)
                if target_level:
                    self.logger.debug(
                        f"⚡ [Event] 使用邻位映射: S_{filled_support_level_id} → "
                        f"L_{target_level_id}({target_level.price:.2f})"
                    )
        
        if not target_level:
            # 回退：查找最近成交支撑位的映射
            recent_fill = None
            for lvl in sorted(self.state.support_levels_state, key=lambda x: x.price, reverse=True):
                if lvl.fill_counter > 0 and lvl.price < current_price:
                    recent_fill = lvl
                    break
            
            if recent_fill:
                target_level_id = self.state.level_mapping.get(recent_fill.level_id)
                if target_level_id:
                    target_level = self._get_level_by_id(target_level_id)
                    if target_level:
                        self.logger.debug(
                            f"⚡ [Event] 回退映射: S_{recent_fill.level_id}({recent_fill.price:.2f}) → "
                            f"L_{target_level_id}({target_level.price:.2f})"
                        )
        
        if not target_level:
            # 再次回退：找当前价上方最近的水位
            all_levels = self.state.support_levels_state + self.state.resistance_levels_state
            candidates = [lvl for lvl in all_levels if lvl.price > current_price]
            if candidates:
                target_level = min(candidates, key=lambda x: x.price)
                self.logger.warning(
                    f"⚠️ [Event] 无映射可用，使用最近上方水位: {target_level.price:.2f}"
                )
        
        if not target_level:
            self.logger.warning(
                f"⚠️ 无可用卖单水位(Event): delta_sell={delta_sell:.6f}, current={current_price:.2f}"
            )
            return []
        
        # 2. 检查价格缓冲（避免在太近的价位挂单）
        if current_price >= target_level.price * (1 - self.state.sell_price_buffer_pct):
            self.logger.warning(
                f"⚠️ 卖单水位太近: current={current_price:.2f}, "
                f"target={target_level.price:.2f}, buffer={self.state.sell_price_buffer_pct}"
            )
            return []
        
        # 3. 生成卖单动作
        self.logger.info(
            f"⚡ [Event] 补卖单: price={target_level.price:.2f}, qty={delta_sell:.6f}, "
            f"level_id={target_level.level_id}"
        )
        return [{
            "action": "place",
            "side": "sell",
            "price": target_level.price,
            "qty": delta_sell,
            "level_id": target_level.level_id,
            "reason": "event_sell_mapping",
        }]

    def _find_support_level_for_price(self, price: float) -> Optional[GridLevelState]:
        if not self.state:
            return None
        price = float(price or 0)
        if price <= 0:
            return None
        price_tol = 0.001
        for lvl in self.state.support_levels_state:
            if abs(lvl.price - price) <= lvl.price * price_tol:
                return lvl
        # 若未找到完全匹配，选择最接近的下方支撑位
        candidates = [lvl for lvl in self.state.support_levels_state if lvl.price < price]
        if not candidates:
            return None
        return max(candidates, key=lambda x: x.price)

    def increment_fill_counter_by_order(self, order_id: str, buy_price: float, buy_qty: float) -> bool:
        if not self.state:
            return False
        order_id = str(order_id or "").strip()
        if not order_id:
            return False
        
        # 1. 查找匹配的水位
        matched_lvl = None
        for lvl in self.state.support_levels_state:
            if lvl.order_id == order_id or lvl.active_order_id == order_id:
                matched_lvl = lvl
                break
        
        # 如果订单ID没匹配上，按价格找最近的水位
        if not matched_lvl:
            matched_lvl = self._find_support_level_for_price(buy_price)
            
        if not matched_lvl:
            self.logger.warning("无法为成交订单匹配到水位: id=%s, price=%.2f", order_id, buy_price)
            return False

        # 2. 入库清单 (Active Inventory)
        new_fill = ActiveFill(
            order_id=order_id,
            price=buy_price,
            qty=buy_qty,
            level_id=matched_lvl.level_id,
            timestamp=int(time.time())
        )
        self.state.active_inventory.append(new_fill)
        
        # 3. 更新水位计数器 (View)
        self._update_fill_counters_from_inventory()
        
        self.logger.info(
            "🧱 [Inventory] 记录新持仓: level=%d, price=%.2f, qty=%.6f, order_id=%s",
            matched_lvl.level_id, buy_price, buy_qty, order_id
        )
        self._save_state()
        return True

    def _update_fill_counters_from_inventory(self) -> None:
        """从清单同步计数器视图"""
        if not self.state:
            return
            
        # 先清零
        for lvl in self.state.support_levels_state:
            lvl.fill_counter = 0
            
        # 重新统计
        for fill in self.state.active_inventory:
            for lvl in self.state.support_levels_state:
                if lvl.level_id == fill.level_id:
                    lvl.fill_counter += 1
                    break

    def release_fill_counter_by_qty(self, sell_qty: float) -> None:
        if not self.state or not self.state.active_inventory:
            return
            
        base_qty = float(self.state.base_amount_per_grid or 0)
        if base_qty <= 0:
            return
            
        sell_qty = max(float(sell_qty or 0), 0.0)
        # 计算需要销账的次数 (通常是 1)
        count = int(round(sell_qty / base_qty))
        if count <= 0:
            count = 1
            
        # FIFO 销账：优先销掉最早的买入记录
        # 也可以改为价格优先：销掉利润最高的那笔（最高价卖单销掉最低价买单）
        # 这里采用网格物理逻辑：卖出意味着某个水位的买入被释放，由于止盈通常是针对特定的买入，
        # 我们按 FIFO 释放，并重新计算计数器
        
        removed_count = 0
        for _ in range(count):
            if self.state.active_inventory:
                removed = self.state.active_inventory.pop(0) # FIFO
                removed_count += 1
                
                # 记录到已平仓清单 (保留最近 10 条)
                self.state.settled_inventory.insert(0, removed)
                if len(self.state.settled_inventory) > 10:
                    self.state.settled_inventory = self.state.settled_inventory[:10]
                
                self.logger.info(
                    "🧱 [Inventory] 销账已平仓持仓: level=%d, buy_price=%.2f, order_id=%s",
                    removed.level_id, removed.price, removed.order_id
                )
        
        if removed_count > 0:
            self._update_fill_counters_from_inventory()
            self._save_state()
    
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
                support_levels_state=[
                    GridLevelState.from_dict(s) for s in grid_data.get("support_levels_state", [])
                ],
                resistance_levels_state=[
                    GridLevelState.from_dict(r) for r in grid_data.get("resistance_levels_state", [])
                ],
                active_inventory=[
                    ActiveFill.from_dict(f) for f in grid_data.get("active_inventory", [])
                ],
                settled_inventory=[
                    ActiveFill.from_dict(f) for f in grid_data.get("settled_inventory", [])
                ],
                # T1.3: 恢复邻位映射表（兼容旧版：默认空字典）
                level_mapping=grid_data.get("level_mapping", {}),
                # 恢复网格配置
                per_grid_contracts=grid_data.get("per_grid_contracts", 0),
                contract_size=grid_data.get("contract_size", 0.0001),
                num_grids=grid_data.get("num_grids", 0),
                sell_quota_ratio=grid_data.get("sell_quota_ratio", self.grid_config.sell_quota_ratio),
                min_profit_pct=grid_data.get("min_profit_pct", self.grid_config.min_profit_pct),
                buy_price_buffer_pct=grid_data.get(
                    "buy_price_buffer_pct",
                    self.grid_config.buy_price_buffer_pct,
                ),
                sell_price_buffer_pct=grid_data.get(
                    "sell_price_buffer_pct",
                    self.grid_config.sell_price_buffer_pct,
                ),
                base_amount_per_grid=grid_data.get("base_amount_per_grid", self.grid_config.base_amount_per_grid),
                base_position_locked=grid_data.get("base_position_locked", self.grid_config.base_position_locked),
                max_fill_per_level=int(grid_data.get("max_fill_per_level", self.grid_config.max_fill_per_level) or 1),
                recon_interval_sec=grid_data.get("recon_interval_sec", self.grid_config.recon_interval_sec),
                order_action_timeout_sec=grid_data.get("order_action_timeout_sec", self.grid_config.order_action_timeout_sec),
                # 恢复锚点
                anchor_price=grid_data.get("anchor_price", 0.0),
                anchor_ts=grid_data.get("anchor_ts", 0),
                # 持仓
                total_position_usdt=grid_data.get("total_position_usdt", 0.0),
                avg_entry_price=grid_data.get("avg_entry_price", 0.0),
                unrealized_pnl=grid_data.get("unrealized_pnl", 0.0),
                total_position_contracts=grid_data.get("total_position_contracts", 0.0),
                resistance_levels=grid_data.get("resistance_levels", []),
                support_levels=grid_data.get("support_levels", []),
            )

            # 使用当前配置覆盖关键网格参数，避免旧状态导致数量不一致
            if restored_state.base_amount_per_grid != self.grid_config.base_amount_per_grid:
                self.logger.info(
                    f"📊 覆盖 base_amount_per_grid: {restored_state.base_amount_per_grid} -> "
                    f"{self.grid_config.base_amount_per_grid}"
                )
                restored_state.base_amount_per_grid = self.grid_config.base_amount_per_grid
            if self.grid_config.base_position_locked > 0 and restored_state.base_position_locked != self.grid_config.base_position_locked:
                self.logger.info(
                    f"📊 覆盖 base_position_locked: {restored_state.base_position_locked} -> "
                    f"{self.grid_config.base_position_locked}"
                )
                restored_state.base_position_locked = self.grid_config.base_position_locked
            if restored_state.max_fill_per_level != self.grid_config.max_fill_per_level:
                self.logger.info(
                    f"📊 覆盖 max_fill_per_level: {restored_state.max_fill_per_level} -> "
                    f"{self.grid_config.max_fill_per_level}"
                )
                restored_state.max_fill_per_level = self.grid_config.max_fill_per_level
            
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
            
            # T2.3: 规范化 level_id 并重建映射（兼容旧版状态文件）
            self._normalize_level_ids_and_rebuild_mapping()
            
            self._save_state()
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
        from key_level_grid.resistance import ResistanceCalculator, ResistanceConfig as CalcResistanceConfig
        if not hasattr(self, '_resistance_calc'):
            # 将位置管理器的 resistance_config 转换为计算器的配置
            calc_config = CalcResistanceConfig(
                swing_lookbacks=self.resistance_config.swing_lookbacks,
                fib_ratios=self.resistance_config.fib_ratios,
                merge_tolerance=self.resistance_config.merge_tolerance,
                min_distance_pct=self.resistance_config.min_distance_pct,
                max_distance_pct=self.resistance_config.max_distance_pct,
            )
            self._resistance_calc = ResistanceCalculator(calc_config)
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
