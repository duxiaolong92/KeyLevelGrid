# KeyLevelGrid V3.1.0 重构计划

> **生成日期**: 2026-01-17  
> **最后更新**: 2026-01-17  
> **基于文档**: CONSTITUTION.md v1.3.0 + LEVEL_GENERATION.md v3.1.0

---

## 🎯 执行进度

| 阶段 | 状态 | 完成时间 |
|:-----|:-----|:---------|
| Phase 0: 基础设施 | ✅ 完成 | 2026-01-17 |
| Phase 1: LevelCalculator | ✅ 完成 | 2026-01-17 |
| Phase 2: MTFKlineFeed | ✅ 完成 | 2026-01-17 |
| Phase 3: AtomicRebuildExecutor | ✅ 完成 | 2026-01-17 |
| Phase 4: 系统集成 | ✅ 完成 | 2026-01-17 |
| Phase 5: 硬编码清理 | ✅ 完成 | 2026-01-17 |

**🎉 V3.1.0 重构全部完成！**

---

## 📊 差距分析摘要 (已更新)

### 当前实现状态 (V3.0)

| 模块 | 状态 | 说明 |
|:-----|:-----|:-----|
| `level_lifecycle.py` | ✅ 已实现 | `inherit_levels_by_index`, `can_destroy_level` |
| `GridLevelState` | ✅ 已扩展 | 新增 `score`, `qty_multiplier`, `original_price` 字段 |
| `GridState` | ✅ 已扩展 | 新增 `rebuild_logs`, `last_rebuild_ts` 字段 |
| `scoring.py` | ✅ 新建 | `LevelScore`, `FractalPoint`, `VPVRData`, `MTFLevelCandidate` |
| `triggers.py` | ✅ 新建 | `RebuildTrigger`, `RebuildLog`, `ManualBoundary`, `PendingMigration` |
| `level_calculator.py` | ✅ 新建 | MTF 水位生成引擎 |
| `mtf_feed.py` | ✅ 新建 | `MTFKlineFeed` + `is_synced()` 一致性锁 |
| `atomic_rebuild.py` | ✅ 新建 | `AtomicRebuildExecutor` + ALARM 模式 |
| `fractal.py` | ✅ 新建 | `FractalExtractor` 分形点提取 |
| `vpvr.py` | ✅ 新建 | `VPVRAnalyzer` 成交量分布分析 |
| `psychology.py` | ✅ 新建 | `PsychologyMatcher` 心理位匹配 |
| `scorer.py` | ✅ 新建 | `LevelScorer` MTF 评分计算 |
| `mtf_merger.py` | ✅ 新建 | `MTFMerger` 多框架融合 |

### 硬编码违规清单

| 文件 | 位置 | 硬编码值 | 应改为配置 |
|:-----|:-----|:---------|:-----------|
| `position.py` | L41 | `PRICE_TOLERANCE = 0.0001` | `config.price_tolerance` |
| `position.py` | L325 | `order.price * 0.003` | `config.buy_trigger_tolerance` |
| `position.py` | L858 | `price_tol = 0.0001` | `config.price_tolerance` |
| `level_lifecycle.py` | L90 | `tolerance: float = 0.0001` | `config.price_tolerance` |
| `mtf.py` | L33 | `trend_lookback: int = 20` | `config.trend_lookback` |

### 缺失数据结构

```python
# V3.1.0 必需但当前缺失的数据结构
LevelScore          # 水位评分详情
FractalPoint        # 分形点
VPVRData            # 成交量分布
MTFLevelCandidate   # MTF 水位候选
RebuildLog          # 重构日志
RebuildTrigger      # 重构触发原因枚举
ManualBoundary      # 手动边界设置
KlineSyncStatus     # K 线同步状态
PendingMigration    # 事务日志
RebuildPhase        # 重构阶段枚举
```

---

## 📋 重构任务清单

### Phase 0: 基础设施 - 数据结构与配置扩展 (P0)

