"""
网格状态模块 (LEVEL_GENERATION.md v3.1.0)

包含网格水位状态、网格整体状态等

V3.0 新增:
- GridLevelState: 添加 score, qty_multiplier, original_price 字段
- GridState: 添加 rebuild_logs, last_rebuild_ts, last_score_refresh_ts 字段
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

from key_level_grid.core.types import LevelStatus, LevelLifecycleStatus

# 延迟导入避免循环依赖
if TYPE_CHECKING:
    from key_level_grid.core.scoring import LevelScore
    from key_level_grid.core.triggers import RebuildLog


# 状态版本（用于迁移）
STATE_VERSION = 3  # V3.0: 新增评分和重构日志字段


@dataclass
class GridLevelState:
    """
    网格水位状态 (LEVEL_GENERATION.md v3.1.0)
    
    支持两种状态维度:
    - status: 订单操作状态 (IDLE/PLACING/ACTIVE/FILLED/CANCELING)
    - lifecycle_status: 生命周期状态 (ACTIVE/RETIRED/DEAD)
    
    V3.0 新增:
    - score: 水位评分详情
    - qty_multiplier: 仓位系数 (1.0/1.2/1.5)
    - original_price: 吸附前原始价格
    """
    level_id: int
    price: float
    side: str  # buy | sell
    role: str = "support"  # support | resistance
    
    # 订单操作状态
    status: LevelStatus = LevelStatus.IDLE
    
    # 生命周期状态
    lifecycle_status: LevelLifecycleStatus = LevelLifecycleStatus.ACTIVE
    
    # 订单相关
    active_order_id: str = ""
    order_id: str = ""
    target_qty: float = 0.0          # 目标数量（合约张数）
    open_qty: float = 0.0            # 实际挂单数量（合约张数）
    filled_qty: float = 0.0          # 已成交数量（合约张数）
    fill_counter: int = 0            # 水位补买计数
    last_action_ts: int = 0
    last_error: str = ""
    
    # 继承追踪
    inherited_from_index: Optional[int] = None  # 继承自旧数组的哪个索引
    inheritance_ts: Optional[int] = None        # 继承时间戳
    
    # 🆕 V3.0 评分字段
    score: Optional[dict] = None      # 评分详情 (LevelScore.to_dict())
    qty_multiplier: float = 1.0       # 仓位系数: 1.0 (基准) / 1.2 (强) / 1.5 (超强)
    original_price: Optional[float] = None  # 吸附前原始价格 (心理位对齐后会改变 price)

    def to_dict(self) -> dict:
        return {
            "level_id": self.level_id,
            "price": self.price,
            "side": self.side,
            "role": self.role,
            "status": self.status.value if isinstance(self.status, LevelStatus) else str(self.status),
            "lifecycle_status": self.lifecycle_status.value if isinstance(self.lifecycle_status, LevelLifecycleStatus) else str(self.lifecycle_status),
            "active_order_id": self.active_order_id,
            "order_id": self.order_id,
            "target_qty": self.target_qty,
            "open_qty": self.open_qty,
            "filled_qty": self.filled_qty,
            "fill_counter": self.fill_counter,
            "last_action_ts": self.last_action_ts,
            "last_error": self.last_error,
            "inherited_from_index": self.inherited_from_index,
            "inheritance_ts": self.inheritance_ts,
            # V3.0 新增
            "score": self.score,
            "qty_multiplier": self.qty_multiplier,
            "original_price": self.original_price,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GridLevelState":
        # 订单状态
        status = data.get("status", LevelStatus.IDLE)
        try:
            status = LevelStatus(status)
        except Exception:
            status = LevelStatus.IDLE
        
        # 生命周期状态（向后兼容：旧版数据默认 ACTIVE）
        lifecycle_status = data.get("lifecycle_status", "ACTIVE")
        try:
            lifecycle_status = LevelLifecycleStatus(lifecycle_status)
        except Exception:
            lifecycle_status = LevelLifecycleStatus.ACTIVE
        
        return cls(
            level_id=int(data.get("level_id", 0)),
            price=float(data.get("price", 0)),
            side=data.get("side", "buy"),
            role=data.get("role", "support" if data.get("side") == "buy" else "resistance"),
            status=status,
            lifecycle_status=lifecycle_status,
            active_order_id=data.get("active_order_id", ""),
            order_id=data.get("order_id", ""),
            target_qty=float(data.get("target_qty", 0) or 0),
            open_qty=float(data.get("open_qty", 0) or 0),
            filled_qty=float(data.get("filled_qty", 0) or 0),
            fill_counter=int(data.get("fill_counter", 0) or 0),
            last_action_ts=int(data.get("last_action_ts", 0) or 0),
            last_error=data.get("last_error", ""),
            inherited_from_index=data.get("inherited_from_index"),
            inheritance_ts=data.get("inheritance_ts"),
            # V3.0 新增（向后兼容：旧版数据默认值）
            score=data.get("score"),
            qty_multiplier=float(data.get("qty_multiplier", 1.0) or 1.0),
            original_price=data.get("original_price"),
        )
    
    def is_active(self) -> bool:
        """是否为活跃水位"""
        return self.lifecycle_status == LevelLifecycleStatus.ACTIVE
    
    def is_retired(self) -> bool:
        """是否为退役水位"""
        return self.lifecycle_status == LevelLifecycleStatus.RETIRED
    
    def can_place_buy(self) -> bool:
        """是否允许挂买单（退役水位禁止买入）"""
        return self.lifecycle_status == LevelLifecycleStatus.ACTIVE
    
    def get_final_score(self) -> float:
        """获取最终评分（若无评分则返回 50 作为默认）"""
        if self.score and isinstance(self.score, dict):
            return float(self.score.get("final_score", 50))
        return 50.0
    
    def set_score(self, score: "LevelScore") -> None:
        """设置评分（存储为 dict）"""
        self.score = score.to_dict() if hasattr(score, "to_dict") else score
        if hasattr(score, "final_score"):
            # 根据评分计算仓位系数
            final = score.final_score
            if final >= 100:
                self.qty_multiplier = 1.5  # MTF 共振级
            elif final >= 60:
                self.qty_multiplier = 1.2  # 强支撑
            elif final >= 30:
                self.qty_multiplier = 1.0  # 基准
            else:
                self.qty_multiplier = 0.0  # 不开仓


@dataclass
class GridOrder:
    """网格订单"""
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
    """
    正在持仓中的买入成交记录 (SELL_MAPPING.md Section 7.2)
    
    设计原则：
    - 只保留不可变的买入事实 + 水位索引归属
    - 卖单状态不持久化，每次 Recon 动态计算
    
    V3.1 变更：
    - level_id → level_index（索引归属原则）
    - 移除 target_sell_level_id, sell_order_id, sell_qty（不持久化）
    """
    order_id: str       # 买入订单 ID（唯一标识，用于校验有效性）
    price: float        # 实际成交价格（非水位价格，保留滑点信息）
    qty: float          # 实际成交数量
    timestamp: int      # 成交时间戳
    level_index: int    # 归属的支撑位索引（0=支撑位1, 1=支撑位2...）
                        # 📌 网格重建后索引不变，自动对应新水位
                        # 📌 若索引越界，运行时兜底到最后一个水位

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "price": self.price,
            "qty": self.qty,
            "level_index": self.level_index,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActiveFill":
        # 向后兼容：旧版 level_id → 新版 level_index
        level_index = data.get("level_index")
        if level_index is None:
            # 旧格式：尝试从 level_id 推断索引（假设 level_id 从 1 开始）
            old_level_id = data.get("level_id", 0)
            level_index = max(0, old_level_id - 1) if old_level_id > 0 else 0
        
        return cls(
            order_id=data.get("order_id", ""),
            price=float(data.get("price", 0)),
            qty=float(data.get("qty", 0)),
            timestamp=int(data.get("timestamp", 0)),
            level_index=int(level_index),
        )


@dataclass
class GridState:
    """
    网格状态 (LEVEL_GENERATION.md v3.1.0)
    
    V3.0 新增:
    - rebuild_logs: 重构日志列表
    - last_rebuild_ts: 上次重构时间戳
    - last_score_refresh_ts: 上次评分刷新时间戳
    """
    symbol: str
    direction: str = "long"           # 只做多
    
    # 状态版本
    state_version: int = STATE_VERSION
    
    # 网格区间
    upper_price: float = 0.0          # 上边界 (阻力位)
    lower_price: float = 0.0          # 下边界 (支撑位)
    grid_floor: float = 0.0           # 网格底线 (止损线)
    
    # 网格订单（旧结构，保留兼容）
    buy_orders: List[GridOrder] = field(default_factory=list)
    sell_orders: List[GridOrder] = field(default_factory=list)

    # 水位状态机（活跃水位，按价格降序排列）
    support_levels_state: List[GridLevelState] = field(default_factory=list)
    resistance_levels_state: List[GridLevelState] = field(default_factory=list)
    
    # 退役水位（等待清仓）
    retired_levels: List[GridLevelState] = field(default_factory=list)
    
    # 精确仓位清单
    active_inventory: List[ActiveFill] = field(default_factory=list)
    settled_inventory: List[ActiveFill] = field(default_factory=list)
    
    # 逐级邻位映射表 {support_level_id: adjacent_sell_level_id}
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
    base_amount_per_grid: float = 1.0
    base_position_locked: float = 0.0
    max_fill_per_level: int = 1
    recon_interval_sec: int = 30
    order_action_timeout_sec: int = 10

    # 网格锚点
    anchor_price: float = 0.0
    anchor_ts: int = 0
    
    # 持仓
    total_position_usdt: float = 0.0
    avg_entry_price: float = 0.0
    unrealized_pnl: float = 0.0
    total_position_contracts: float = 0.0
    
    # 兼容属性
    resistance_levels: List = field(default_factory=list)
    support_levels: List = field(default_factory=list)
    
    # 🆕 V3.0 重构日志
    rebuild_logs: List[dict] = field(default_factory=list)  # List[RebuildLog.to_dict()]
    last_rebuild_ts: int = 0           # 上次重构时间戳 (秒)
    last_score_refresh_ts: int = 0     # 上次评分刷新时间戳 (秒)
    
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
        return None
    
    @property
    def take_profit_plan(self):
        """兼容: 返回止盈计划"""
        return None
    
    @property
    def batches(self) -> List:
        """兼容: 返回空列表"""
        return []
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "state_version": self.state_version,
            "upper_price": self.upper_price,
            "lower_price": self.lower_price,
            "grid_floor": self.grid_floor,
            "buy_orders": [o.to_dict() for o in self.buy_orders],
            "sell_orders": [o.to_dict() for o in self.sell_orders],
            "support_levels_state": [s.to_dict() for s in self.support_levels_state],
            "resistance_levels_state": [r.to_dict() for r in self.resistance_levels_state],
            "retired_levels": [r.to_dict() for r in self.retired_levels],
            "active_inventory": [f.to_dict() for f in self.active_inventory],
            "settled_inventory": [f.to_dict() for f in self.settled_inventory],
            "level_mapping": self.level_mapping,
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
            "total_position_usdt": self.total_position_usdt,
            "avg_entry_price": self.avg_entry_price,
            "unrealized_pnl": self.unrealized_pnl,
            "total_position_contracts": self.total_position_contracts,
            "resistance_levels": self.resistance_levels,
            "support_levels": self.support_levels,
            # V3.0 新增
            "rebuild_logs": self.rebuild_logs,
            "last_rebuild_ts": self.last_rebuild_ts,
            "last_score_refresh_ts": self.last_score_refresh_ts,
        }
    
    def add_rebuild_log(self, log: "RebuildLog") -> None:
        """
        添加重构日志
        
        自动保留最近 100 条记录
        """
        log_dict = log.to_dict() if hasattr(log, "to_dict") else log
        self.rebuild_logs.append(log_dict)
        
        # 限制日志数量
        max_logs = 100
        if len(self.rebuild_logs) > max_logs:
            self.rebuild_logs = self.rebuild_logs[-max_logs:]