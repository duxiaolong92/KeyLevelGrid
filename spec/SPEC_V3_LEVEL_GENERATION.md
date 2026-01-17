# 📑 KeyLevelGrid V3.0 水位生成与管理核心规格说明书

> **版本**: v3.0.0  
> **状态**: Draft  
> **创建日期**: 2026-01-17  
> **基于**: SPEC_LEVEL_LIFECYCLE.md v2.0.0

---

## 目录

1. [核心设计哲学](#1-核心设计哲学)
2. [环境参数与计算引擎](#2-环境参数与计算引擎)
3. [水位评分机制](#3-水位评分机制-scoring-matrix)
4. [核心管理协议：降序索引继承](#4-核心管理协议降序索引继承)
5. [仓位自动缩放](#5-仓位自动缩放-qty-scaling)
6. [更新触发规则](#6-更新触发规则-event-triggers)
7. [数据结构定义](#7-数据结构定义)
8. [模块设计与实现](#8-模块设计与实现)
9. [与现有系统的集成](#9-与现有系统的集成)
10. [开发执行计划](#10-开发执行计划)

---

## 1. 核心设计哲学

### 1.1 架构升级：从固定间距到市场结构感知

| 版本 | 水位生成逻辑 | 特点 |
|------|-------------|------|
| **V2.x** | 固定间距网格 | 简单、机械、无法适应波动变化 |
| **V3.0** | 多尺度市场结构感知 | 动态、智能、与真实支撑阻力对齐 |

### 1.2 三层构建逻辑："骨架 + 肌肉 + 皮肤"

```
┌─────────────────────────────────────────────────────────┐
│                    水位生成引擎                          │
├─────────────────────────────────────────────────────────┤
│  🦴 骨架 (Structure)                                    │
│     └── 基于斐波那契周期的物理分形点                      │
│         (Fractal Highs/Lows from 8x, 21x, 55x periods)  │
├─────────────────────────────────────────────────────────┤
│  💪 肌肉 (Volume)                                       │
│     └── 基于成交量分布 (VPVR) 的能量验证                  │
│         (HVN = 高能量节点, LVN = 真空区)                 │
├─────────────────────────────────────────────────────────┤
│  🎭 皮肤 (Psychology)                                   │
│     └── 基于斐波那契回撤与整数位的心理吸附                 │
│         (0.618, 0.382, .000, .500 整数位)               │
└─────────────────────────────────────────────────────────┘
```

### 1.3 设计目标

1. **精准性**: 水位与真实市场结构对齐，非任意间距
2. **稳定性**: 保持 V2.0 的索引继承协议，确保持仓连续性
3. **智能性**: 根据水位强度动态调整仓位大小
4. **抗噪性**: 多周期共振过滤虚假信号

---

## 2. 环境参数与计算引擎

### 2.1 时间框架参数

```python
# 主周期设定
MAIN_INTERVAL = "4h"          # 主时间框架

# 斐波那契回溯序列
FIBONACCI_LOOKBACK = [8, 21, 55]

# 物理含义
# - 8x  (短线): 8 * 4h = 32小时 ≈ 1.3天   → 捕捉日内波动
# - 21x (中线): 21 * 4h = 84小时 ≈ 3.5天  → 识别周内核心震荡区
# - 55x (长线): 55 * 4h = 220小时 ≈ 9.2天 → 定义系统安全边际
```

### 2.2 分形识别参数

```python
# 分形定义: 比前后 N 根 K 线都高/低的极值点
FRACTAL_WINDOW = 2           # 分形窗口大小 (左右各2根)

# 分形提取数量
MAX_FRACTALS_PER_PERIOD = 5  # 每周期最多提取5个分形点
```

### 2.3 VPVR 参数

```python
# 成交量分布分析
VPVR_BINS = 50               # 价格区间划分数
HVN_THRESHOLD = 0.7          # 高成交量节点阈值 (前70%分位)
LVN_THRESHOLD = 0.3          # 低成交量真空区阈值 (前30%分位)
```

### 2.4 趋势参考指标

```python
# EMA 隧道指标
EMA_FAST = 144               # 快速 EMA
EMA_SLOW = 169               # 慢速 EMA
```

---

## 3. 水位评分机制 (Scoring Matrix)

### 3.1 评分公式

$$
\text{Final\_Score} = S_{base} \times W_{volume} \times W_{psychology} \times T_{env}
$$

### 3.2 基础分 \(S_{base}\): 结构尺度

根据分形点来源周期赋予初始分。**多周期共振时取最大值**:

$$
S_{base} = \max(S_{period\_1}, S_{period\_2}, ...)
$$

| 来源周期 | 基础分 | 理由 |
|:---------|:-------|:-----|
| **55x (长线)** | 80 | 战略级防线，高确定性 |
| **21x (中线)** | 50 | 核心震荡带，主要盈利区 |
| **8x (短线)** | 20 | 灵敏度高，抗噪性弱 |

```python
BASE_SCORES = {
    55: 80,  # 长线
    21: 50,  # 中线
    8: 20,   # 短线
}
```

### 3.3 修正系数 \(W\): 能量与心理

#### 3.3.1 成交量权重 \(W_{volume}\)

| 条件 | 系数 | 含义 |
|:-----|:-----|:-----|
| HVN (高能量节点) / POC | **1.3** | 筹码密集，强支撑/阻力 |
| 普通区域 | **1.0** | 默认 |
| LVN (真空区) | **0.6** | 价格易穿越，弱支撑 |

```python
VOLUME_WEIGHTS = {
    "HVN": 1.3,
    "NORMAL": 1.0,
    "LVN": 0.6,
}
```

#### 3.3.2 心理位吸附 \(W_{psychology}\)

当水位与以下心理位重合时 (容差 ±0.1%)：
- 斐波那契回撤位 (0.236, 0.382, 0.5, 0.618, 0.786)
- 大整数位 (.000, .500)

| 条件 | 系数 | 备注 |
|:-----|:-----|:-----|
| 与心理位重合 | **1.2** | **水位价格强制对齐至心理位** |
| 无重合 | **1.0** | 保持原始分形价格 |

```python
PSYCHOLOGY_WEIGHT = 1.2
PSYCHOLOGY_TOLERANCE = 0.001  # 0.1% 容差

# 斐波那契回撤比例
FIB_LEVELS = [0.236, 0.382, 0.5, 0.618, 0.786]
```

### 3.4 环境加成 \(T_{env}\): 趋势干预

基于 EMA 144/169 隧道判断趋势：

| 趋势状态 | 支撑位系数 | 阻力位系数 | 理由 |
|:---------|:-----------|:-----------|:-----|
| **多头** (Price > EMA) | **1.1** | **0.9** | 顺势加码支撑，轻仓试探阻力 |
| **空头** (Price < EMA) | **0.9** | **1.1** | 逆势轻仓支撑，重视阻力 |
| **震荡** (EMA 交叉区) | **1.0** | **1.0** | 中性 |

```python
TREND_COEFFICIENTS = {
    "BULLISH": {"support": 1.1, "resistance": 0.9},
    "BEARISH": {"support": 0.9, "resistance": 1.1},
    "NEUTRAL": {"support": 1.0, "resistance": 1.0},
}
```

### 3.5 评分示例

```
场景: BTC 在多头趋势中，于 $94,000 发现一个 21x 分形低点
      该价位处于 VPVR 的 HVN 区域，且接近 0.618 回撤位

计算:
  S_base = 50 (21x 中线)
  W_volume = 1.3 (HVN)
  W_psychology = 1.2 (0.618 回撤)
  T_env = 1.1 (多头支撑)

  Final_Score = 50 × 1.3 × 1.2 × 1.1 = 85.8

结果: 高分水位，执行 1.2x 仓位
```

---

## 4. 核心管理协议：降序索引继承

> ⚠️ **这是系统稳定性的底线逻辑，严禁使用基于价格距离的模糊匹配**

### 4.1 核心不变量

```
INVARIANT: 水位数组必须始终保持价格降序排列
           levels[0].price > levels[1].price > ... > levels[n].price
```

### 4.2 1:1 索引继承规则

继承自 `SPEC_LEVEL_LIFECYCLE.md v2.0.0`:

```python
def inherit_levels_by_index(new_prices, old_levels):
    """
    新数组 N[i] 直接继承旧数组 O[i] 的状态
    
    继承内容:
    - fill_counter: 补仓计数
    - active_inventory 关联
    - 订单追踪状态
    """
    for i in range(min(len(new_prices), len(old_levels))):
        N[i].fill_counter = O[i].fill_counter
        N[i].inherited_from_index = i
```

### 4.3 状态流转规则

```
┌────────────────────────────────────────────────────────┐
│                    状态流转图                           │
├────────────────────────────────────────────────────────┤
│                                                        │
│   ┌─────────┐                        ┌─────────┐      │
│   │ ACTIVE  │ ──── 评分 < 30 ────── │ RETIRED │      │
│   │  活跃   │       或被挤出        │  退役   │      │
│   └────┬────┘                        └────┬────┘      │
│        │                                  │           │
│        │ 允许买入/卖出                     │ 仅允许卖出 │
│        │                                  │           │
│        │                                  ▼           │
│        │                            fill_counter==0   │
│        │                            且无挂单          │
│        │                                  │           │
│        │                            ┌─────┴─────┐     │
│        │                            │   DEAD    │     │
│        │                            │  已销毁   │     │
│        └────────────────────────────┴───────────┘     │
│                                                        │
└────────────────────────────────────────────────────────┘
```

| 状态 | 触发条件 | 允许操作 |
|:-----|:---------|:---------|
| **ACTIVE** | Score ≥ 30 且在索引范围内 | 买入补仓 + 卖出止盈 |
| **RETIRED** | Score < 30 或被挤出索引 | **禁止买入**，仅卖出清仓 |
| **DEAD** | fill_counter == 0 且无挂单 | 物理删除 |

### 4.4 继承目的

当 55x 极点漂移导致水位平移时，确保持仓逻辑在**逻辑层级（第几格）**上保持连续：

```
时刻 T:  [96000, 94000, 92000] ← fill_counter = [1, 2, 0]
                ↓
时刻 T+1: [96500, 94500, 92500] ← fill_counter = [1, 2, 0] (继承)

解释: 虽然价格都上移了 500，但「第二格」的持仓逻辑保持不变
```

---

## 5. 仓位自动缩放 (Qty Scaling)

### 5.1 缩放规则

根据 `Final_Score` 动态决定下单量：

| 评分区间 | 仓位系数 | 含义 |
|:---------|:---------|:-----|
| **Score ≥ 60** | **1.2x** | 强支撑重仓 |
| **30 ≤ Score < 60** | **1.0x** | 基准仓位 |
| **Score < 30** | **0x** | 不开新仓 (若为新水位则丢弃) |

```python
def calculate_qty_multiplier(score: float) -> float:
    if score >= 60:
        return 1.2
    elif score >= 30:
        return 1.0
    else:
        return 0.0  # 不开仓
```

### 5.2 仓位计算示例

```python
base_qty = 0.001  # 基准 BTC 数量

# 高分水位 (score=85)
actual_qty = base_qty * 1.2  # = 0.0012 BTC

# 低分水位 (score=25)
actual_qty = 0  # 不开仓，但保留水位供映射
```

---

## 6. 更新触发规则 (Event Triggers)

### 6.1 触发条件

| 触发器 | 条件 | 说明 |
|:-------|:-----|:-----|
| **锚点偏移** | 55x 周期最高/最低点位移 > 1% | 市场结构重组信号 |
| **覆盖告急** | 现价距最近水位 ≤ 1 格 | 边界防护 |
| **定时刷新** | 每 4 小时 (主周期结束) | 常规对账 |

### 6.2 冷冻期约束

```python
MIN_INHERITANCE_INTERVAL = 15 * 60  # 15 分钟

def can_trigger_inheritance(last_inheritance_ts: int) -> bool:
    now = int(time.time())
    return (now - last_inheritance_ts) >= MIN_INHERITANCE_INTERVAL
```

**理由**: 防止频繁继承导致状态混乱和订单抖动

### 6.3 触发流程

```
┌──────────────────────────────────────────────────────┐
│                  触发检查流程                         │
├──────────────────────────────────────────────────────┤
│                                                      │
│  1. 检查冷冻期 ─────────── 未到期 ────▶ 跳过        │
│         │                                            │
│         ▼ 已到期                                     │
│                                                      │
│  2. 检查触发条件 ──────── 无触发 ────▶ 跳过         │
│         │                                            │
│         ▼ 有触发                                     │
│                                                      │
│  3. 执行 LevelCalculator.generate()                 │
│         │                                            │
│         ▼                                            │
│                                                      │
│  4. 执行 inherit_levels_by_index()                  │
│         │                                            │
│         ▼                                            │
│                                                      │
│  5. 执行订单调整 (撤单 + 重挂)                       │
│         │                                            │
│         ▼                                            │
│                                                      │
│  6. 更新 last_inheritance_ts                        │
│                                                      │
└──────────────────────────────────────────────────────┘
```

---

## 7. 数据结构定义

### 7.1 水位评分数据

```python
@dataclass
class LevelScore:
    """水位评分详情"""
    base_score: float           # 基础分 (来自周期)
    source_periods: List[int]   # 来源周期列表 [8, 21, 55]
    volume_weight: float        # 成交量权重
    volume_zone: str            # "HVN" | "LVN" | "NORMAL"
    psychology_weight: float    # 心理位权重
    psychology_anchor: Optional[float]  # 吸附的心理位价格
    trend_coefficient: float    # 趋势系数
    trend_state: str            # "BULLISH" | "BEARISH" | "NEUTRAL"
    final_score: float          # 最终评分
    
    def to_dict(self) -> dict:
        return {
            "base_score": self.base_score,
            "source_periods": self.source_periods,
            "volume_weight": self.volume_weight,
            "volume_zone": self.volume_zone,
            "psychology_weight": self.psychology_weight,
            "psychology_anchor": self.psychology_anchor,
            "trend_coefficient": self.trend_coefficient,
            "trend_state": self.trend_state,
            "final_score": self.final_score,
        }
```

### 7.2 扩展 GridLevelState

```python
@dataclass
class GridLevelState:
    """扩展: 添加评分相关字段"""
    # ... 现有字段 (from v2.0) ...
    level_id: int
    price: float
    side: str
    role: str
    status: LevelStatus
    lifecycle_status: LevelLifecycleStatus
    fill_counter: int
    inherited_from_index: Optional[int]
    inheritance_ts: Optional[int]
    
    # 🆕 V3.0 评分字段
    score: Optional[LevelScore] = None
    qty_multiplier: float = 1.0          # 仓位系数
    original_price: Optional[float] = None  # 吸附前原始价格
```

### 7.3 分形点数据

```python
@dataclass
class FractalPoint:
    """分形点"""
    price: float
    timestamp: int
    type: str                   # "HIGH" | "LOW"
    period: int                 # 8 | 21 | 55
    kline_index: int            # K 线索引
    
@dataclass
class VPVRData:
    """成交量分布数据"""
    poc_price: float            # 控制价 (Point of Control)
    hvn_zones: List[Tuple[float, float]]  # 高能量区间列表
    lvn_zones: List[Tuple[float, float]]  # 真空区间列表
    total_volume: float
```

---

## 8. 模块设计与实现

### 8.1 模块架构

```
src/key_level_grid/
├── level_calculator.py     # 🆕 水位计算引擎
│   ├── FractalExtractor    # 分形提取器
│   ├── VPVRAnalyzer        # 成交量分析器
│   ├── PsychologyMatcher   # 心理位匹配器
│   └── LevelScorer         # 评分计算器
│
├── level_manager.py        # ✅ 已实现 (v2.0)
│   ├── inherit_levels_by_index()
│   ├── can_destroy_level()
│   └── ...
│
└── position.py             # ✅ 已扩展 (v2.0)
    ├── GridLevelState
    ├── GridState
    └── ...
```

### 8.2 LevelCalculator 接口设计

```python
class LevelCalculator:
    """
    水位计算引擎
    
    职责:
    1. 从 K 线数据提取多周期分形点
    2. 获取 VPVR 成交量分布
    3. 计算综合评分
    4. 输出排序后的目标水位列表
    """
    
    def __init__(
        self,
        main_interval: str = "4h",
        fib_lookback: List[int] = [8, 21, 55],
        ema_fast: int = 144,
        ema_slow: int = 169,
    ):
        self.main_interval = main_interval
        self.fib_lookback = fib_lookback
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
    
    async def generate_target_levels(
        self,
        klines: List[Dict],
        current_price: float,
        role: str = "support",
        max_levels: int = 10,
    ) -> List[Tuple[float, LevelScore]]:
        """
        生成目标水位列表
        
        Returns:
            [(price, score), ...] 按价格降序排列
        """
        # 1. 提取分形点
        fractals = self._extract_fractals(klines)
        
        # 2. 获取 VPVR 数据
        vpvr = self._analyze_vpvr(klines)
        
        # 3. 计算趋势状态
        trend = self._determine_trend(klines, current_price)
        
        # 4. 对每个分形点评分
        scored_levels = []
        for fractal in fractals:
            score = self._calculate_score(fractal, vpvr, trend, role)
            if score.final_score >= 30:  # 过滤低分
                price = self._apply_psychology_snap(fractal.price, klines)
                scored_levels.append((price, score))
        
        # 5. 去重、排序、截断
        return self._finalize_levels(scored_levels, max_levels)
    
    def _extract_fractals(self, klines: List[Dict]) -> List[FractalPoint]:
        """提取多周期分形点"""
        ...
    
    def _analyze_vpvr(self, klines: List[Dict]) -> VPVRData:
        """分析成交量分布"""
        ...
    
    def _determine_trend(self, klines: List[Dict], price: float) -> str:
        """判断趋势状态"""
        ...
    
    def _calculate_score(
        self,
        fractal: FractalPoint,
        vpvr: VPVRData,
        trend: str,
        role: str,
    ) -> LevelScore:
        """计算综合评分"""
        ...
    
    def _apply_psychology_snap(
        self,
        price: float,
        klines: List[Dict],
    ) -> float:
        """应用心理位吸附"""
        ...
```

### 8.3 IndexInheritor 接口设计

```python
class IndexInheritor:
    """
    索引继承器
    
    职责:
    1. 执行降序排列后的 1:1 状态迁移
    2. 输出 InheritanceResult
    3. 处理订单撤销/重挂
    """
    
    def execute(
        self,
        target_levels: List[Tuple[float, LevelScore]],
        current_levels: List[GridLevelState],
        active_inventory: List[ActiveFill],
    ) -> InheritanceResult:
        """
        执行继承
        
        Args:
            target_levels: 新目标水位 [(price, score), ...]
            current_levels: 当前水位列表
            active_inventory: 当前持仓
        
        Returns:
            InheritanceResult 包含:
            - active_levels: 新活跃水位
            - retired_levels: 退役水位
            - orders_to_cancel: 待撤订单
            - orders_to_place: 待挂订单
            - inventory_updates: 持仓更新
        """
        # 提取新价格列表
        new_prices = [price for price, _ in target_levels]
        
        # 调用现有的 inherit_levels_by_index
        result = inherit_levels_by_index(
            new_prices, current_levels, active_inventory
        )
        
        # 附加评分信息到新水位
        for i, level in enumerate(result.active_levels):
            if i < len(target_levels):
                _, score = target_levels[i]
                level.score = score
                level.qty_multiplier = self._calc_multiplier(score.final_score)
        
        return result
    
    def _calc_multiplier(self, score: float) -> float:
        if score >= 60:
            return 1.2
        elif score >= 30:
            return 1.0
        return 0.0
```

---

## 9. 与现有系统的集成

### 9.1 集成点

| 组件 | 集成方式 |
|:-----|:---------|
| `GridPositionManager` | 调用 `LevelCalculator` 生成水位 |
| `KeyLevelGridStrategy` | 在 `_update_cycle` 中触发水位更新 |
| `level_manager.py` | 复用现有继承逻辑 |
| `position.py` | 扩展 `GridLevelState` 添加评分字段 |

### 9.2 向后兼容

- **state.json**: 新字段 (`score`, `qty_multiplier`) 可选，旧版自动默认
- **继承逻辑**: 完全复用 v2.0 的 `inherit_levels_by_index()`
- **订单执行**: 无变化，仅下单数量根据 `qty_multiplier` 调整

### 9.3 配置扩展

```yaml
# configs/config.yaml

grid:
  # 🆕 V3.0 水位生成配置
  level_generation:
    enabled: true                     # 启用动态水位生成
    main_interval: "4h"               # 主周期
    fib_lookback: [8, 21, 55]         # 斐波那契回溯序列
    ema_fast: 144                     # 快速 EMA
    ema_slow: 169                     # 慢速 EMA
    max_levels: 10                    # 最大水位数
    min_score: 30                     # 最低评分阈值
    
  level_scoring:
    base_scores:
      55: 80
      21: 50
      8: 20
    volume_weights:
      HVN: 1.3
      NORMAL: 1.0
      LVN: 0.6
    psychology_weight: 1.2
    trend_coefficients:
      BULLISH:
        support: 1.1
        resistance: 0.9
      BEARISH:
        support: 0.9
        resistance: 1.1
```

---

## 10. 开发执行计划

### 10.1 阶段划分

| 阶段 | 任务 | 优先级 | 依赖 |
|:-----|:-----|:-------|:-----|
| **Phase 1** | 实现 `FractalExtractor` | P0 | 无 |
| **Phase 2** | 实现 `VPVRAnalyzer` | P1 | Phase 1 |
| **Phase 3** | 实现 `LevelScorer` | P0 | Phase 1, 2 |
| **Phase 4** | 实现 `IndexInheritor` 封装 | P0 | Phase 3 |
| **Phase 5** | 集成到 `GridPositionManager` | P0 | Phase 4 |
| **Phase 6** | 配置与 UI 展示 | P2 | Phase 5 |

### 10.2 详细任务清单

#### Phase 1: FractalExtractor

```
P1.1: 实现 K 线数据结构解析
P1.2: 实现分形识别算法 (左右 N 根比较)
P1.3: 实现多周期分形提取 (8x, 21x, 55x)
P1.4: 编写单元测试
```

#### Phase 2: VPVRAnalyzer

```
P2.1: 实现价格区间划分
P2.2: 实现成交量累积计算
P2.3: 实现 HVN/LVN 区域识别
P2.4: 实现 POC (控制价) 计算
P2.5: 编写单元测试
```

#### Phase 3: LevelScorer

```
P3.1: 实现基础分计算 (周期共振)
P3.2: 实现成交量权重计算
P3.3: 实现心理位吸附 (Fib + 整数位)
P3.4: 实现趋势系数计算 (EMA 隧道)
P3.5: 实现最终评分公式
P3.6: 编写单元测试
```

#### Phase 4: IndexInheritor 封装

```
P4.1: 封装 inherit_levels_by_index 调用
P4.2: 实现评分附加逻辑
P4.3: 实现 qty_multiplier 计算
P4.4: 编写集成测试
```

#### Phase 5: 系统集成

```
P5.1: 扩展 GridLevelState 数据结构
P5.2: 修改 GridPositionManager.create_grid
P5.3: 实现触发检查逻辑
P5.4: 添加冷冻期约束
P5.5: 编写端到端测试
```

### 10.3 Cursor / AI 执行指令

1. **实现 `LevelCalculator`**: 负责 K 线回溯、分形提取、VPVR 映射及三级评分计算
2. **实现 `IndexInheritor`**: 负责执行降序排列后的 1:1 状态迁移，输出 `InheritanceResult`
3. **重构 `PositionManager`**: 根据 `ACTIVE/RETIRED` 状态执行分流对账逻辑

---

## 附录 A: 评分计算参考实现

```python
def calculate_final_score(
    fractal: FractalPoint,
    vpvr: VPVRData,
    trend: str,
    role: str,
    fib_range: Tuple[float, float],
) -> LevelScore:
    """
    计算最终评分
    
    公式: Final = S_base × W_volume × W_psychology × T_env
    """
    # 1. 基础分
    base_score = BASE_SCORES.get(fractal.period, 20)
    
    # 2. 成交量权重
    volume_zone = "NORMAL"
    volume_weight = 1.0
    for low, high in vpvr.hvn_zones:
        if low <= fractal.price <= high:
            volume_zone = "HVN"
            volume_weight = 1.3
            break
    for low, high in vpvr.lvn_zones:
        if low <= fractal.price <= high:
            volume_zone = "LVN"
            volume_weight = 0.6
            break
    
    # 3. 心理位吸附
    psychology_weight = 1.0
    psychology_anchor = None
    snap_price = find_psychology_snap(fractal.price, fib_range)
    if snap_price:
        psychology_weight = 1.2
        psychology_anchor = snap_price
    
    # 4. 趋势系数
    trend_coef = TREND_COEFFICIENTS.get(trend, {}).get(role, 1.0)
    
    # 5. 最终评分
    final_score = base_score * volume_weight * psychology_weight * trend_coef
    
    return LevelScore(
        base_score=base_score,
        source_periods=[fractal.period],
        volume_weight=volume_weight,
        volume_zone=volume_zone,
        psychology_weight=psychology_weight,
        psychology_anchor=psychology_anchor,
        trend_coefficient=trend_coef,
        trend_state=trend,
        final_score=final_score,
    )
```

---

## 附录 B: 与 V2.0 继承规格的关系

| V2.0 规格 | V3.0 继承 | 变化 |
|:----------|:----------|:-----|
| `inherit_levels_by_index()` | ✅ 完全复用 | 无 |
| `LevelLifecycleStatus` | ✅ 完全复用 | 无 |
| `can_destroy_level()` | ✅ 完全复用 | 无 |
| 降序排列不变量 | ✅ 强化 | 从建议变为强制 |
| 水位生成 | 🆕 全新 | 从固定间距到分形评分 |
| 仓位计算 | 🆕 全新 | 从固定到动态缩放 |

---

**文档结束**