#### T0.1 扩展 `GridLevelState` 数据结构

**文件**: `src/key_level_grid/core/state.py`

```python
# 新增字段
@dataclass
class GridLevelState:
    # ... 现有字段 ...
    
    # 🆕 V3.0 评分字段
    score: Optional["LevelScore"] = None       # 评分详情
    qty_multiplier: float = 1.0                # 仓位系数 (1.0/1.2/1.5)
    original_price: Optional[float] = None     # 吸附前原始价格
```

**检查点**:
- [ ] `to_dict()` 包含新字段
- [ ] `from_dict()` 支持向后兼容（旧数据默认值）
- [ ] 单元测试覆盖

---

#### T0.2 新增评分相关数据结构

**文件**: `src/key_level_grid/core/scoring.py` (新建)

```python
from dataclasses import dataclass
from typing import List, Optional, Tuple
from enum import Enum

class VolumeZone(str, Enum):
    HVN = "HVN"        # 高成交量节点
    NORMAL = "NORMAL"  # 普通区域
    LVN = "LVN"        # 真空区

@dataclass
class LevelScore:
    """水位评分详情 (MTF 增强版)"""
    base_score: float
    source_timeframes: List[str]
    source_periods: List[int]
    volume_weight: float
    volume_zone: VolumeZone
    psychology_weight: float
    psychology_anchor: Optional[float]
    trend_coefficient: float
    trend_state: str
    mtf_coefficient: float = 1.0
    is_resonance: bool = False
    final_score: float = 0.0

@dataclass
class FractalPoint:
    """分形点"""
    price: float
    timestamp: int
    type: str           # "HIGH" | "LOW"
    timeframe: str      # "1d" | "4h" | "15m"
    period: int         # 回溯周期
    kline_index: int

@dataclass
class VPVRData:
    """成交量分布数据"""
    poc_price: float
    hvn_zones: List[Tuple[float, float]]
    lvn_zones: List[Tuple[float, float]]
    total_volume: float

@dataclass
class MTFLevelCandidate:
    """MTF 水位候选"""
    price: float
    source_fractals: List[FractalPoint]
    source_timeframes: List[str]
    is_resonance: bool
    merged_price: float
```

---

#### T0.3 新增触发与日志数据结构

**文件**: `src/key_level_grid/core/triggers.py` (新建)

```python
from dataclasses import dataclass
from typing import Optional
from enum import Enum

class RebuildTrigger(str, Enum):
    ANCHOR_DRIFT = "ANCHOR_DRIFT"
    BOUNDARY_ALERT = "BOUNDARY_ALERT"
    DAILY_REFRESH = "DAILY_REFRESH"
    MANUAL_REBUILD = "MANUAL_REBUILD"
    COLD_START = "COLD_START"

@dataclass
class RebuildLog:
    """重构日志"""
    timestamp: int
    trigger: RebuildTrigger
    anchor_before: float
    anchor_after: float
    drift_pct: float
    levels_before: int
    levels_after: int
    orders_cancelled: int
    orders_placed: int
    detail: Optional[str] = None

@dataclass
class ManualBoundary:
    """手动边界设置"""
    enabled: bool = False
    upper_price: Optional[float] = None
    lower_price: Optional[float] = None
```

---

#### T0.4 扩展 `GridState` 支持重构日志

**文件**: `src/key_level_grid/core/state.py`

```python
@dataclass
class GridState:
    # ... 现有字段 ...
    
    # 🆕 V3.0 字段
    rebuild_logs: List["RebuildLog"] = field(default_factory=list)
    last_rebuild_ts: int = 0
    last_score_refresh_ts: int = 0
```

---

#### T0.5 扩展配置文件结构

**文件**: `configs/config.yaml`

