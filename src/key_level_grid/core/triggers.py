"""
触发器与日志数据结构 (LEVEL_GENERATION.md v3.2.5)

包含:
- RebuildTrigger: 重构触发原因枚举
- RebuildPhase: 重构阶段枚举
- RebuildLog: 重构日志
- ManualBoundary: 手动边界设置
- PendingMigration: 原子性重构事务日志
- KlineSyncStatus: K 线同步状态
- ATRConfig: ATR 空间硬约束配置 (V3.2.5)
- TimeframeConfig: 时间框架层级配置 (V3.2.5)
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class RebuildTrigger(str, Enum):
    """
    重构触发原因
    
    用于记录每次网格重构的触发条件，
    便于后续分析和参数调优。
    """
    ANCHOR_DRIFT = "ANCHOR_DRIFT"       # 锚点偏移 > 3%
    BOUNDARY_ALERT = "BOUNDARY_ALERT"   # 覆盖告急 (现价距边界 ≤ 1 格)
    DAILY_REFRESH = "DAILY_REFRESH"     # 每日定时刷新 (UTC 0:00)
    MANUAL_REBUILD = "MANUAL_REBUILD"   # 手动触发
    COLD_START = "COLD_START"           # 冷启动 (系统重启)
    CONFIG_CHANGE = "CONFIG_CHANGE"     # 配置变更


class RebuildPhase(str, Enum):
    """
    原子性重构阶段
    
    用于跟踪重构执行状态，支持崩溃恢复。
    """
    PENDING = "PENDING"           # 准备中 (已计算迁移计划)
    CANCELLING = "CANCELLING"     # 撤单中
    PLACING = "PLACING"           # 挂单中
    SYNCING = "SYNCING"           # 状态同步中
    COMPLETED = "COMPLETED"       # 完成
    ALARM = "ALARM"               # 告警模式 (撤单失败)
    RETRY = "RETRY"               # 重试模式 (挂单部分失败)


@dataclass
class RebuildLog:
    """
    重构日志
    
    记录每次网格重构的详细信息，
    用于诊断和优化重构触发阈值。
    """
    timestamp: int                       # 时间戳 (秒)
    trigger: RebuildTrigger              # 触发原因
    anchor_before: float                 # 重构前锚点价格
    anchor_after: float                  # 重构后锚点价格
    drift_pct: float                     # 锚点偏移百分比
    levels_before: int                   # 重构前水位数
    levels_after: int                    # 重构后水位数
    orders_cancelled: int                # 撤销订单数
    orders_placed: int                   # 新挂订单数
    detail: Optional[str] = None         # 额外说明
    
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "trigger": self.trigger.value if isinstance(self.trigger, RebuildTrigger) else str(self.trigger),
            "anchor_before": self.anchor_before,
            "anchor_after": self.anchor_after,
            "drift_pct": self.drift_pct,
            "levels_before": self.levels_before,
            "levels_after": self.levels_after,
            "orders_cancelled": self.orders_cancelled,
            "orders_placed": self.orders_placed,
            "detail": self.detail,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "RebuildLog":
        trigger = data.get("trigger", "COLD_START")
        try:
            trigger = RebuildTrigger(trigger)
        except (ValueError, TypeError):
            trigger = RebuildTrigger.COLD_START
        
        return cls(
            timestamp=int(data.get("timestamp", 0)),
            trigger=trigger,
            anchor_before=float(data.get("anchor_before", 0)),
            anchor_after=float(data.get("anchor_after", 0)),
            drift_pct=float(data.get("drift_pct", 0)),
            levels_before=int(data.get("levels_before", 0)),
            levels_after=int(data.get("levels_after", 0)),
            orders_cancelled=int(data.get("orders_cancelled", 0)),
            orders_placed=int(data.get("orders_placed", 0)),
            detail=data.get("detail"),
        )


@dataclass
class ManualBoundary:
    """
    手动边界设置
    
    当自动分形提取的网格覆盖范围不足时，
    可以手动设置上下边界来扩展网格区间。
    
    模式说明:
    - strict: 严格过滤，超出边界的水位直接丢弃
    - filter: 过滤模式，超出边界的水位保留但降低评分
    - expand: 扩展模式，自动边界与手动边界取并集
    """
    enabled: bool = False                # 是否启用手动边界
    upper_price: Optional[float] = None  # 手动上边界 (阻力位)
    lower_price: Optional[float] = None  # 手动下边界 (支撑位)
    mode: str = "strict"                 # 模式: strict/filter/expand
    buffer_pct: float = 0.0              # 边界缓冲百分比
    
    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "upper_price": self.upper_price,
            "lower_price": self.lower_price,
            "mode": self.mode,
            "buffer_pct": self.buffer_pct,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ManualBoundary":
        return cls(
            enabled=bool(data.get("enabled", False)),
            upper_price=data.get("upper_price"),
            lower_price=data.get("lower_price"),
            mode=data.get("mode", "strict"),
            buffer_pct=float(data.get("buffer_pct", 0.0)),
        )
    
    def filter_levels(self, levels: List[float]) -> List[float]:
        """
        根据边界过滤水位
        
        Args:
            levels: 水位列表
        
        Returns:
            过滤后的水位列表
        """
        if not self.enabled:
            return levels
        
        result = []
        
        # 计算实际边界 (含缓冲区)
        effective_upper = self.upper_price * (1 + self.buffer_pct) if self.upper_price else None
        effective_lower = self.lower_price * (1 - self.buffer_pct) if self.lower_price else None
        
        for price in levels:
            # 检查上边界
            if effective_upper is not None and price > effective_upper:
                if self.mode == "strict":
                    continue  # 丢弃
                # filter/expand 模式保留
            
            # 检查下边界
            if effective_lower is not None and price < effective_lower:
                if self.mode == "strict":
                    continue  # 丢弃
                # filter/expand 模式保留
            
            result.append(price)
        
        return result
    
    def apply(self, auto_levels: List[float]) -> List[float]:
        """
        应用手动边界
        
        规则:
        1. strict 模式: 只保留边界内的水位
        2. filter 模式: 保留所有水位，边界外的降低优先级
        3. expand 模式: 自动边界与手动边界取并集
        
        Args:
            auto_levels: 自动计算的水位列表 (降序排列)
        
        Returns:
            应用边界后的水位列表 (降序排列)
        """
        if not self.enabled:
            return auto_levels
        
        # 先过滤
        if self.mode == "strict":
            result = self.filter_levels(auto_levels)
        else:
            result = list(auto_levels)
        
        # expand 模式: 确保边界点存在
        if self.mode == "expand":
            if self.upper_price is not None:
                if not result or result[0] < self.upper_price:
                    result.insert(0, self.upper_price)
            
            if self.lower_price is not None:
                if not result or result[-1] > self.lower_price:
                    result.append(self.lower_price)
        
        # 去重并保持降序
        return sorted(set(result), reverse=True)


@dataclass
class PendingMigration:
    """
    原子性重构事务日志
    
    持久化到 state_migration.json，用于:
    1. 跟踪重构执行进度
    2. 崩溃后恢复
    3. 审计与调试
    
    关键原则: 撤单失败绝对不能进行新挂单
    """
    phase: RebuildPhase                  # 当前阶段
    started_at: int                      # 开始时间戳 (秒)
    orders_to_cancel: List[str] = field(default_factory=list)    # 待撤订单 ID
    orders_cancelled: List[str] = field(default_factory=list)    # 已撤订单 ID
    orders_to_place: List[Dict[str, Any]] = field(default_factory=list)  # 待挂订单
    orders_placed: List[str] = field(default_factory=list)       # 已挂订单 ID
    failed_orders: List[Dict[str, Any]] = field(default_factory=list)    # 失败订单
    error_message: Optional[str] = None  # 错误信息
    retry_count: int = 0                 # 重试次数
    
    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value if isinstance(self.phase, RebuildPhase) else str(self.phase),
            "started_at": self.started_at,
            "orders_to_cancel": self.orders_to_cancel,
            "orders_cancelled": self.orders_cancelled,
            "orders_to_place": self.orders_to_place,
            "orders_placed": self.orders_placed,
            "failed_orders": self.failed_orders,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PendingMigration":
        phase = data.get("phase", "PENDING")
        try:
            phase = RebuildPhase(phase)
        except (ValueError, TypeError):
            phase = RebuildPhase.PENDING
        
        return cls(
            phase=phase,
            started_at=int(data.get("started_at", 0)),
            orders_to_cancel=data.get("orders_to_cancel", []),
            orders_cancelled=data.get("orders_cancelled", []),
            orders_to_place=data.get("orders_to_place", []),
            orders_placed=data.get("orders_placed", []),
            failed_orders=data.get("failed_orders", []),
            error_message=data.get("error_message"),
            retry_count=int(data.get("retry_count", 0)),
        )
    
    def is_incomplete(self) -> bool:
        """检查是否有未完成的迁移"""
        return self.phase not in [RebuildPhase.COMPLETED]
    
    def needs_intervention(self) -> bool:
        """检查是否需要人工介入"""
        return self.phase == RebuildPhase.ALARM


@dataclass
class KlineSyncStatus:
    """
    K 线同步状态
    
    用于检测不同时间框架的 K 线数据是否在同一时间逻辑轴上，
    防止计算出基于旧趋势和新结构的错误共振。
    """
    timeframe: str                       # 时间框架 "1d" | "4h" | "15m"
    last_close_time: int                 # 最新闭合 K 线的 close_time (ms)
    expected_close_time: int             # 预期的 close_time (ms)
    is_stale: bool                       # 是否过期
    lag_seconds: int                     # 延迟秒数
    
    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "last_close_time": self.last_close_time,
            "expected_close_time": self.expected_close_time,
            "is_stale": self.is_stale,
            "lag_seconds": self.lag_seconds,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "KlineSyncStatus":
        return cls(
            timeframe=data.get("timeframe", "4h"),
            last_close_time=int(data.get("last_close_time", 0)),
            expected_close_time=int(data.get("expected_close_time", 0)),
            is_stale=bool(data.get("is_stale", True)),
            lag_seconds=int(data.get("lag_seconds", 0)),
        )


# ============================================
# 触发器配置常量 (可被 config.yaml 覆盖)
# ============================================

# 锚点偏移阈值 (绝对值，必要条件)
DEFAULT_ANCHOR_DRIFT_THRESHOLD = 0.03  # 3%

# 重构冷冻期 (秒)
DEFAULT_REBUILD_COOLDOWN = 4 * 60 * 60  # 4 小时

# 评分刷新冷冻期 (秒)
DEFAULT_SCORE_REFRESH_COOLDOWN = {
    "4h":  15 * 60,       # 战略层: 15 分钟
    "15m": 5 * 60,        # 战术层: 5 分钟
}

# K 线同步最大延迟 (秒)
DEFAULT_MAX_KLINE_LAG = {
    "1w":  600,   # 周线允许 10 分钟延迟
    "3d":  600,   # 3日线允许 10 分钟延迟
    "1d":  300,   # 日线允许 5 分钟延迟
    "4h":  60,    # 4h 允许 1 分钟延迟
    "15m": 30,    # 15m 允许 30 秒延迟
}

# K 线步长 (毫秒)
KLINE_INTERVAL_MS = {
    "1w":  7 * 24 * 60 * 60 * 1000,
    "3d":  3 * 24 * 60 * 60 * 1000,
    "1d":  24 * 60 * 60 * 1000,
    "4h":  4 * 60 * 60 * 1000,
    "15m": 15 * 60 * 1000,
}


# ============================================
# V3.2.5 新增数据结构
# ============================================

@dataclass
class TimeframeLayerConfig:
    """
    单层时间框架配置 (V3.2.5)
    
    用于配置四层级系统中的每一层:
    - L1 战略层 (1w/3d)
    - L2 骨架层 (1d)
    - L3 中继层 (4h)
    - L4 战术层 (15m)
    """
    layer: str                            # 层级标识: "l1", "l2", "l3", "l4"
    interval: str                         # 时间框架: "1w", "3d", "1d", "4h", "15m"
    enabled: bool = True                  # 是否启用
    fib_lookback: List[int] = field(default_factory=lambda: [8, 21, 55])  # 斐波那契回溯
    triggers_rebuild: bool = True         # 是否触发重构
    use_fallback: bool = False            # 是否启用降级 (L1 专用)
    fallback_interval: Optional[str] = None  # 降级时间框架 (如 "3d")
    
    def to_dict(self) -> dict:
        return {
            "layer": self.layer,
            "interval": self.interval,
            "enabled": self.enabled,
            "fib_lookback": self.fib_lookback,
            "triggers_rebuild": self.triggers_rebuild,
            "use_fallback": self.use_fallback,
            "fallback_interval": self.fallback_interval,
        }
    
    @classmethod
    def from_dict(cls, data: dict, layer: str) -> "TimeframeLayerConfig":
        return cls(
            layer=layer,
            interval=data.get("interval", "4h"),
            enabled=data.get("enabled", True),
            fib_lookback=data.get("fib_lookback", [8, 21, 55]),
            triggers_rebuild=data.get("triggers_rebuild", True),
            use_fallback=data.get("use_3d_fallback", False),
            fallback_interval="3d" if data.get("use_3d_fallback", False) else None,
        )


@dataclass
class ATRConfig:
    """
    ATR 空间硬约束配置 (V3.2.5 核心)
    
    系统的最高物理准则，所有生成的候选水位必须通过此审计器。
    
    密度审计门槛:
    - gap_min_atr_ratio: 最小间距约束 (默认 0.5×ATR)
      - 若相邻水位距离 < 0.5×ATR，触发能量优先裁剪
      - 裁剪准则: 比较 W_vol，保留 POC/HVN，剔除 LVN
    
    - gap_max_atr_ratio: 最大间距约束 (默认 3.0×ATR)
      - 若相邻水位距离 > 3.0×ATR，触发递归补全
    
    补全优先级:
    1. 战术种子召回 (L4 分形池)
    2. VPVR 能量锚点 (POC/HVN)
    3. 斐波那契数学兜底 (0.618)
    """
    enabled: bool = True                  # 是否启用 ATR 约束
    atr_period: int = 14                  # ATR 计算周期
    atr_timeframe: str = "4h"             # ATR 计算时间框架
    
    # 间距约束 (以 ATR 倍数为单位)
    gap_min_atr_ratio: float = 0.5        # 最小间距 = 0.5 × ATR
    gap_max_atr_ratio: float = 3.0        # 最大间距 = 3.0 × ATR
    
    # 补全优先级
    fill_priority: List[str] = field(default_factory=lambda: ["tactical", "vpvr", "fibonacci"])
    
    # 斐波那契兜底配置
    fibonacci_fill_ratio: float = 0.618   # 兜底插入位置比例
    fibonacci_fill_score: int = 35        # 兜底水位的强制评分
    fibonacci_enabled: bool = True        # 是否启用斐波那契兜底
    
    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "atr_period": self.atr_period,
            "atr_timeframe": self.atr_timeframe,
            "gap_min_atr_ratio": self.gap_min_atr_ratio,
            "gap_max_atr_ratio": self.gap_max_atr_ratio,
            "fill_priority": self.fill_priority,
            "fibonacci_fill_ratio": self.fibonacci_fill_ratio,
            "fibonacci_fill_score": self.fibonacci_fill_score,
            "fibonacci_enabled": self.fibonacci_enabled,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ATRConfig":
        return cls(
            enabled=data.get("enabled", True),
            atr_period=int(data.get("atr_period", 14)),
            atr_timeframe=data.get("atr_timeframe", "4h"),
            gap_min_atr_ratio=float(data.get("gap_min_atr_ratio", 0.5)),
            gap_max_atr_ratio=float(data.get("gap_max_atr_ratio", 3.0)),
            fill_priority=data.get("fill_priority", ["tactical", "vpvr", "fibonacci"]),
            fibonacci_fill_ratio=float(data.get("fibonacci_fill_ratio", 0.618)),
            fibonacci_fill_score=int(data.get("fibonacci_fill_score", 35)),
            fibonacci_enabled=data.get("fibonacci_enabled", True),
        )
    
    def is_too_dense(self, gap: float, atr: float) -> bool:
        """
        检查间距是否过密
        
        Args:
            gap: 两个水位之间的价格差距 (绝对值)
            atr: 当前 ATR 值
        
        Returns:
            True if gap < gap_min_atr_ratio × ATR
        """
        if not self.enabled or atr <= 0:
            return False
        return gap < (self.gap_min_atr_ratio * atr)
    
    def is_too_sparse(self, gap: float, atr: float) -> bool:
        """
        检查间距是否过稀
        
        Args:
            gap: 两个水位之间的价格差距 (绝对值)
            atr: 当前 ATR 值
        
        Returns:
            True if gap > gap_max_atr_ratio × ATR
        """
        if not self.enabled or atr <= 0:
            return False
        return gap > (self.gap_max_atr_ratio * atr)
    
    def get_fibonacci_fill_price(self, upper: float, lower: float) -> float:
        """
        计算斐波那契兜底插入价格
        
        Args:
            upper: 区间上界
            lower: 区间下界
        
        Returns:
            插入价格 (上界 - (上界 - 下界) × ratio)
        """
        return upper - (upper - lower) * self.fibonacci_fill_ratio


@dataclass
class FilledLevel:
    """
    补全水位数据结构 (V3.2.5)
    
    标记由 ATR 稀疏补全机制插入的水位，
    便于回测分析和诊断。
    """
    price: float                          # 插入价格
    fill_type: str                        # 补全类型: "tactical" | "vpvr" | "fibonacci"
    score: int                            # 分配的评分
    source_layer: Optional[str] = None    # 来源层级 (tactical 类型)
    vpvr_zone: Optional[str] = None       # VPVR 区域类型 (vpvr 类型)
    gap_upper: Optional[float] = None     # 空隙上界
    gap_lower: Optional[float] = None     # 空隙下界
    
    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "fill_type": self.fill_type,
            "score": self.score,
            "source_layer": self.source_layer,
            "vpvr_zone": self.vpvr_zone,
            "gap_upper": self.gap_upper,
            "gap_lower": self.gap_lower,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "FilledLevel":
        return cls(
            price=float(data.get("price", 0)),
            fill_type=data.get("fill_type", "fibonacci"),
            score=int(data.get("score", 35)),
            source_layer=data.get("source_layer"),
            vpvr_zone=data.get("vpvr_zone"),
            gap_upper=data.get("gap_upper"),
            gap_lower=data.get("gap_lower"),
        )


def should_rebuild_grid(
    current_anchor: float,
    last_anchor: float,
    last_rebuild_ts: int,
    anchor_drift_threshold: float = DEFAULT_ANCHOR_DRIFT_THRESHOLD,
    rebuild_cooldown: int = DEFAULT_REBUILD_COOLDOWN,
) -> bool:
    """
    判断是否应该重构网格
    
    必须同时满足:
    1. 锚点偏移 |Δ| > threshold (绝对值，无论涨跌)
    2. 距上次重构 > 冷冻期
    
    Args:
        current_anchor: 当前锚点价格
        last_anchor: 上次锚点价格
        last_rebuild_ts: 上次重构时间戳 (秒)
        anchor_drift_threshold: 锚点偏移阈值 (默认 3%)
        rebuild_cooldown: 重构冷冻期 (秒)
    
    Returns:
        True if should rebuild
    """
    now = int(time.time())
    
    # 冷冻期检查
    if (now - last_rebuild_ts) < rebuild_cooldown:
        return False
    
    # 锚点偏移检查 (绝对值，必要条件)
    if last_anchor > 0:
        drift = abs(current_anchor - last_anchor) / last_anchor
        return drift > anchor_drift_threshold
    
    return False


def can_refresh_score(
    timeframe: str,
    last_refresh_ts: int,
    cooldown_config: Optional[Dict[str, int]] = None,
) -> bool:
    """
    判断是否可以刷新评分 (不触发重构)
    
    Args:
        timeframe: 时间框架 "4h" | "15m"
        last_refresh_ts: 上次刷新时间戳 (秒)
        cooldown_config: 冷冻期配置
    
    Returns:
        True if can refresh
    """
    now = int(time.time())
    config = cooldown_config or DEFAULT_SCORE_REFRESH_COOLDOWN
    cooldown = config.get(timeframe, 15 * 60)
    return (now - last_refresh_ts) >= cooldown


def analyze_rebuild_logs(logs: List[RebuildLog]) -> Dict[str, Any]:
    """
    分析重构日志，识别优化方向
    
    Args:
        logs: 重构日志列表
    
    Returns:
        分析结果，包含触发原因统计和优化建议
    """
    if not logs:
        return {
            "total_rebuilds": 0,
            "by_trigger": {},
            "avg_drift_pct": 0,
            "avg_orders_per_rebuild": 0,
            "suggestion": None,
        }
    
    trigger_counts: Dict[RebuildTrigger, int] = {}
    for log in logs:
        trigger_counts[log.trigger] = trigger_counts.get(log.trigger, 0) + 1
    
    total = len(logs)
    analysis = {
        "total_rebuilds": total,
        "by_trigger": {
            t.value: {
                "count": c,
                "pct": c / total * 100 if total > 0 else 0
            }
            for t, c in trigger_counts.items()
        },
        "avg_drift_pct": sum(l.drift_pct for l in logs) / total if total > 0 else 0,
        "avg_orders_per_rebuild": sum(
            l.orders_cancelled + l.orders_placed for l in logs
        ) / total if total > 0 else 0,
        "suggestion": None,
    }
    
    # 诊断建议
    boundary_count = trigger_counts.get(RebuildTrigger.BOUNDARY_ALERT, 0)
    boundary_pct = boundary_count / total * 100 if total > 0 else 0
    if boundary_pct > 50:
        analysis["suggestion"] = (
            f"⚠️ {boundary_pct:.0f}% 的重构由'覆盖告急'触发，"
            "建议: 1) 扩大手动边界 2) 增加 55x 分形提取范围"
        )
    
    return analysis