```yaml
grid:
  # === 现有配置 ===
  # ...
  
  # 🆕 V3.1 水位生成配置
  level_generation:
    enabled: true
    max_levels: 10
    min_score: 30
    
    # 触发规则
    triggers:
      anchor_drift_threshold: 0.03    # |Δ| > 3% 必要条件
      rebuild_cooldown_sec: 14400     # 4 小时
      score_refresh_cooldown:
        "4h": 900
        "15m": 300
    
    # 手动边界
    manual_boundary:
      enabled: false
      upper_price: null
      lower_price: null
    
    # 重构日志
    rebuild_logging:
      enabled: true
      max_logs: 100
    
    # MTF 配置
    mtf:
      enabled: true
      resonance_tolerance: 0.003
      trend:
        interval: "1d"
        fib_lookback: [21, 55, 89]
        max_fractals: 3
      main:
        interval: "4h"
        fib_lookback: [8, 21, 55]
        max_fractals: 5
      tactical:
        interval: "15m"
        fib_lookback: [13, 34, 55]
        max_fractals: 7
        triggers_rebuild: false
  
  # 🆕 评分配置
  level_scoring:
    timeframe_weights:
      "1d": 1.5
      "4h": 1.0
      "15m": 0.6
    period_scores:
      89: 80
      55: 80
      34: 50
      21: 50
      13: 20
      8: 20
    volume_weights:
      HVN: 1.3
      NORMAL: 1.0
      LVN: 0.6
    psychology_weight: 1.2
    trend_coefficients:
      BULLISH: { support: 1.1, resistance: 0.9 }
      BEARISH: { support: 0.9, resistance: 1.1 }
    mtf_resonance:
      "1d+4h+15m": 2.0
      "1d+4h": 1.5
      "1d+15m": 1.3
      "4h+15m": 1.2
```

---

### Phase 1: 核心引擎 - LevelCalculator 模块 (P0)

#### T1.1 创建 `FractalExtractor` 分形提取器

**文件**: `src/key_level_grid/analysis/fractal.py` (新建)

**功能**:
- 从 K 线数据提取分形高点/低点
- 支持多周期回溯 (8/13/21/34/55/89)
- 支持可配置的分形窗口大小

**接口**:
```python
class FractalExtractor:
    def extract(
        self,
        klines: List[Dict],
        timeframe: str,
        lookback_periods: List[int],
        fractal_window: int = 2,
        max_fractals: int = 5,
    ) -> List[FractalPoint]:
        """提取分形点"""
```

---

#### T1.2 创建 `VPVRAnalyzer` 成交量分析器

**文件**: `src/key_level_grid/analysis/vpvr.py` (新建)

**功能**:
- 构建价格-成交量分布直方图
- 识别 HVN (高成交量节点) / LVN (真空区)
- 计算 POC (控制价)

**接口**:
```python
class VPVRAnalyzer:
    def analyze(
        self,
        klines: List[Dict],
        bins: int = 50,
        hvn_threshold: float = 0.7,
        lvn_threshold: float = 0.3,
    ) -> VPVRData:
        """分析成交量分布"""
```

---

#### T1.3 创建 `PsychologyMatcher` 心理位匹配器

**文件**: `src/key_level_grid/analysis/psychology.py` (新建)

**功能**:
- 计算斐波那契回撤位
- 识别整数位 (.000, .500)
- 将分形价格吸附到最近心理位

**接口**:
```python
class PsychologyMatcher:
    def find_nearest_anchor(
        self,
        price: float,
        fib_range: Tuple[float, float],
        tolerance: float = 0.001,
    ) -> Optional[float]:
        """查找最近的心理位锚点"""
```

---

#### T1.4 创建 `MTFMerger` 多时间框架融合器

**文件**: `src/key_level_grid/analysis/mtf_merger.py` (新建)

**功能**:
- 检测跨时间框架的价格共振
- 合并共振水位 (以高时间框架为准)
- 输出 `MTFLevelCandidate` 列表

**接口**:
```python
class MTFMerger:
    def merge(
        self,
        fractals: List[FractalPoint],
        resonance_tolerance: float = 0.003,
    ) -> List[MTFLevelCandidate]:
        """合并跨时间框架的分形点"""
```

---

#### T1.5 创建 `LevelScorer` 评分计算器

**文件**: `src/key_level_grid/analysis/scorer.py` (新建)

**功能**:
- 计算基础分 (时间框架权重 × 周期权重)
- 应用成交量/心理位/趋势修正系数
- 计算 MTF 共振系数
- 输出最终评分

**接口**:
```python
class LevelScorer:
    def calculate_score(
        self,
        candidate: MTFLevelCandidate,
        vpvr: VPVRData,
        trend: str,
        role: str,
        config: ScoringConfig,
    ) -> LevelScore:
        """计算水位评分"""
```

---

#### T1.6 组装 `LevelCalculator` 主类

**文件**: `src/key_level_grid/level_calculator.py` (新建)

**功能**:
- 集成所有子模块
- 提供 `generate_target_levels()` 主入口
- 处理配置加载与验证

**接口**:
```python
class LevelCalculator:
    async def generate_target_levels(
        self,
        klines_by_tf: Dict[str, List[Dict]],
        current_price: float,
        role: str = "support",
        max_levels: int = 10,
    ) -> Optional[List[Tuple[float, LevelScore]]]:
        """生成目标水位列表"""
```

---

#### T1.7 🆕 重构 `ResistanceCalculator` 为可复用子模块

**背景**: 
现有 `ResistanceCalculator` 已实现部分 V3.0 所需功能（分形点、VPVR、心理位），应**复用而非删除**。

**策略**: 增量重构，保持向后兼容

**依赖分析**:
| 模块 | 调用方式 |
|:-----|:---------|
| `strategy_main.py` | `resistance_calc.calculate_support_levels()` |
| `strategy.py` | `resistance_calc.calculate_resistance_levels()` |
| `position.py` | 作为 `@property` 提供 |
| `display.py` | Telegram 显示 |
| `telegram/bot.py` | `/levels` 命令 |

**重构步骤**:

```
┌─────────────────────────────────────────────────────────┐
│  Step 1: 提取子模块 (不破坏现有接口)                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  resistance.py::_find_multi_scale_swings()              │
│       └──▶ fractal.py::FractalExtractor.extract()      │
│                                                         │
│  resistance.py::_find_volume_nodes()                    │
│       └──▶ vpvr.py::VPVRAnalyzer.analyze()             │
│                                                         │
│  resistance.py::_find_psychological_levels()            │
│       └──▶ psychology.py::PsychologyMatcher.find()     │
│                                                         │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Step 2: ResistanceCalculator 改为调用新子模块           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  class ResistanceCalculator:                            │
│      def __init__(self, ...):                           │
│          self.fractal = FractalExtractor(...)           │
│          self.vpvr = VPVRAnalyzer(...)                  │
│          self.psychology = PsychologyMatcher(...)       │
│                                                         │
│      def _find_multi_scale_swings(self, ...):           │
│          return self.fractal.extract(...)  # 委托调用   │
│                                                         │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Step 3: LevelCalculator 复用子模块                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  class LevelCalculator:                                 │
│      def __init__(self, ...):                           │
│          # 复用相同的子模块实例                           │
│          self.fractal = FractalExtractor(...)           │
│          self.vpvr = VPVRAnalyzer(...)                  │
│          self.psychology = PsychologyMatcher(...)       │
│          # V3.0 新增                                    │
│          self.merger = MTFMerger(...)                   │
│          self.scorer = LevelScorer(...)                 │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**配置开关**:
```yaml
grid:
  level_generation:
    enabled: true         # true = 使用 V3.0 LevelCalculator
                          # false = 使用旧版 ResistanceCalculator
```

**验收标准**:
- [ ] `ResistanceCalculator` 所有现有测试通过
- [ ] `strategy.py` / `strategy_main.py` 无需修改
- [ ] Telegram `/levels` 命令正常工作
- [ ] 配置开关可切换新旧逻辑

---

### Phase 2: MTF 增强 - MTFKlineFeed 与一致性锁 (P0)

#### T2.1 创建 `KlineSyncStatus` 数据结构

**文件**: `src/key_level_grid/core/sync.py` (新建)

```python
@dataclass
class KlineSyncStatus:
    timeframe: str
    last_close_time: int
    expected_close_time: int
    is_stale: bool
    lag_seconds: int
```

---

#### T2.2 创建 `MTFKlineFeed` 管理器

**文件**: `src/key_level_grid/data/feeds/mtf_feed.py` (新建)

**功能**:
- 管理 1d/4h/15m 三个时间框架的 K 线订阅
- 实现 `is_synced()` 一致性锁检查
- 提供 `get_klines_if_synced()` 安全获取接口

**接口**:
```python
class MTFKlineFeed:
    def is_synced(self, reference_time: Optional[int] = None) -> bool:
        """检查三个时间框架是否同步"""
    
    def get_sync_report(self) -> Dict[str, KlineSyncStatus]:
        """获取同步状态报告"""
    
    async def get_klines_if_synced(
        self,
        lookback: Dict[str, int],
    ) -> Optional[Dict[str, List[Dict]]]:
        """仅在同步时返回 K 线数据"""
```

---

#### T2.3 增强现有 `MultiTimeframeManager`

**文件**: `src/key_level_grid/analysis/mtf.py`

- 集成 `MTFKlineFeed`
- 添加趋势状态缓存
- 支持从配置读取 `trend_lookback`

---

### Phase 3: 原子性重构 - AtomicRebuildExecutor (P0)

#### T3.1 创建 `RebuildPhase` 枚举

**文件**: `src/key_level_grid/core/triggers.py`

```python
class RebuildPhase(str, Enum):
    PENDING = "PENDING"
    CANCELLING = "CANCELLING"
    PLACING = "PLACING"
    SYNCING = "SYNCING"
    COMPLETED = "COMPLETED"
    ALARM = "ALARM"
    RETRY = "RETRY"
```

---

#### T3.2 创建 `PendingMigration` 事务日志

**文件**: `src/key_level_grid/core/triggers.py`

```python
@dataclass
class PendingMigration:
    phase: RebuildPhase
    started_at: int
    orders_to_cancel: List[str]
    orders_cancelled: List[str]
    orders_to_place: List[dict]
    orders_placed: List[str]
    failed_orders: List[dict]
    error_message: Optional[str] = None
```

---

#### T3.3 创建 `AtomicRebuildExecutor`

**文件**: `src/key_level_grid/strategy/grid/atomic_rebuild.py` (新建)

**功能**:
- 四阶段原子性执行流程
- 撤单失败 → ALARM 模式
- 挂单部分失败 → RETRY 模式
- 崩溃恢复机制

**接口**:
```python
class AtomicRebuildExecutor:
    async def execute_rebuild(
        self,
        inheritance_result: InheritanceResult,
    ) -> bool:
        """原子性执行重构"""
    
    def recover_from_crash(self) -> Optional[PendingMigration]:
        """崩溃恢复检查"""
```

---

### Phase 4: 系统集成 - GridPositionManager 重构 (P0)

#### T4.1 重构 `create_grid` 方法

**文件**: `src/key_level_grid/position.py`

- 集成 `LevelCalculator` 生成水位
- 支持 MTF 评分
- 应用 `qty_multiplier` 到下单量

---

#### T4.2 实现触发检查逻辑

**文件**: `src/key_level_grid/position.py`

- 实现 `should_rebuild_grid()` 判断
- 实现 `can_refresh_score()` 判断
- 区分「重构路径」和「评分刷新路径」

---

#### T4.3 实现重构日志记录

**文件**: `src/key_level_grid/position.py`

- 调用 `log_rebuild()` 记录每次重构
- 支持 `analyze_rebuild_logs()` 分析

---

#### T4.4 集成 `AtomicRebuildExecutor`

**文件**: `src/key_level_grid/position.py`

- 替换现有的直接撤挂单逻辑
- 处理 ALARM/RETRY 模式

---

### Phase 5: 硬编码清理与配置化 (P1)

#### T5.1 提取 `position.py` 中的硬编码

| 原位置 | 原值 | 新配置路径 |
|:-------|:-----|:-----------|
| L41 | `0.0001` | `self.config.price_tolerance` |
| L325 | `0.003` | `self.config.buy_trigger_tolerance` |
| L858 | `0.0001` | `self.config.price_tolerance` |

---

#### T5.2 提取 `level_lifecycle.py` 中的硬编码

| 原位置 | 原值 | 新配置路径 |
|:-------|:-----|:-----------|
| L90 | `0.0001` | `config.price_tolerance` |

---

#### T5.3 提取 `mtf.py` 中的硬编码

| 原位置 | 原值 | 新配置路径 |
|:-------|:-----|:-----------|
| L33 | `20` | `config.trend_lookback` |

---

#### T5.4 更新配置加载逻辑

**文件**: `src/key_level_grid/core/config.py`

- 新增 `LevelGenerationConfig` 类
- 新增 `ScoringConfig` 类
- 新增 `TriggerConfig` 类

---

## ⚠️ 降序索引继承准则审查

### 当前实现状态

`level_lifecycle.py::inherit_levels_by_index` **正确实现了** 1:1 索引继承，但存在以下风险点：

| 检查项 | 状态 | 说明 |
|:-------|:-----|:-----|
| 输入验证 | ⚠️ 缺失 | 未验证 `new_prices` 是否降序排列 |
| 自动排序 | ⚠️ 缺失 | 应在继承前自动调用 `sort_levels_descending` |
| 不变量断言 | ⚠️ 缺失 | 应在继承后断言 `validate_level_order` |

### 修复建议

```python
def inherit_levels_by_index(
    new_prices: List[float],
    old_levels: List[GridLevelState],
    active_inventory: List[ActiveFill],
    ...
) -> InheritanceResult:
    # 🆕 Step 0: 验证并强制排序
    new_prices = sorted(new_prices, reverse=True)  # 强制降序
    old_levels = sort_levels_descending(old_levels)
    
    # 验证
    if len(new_prices) > 1:
        for i in range(len(new_prices) - 1):
            assert new_prices[i] > new_prices[i + 1], \
                f"new_prices 必须严格降序: {new_prices}"
    
    # ... 现有逻辑 ...
    
    # 🆕 Step Final: 断言输出降序
    assert validate_level_order(result.active_levels), \
        "继承结果必须满足降序约束"
    
    return result
```

---

## 📅 执行时间线

| 阶段 | 预估工时 | 优先级 | 依赖 |
|:-----|:---------|:-------|:-----|
| Phase 0 | 4h | P0 | 无 |
| Phase 1 | 8h | P0 | Phase 0 |
| Phase 2 | 4h | P0 | Phase 1 |
| Phase 3 | 6h | P0 | Phase 2 |
| Phase 4 | 8h | P0 | Phase 3 |
| Phase 5 | 2h | P1 | Phase 4 |

**总计**: ~32h

---

## ✅ 验收标准

1. **单元测试**:
   - [ ] `FractalExtractor` 测试覆盖率 > 80%
   - [ ] `LevelScorer` 测试覆盖 MTF 共振场景
   - [ ] `AtomicRebuildExecutor` 测试 ALARM/RETRY 模式

2. **集成测试**:
   - [ ] 端到端水位生成流程
   - [ ] 重构触发条件验证
   - [ ] 崩溃恢复场景

3. **CONSTITUTION 合规**:
   - [ ] 无硬编码魔数 (P10)
   - [ ] 所有配置有中文说明 (P11)
   - [ ] 无物理删除 fill_counter > 0 水位 (P9)

---

**文档结束**
