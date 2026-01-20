# 📑 KeyLevelGrid V3.2.5 水位生成与管理核心规格说明书

> **版本**: v3.2.5  
> **状态**: Active  
> **创建日期**: 2026-01-17  
> **更新日期**: 2026-01-18  
> **基于**: SPEC_LEVEL_LIFECYCLE.md v2.0.0, CONSTITUTION.md v1.4.0

---

## 📋 变更日志

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v3.2.5 | 2026-01-18 | 🆕 四层级全量探测系统、ATR 空间硬约束协议、评分矩阵回归 |
| v3.1.0 | 2026-01-17 | 三层 MTF 体系、评分公式、触发规则 |
| v3.0.0 | 2026-01-17 | 初始版本 |

---

## 目录

1. [核心设计哲学](#1-核心设计哲学)
2. [四层级全量探测系统 (MTF v3.2.5)](#2-四层级全量探测系统-mtf-v325)
3. [🆕 ATR 空间硬约束协议](#3-atr-空间硬约束协议-hard-gap-constraint)
4. [水位评分机制 (Scoring Matrix v3.2.5)](#4-水位评分机制-scoring-matrix-v325)
5. [重构触发准则 (Rebuild Rules)](#5-重构触发准则-rebuild-rules)
6. [核心管理协议：降序索引继承](#6-核心管理协议降序索引继承)
7. [仓位自动缩放](#7-仓位自动缩放-qty-scaling)
8. [数据结构定义](#8-数据结构定义)
9. [模块设计与实现](#9-模块设计与实现)
10. [配置参数清单](#10-配置参数清单)

---

## 1. 核心设计哲学

### 1.1 架构升级：V3.2.5 精简自适应版

| 版本 | 水位生成逻辑 | 特点 |
|------|-------------|------|
| **V2.x** | 固定间距网格 | 简单、机械、无法适应波动变化 |
| **V3.0** | 三层 MTF + 动态权重 | 复杂、参数多、调优难 |
| **V3.2.5** | 🆕 **四层级全量探测 + ATR 硬约束** | 简化、自适应、物理边界明确 |

### 1.2 V3.2.5 核心原则

```
┌─────────────────────────────────────────────────────────────────┐
│                    V3.2.5 设计原则                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  📐 原则一: ATR 即物理法则                                       │
│     └── 所有水位间距必须通过 ATR 审计，无例外                     │
│                                                                 │
│  🔄 原则二: 全量探测 + 后置裁剪                                   │
│     └── 先提取所有层级分形，再用 ATR 统一裁剪/补全                │
│                                                                 │
│  📊 原则三: 成交量是裁剪依据                                     │
│     └── 过密时保留 POC/HVN，剔除 LVN                            │
│                                                                 │
│  🛡️ 原则四: 0.618 兜底                                          │
│     └── 任何空隙都不能超过 3×ATR，否则强制填充                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 设计目标

1. **自适应性**: 水位密度随市场波动 (ATR) 自动调整
2. **稳定性**: 保持降序索引继承协议，确保持仓连续性
3. **简洁性**: 取消动态权重切换，回归统一极值提取
4. **抗稀疏**: 0.618 数学兜底，杜绝大间距盲区
5. **能量优先**: 过密时依据成交量裁剪，保留真实支撑

---

## 2. 四层级全量探测系统 (MTF v3.2.5)

### 2.1 四层级架构设计

系统采用「**战略 → 骨架 → 中继 → 战术**」的四层探测体系：

```
┌─────────────────────────────────────────────────────────────────┐
│                  四层级全量探测系统 (v3.2.5)                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  🏔️ L1 战略层 (1w/3d) - 长期边界锚定 [可选]                     │
│  ├── 职责: 锁定长期支撑/阻力边界                                 │
│  ├── 回溯: [8, 21, 55] 周期                                     │
│  ├── 权重: ⭐⭐⭐⭐⭐ (最高)                                      │
│  ├── 触发: 周线/3日线收盘时更新                                  │
│  └── 📌 可选: 数据不足时可禁用或用 3d 替代 1w                    │
│                                                                 │
│  🦴 L2 骨架层 (1d) - 主网格定义 ← 锚点基准                       │
│  ├── 职责: 定义主网格结构，提供 55x 锚点                         │
│  ├── 回溯: [13, 34, 55, 89] 周期                                │
│  ├── 权重: ⭐⭐⭐⭐ (高)                                          │
│  └── 触发: 日线收盘时更新                                        │
│                                                                 │
│  🎯 L3 中继层 (4h) - 主交易执行层                                │
│  ├── 职责: 核心网格布局，订单执行                                │
│  ├── 回溯: [8, 21, 55] 周期                                     │
│  ├── 权重: ⭐⭐⭐ (中)                                           │
│  └── 触发: 每 4 小时更新                                         │
│                                                                 │
│  ⚡ L4 战术层 (15m) - 种子池 (仅用于补全)                        │
│  ├── 职责: 作为 ATR 空隙补全的候选种子池                         │
│  ├── 回溯: [34, 55, 144] 周期                                   │
│  ├── 权重: ⭐⭐ (辅助)                                           │
│  └── 触发: 仅在补全逻辑中被召回                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 层级职责与参数

| 层级 | 周期 | 斐波那契回溯 | 核心职责 | 更新触发 | 可选 |
|:-----|:-----|:-------------|:---------|:---------|:-----|
| **L1 战略层** | 1w 或 3d | [8, 21, 55] | 锁定长期边界 | 周线/3日线收盘 | ✅ 可禁用 |
| **L2 骨架层** | 1d | [13, 34, 55, 89] | 定义主网格，提供锚点 | 日线收盘 | ❌ 必须 |
| **L3 中继层** | 4h | [8, 21, 55] | 核心交易执行 | 每 4 小时 | ❌ 必须 |
| **L4 战术层** | 15m | [34, 55, 144] | 种子池 (补全专用) | 按需召回 | ✅ 可禁用 |

> **📌 L1 层可选说明**：
> - 1w K 线数据量少 (1年仅52根)，分形提取可能不足
> - 部分交易所周线数据延迟高
> - 可用 **3d (3日线)** 替代：数据量 ×2.3，精度与周线相近
> - 或直接禁用，由 L2 (1d) 承担边界锚定职责

### 2.3 跨框架共振加成

当同一价位在多个时间框架中被识别为分形点时，触发**共振加成**：

```python
# 共振加成规则 (v3.2.5)
RESONANCE_COEFFICIENTS = {
    ("1w", "1d"):           1.5,   # 战略+骨架 = 强共振
    ("1w", "1d", "4h"):     2.0,   # 三框架共振 = 超强
    ("1d", "4h"):           1.5,   # 骨架+中继 = 强共振
    ("4h", "15m"):          1.2,   # 中继+战术 = 弱共振 (补全时生效)
}
```

**共振检测容差**: `RESONANCE_TOLERANCE = 0.003` (0.3%)

### 2.4 水位生成流程 (v3.2.5)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    V3.2.5 水位生成流程                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Phase 1: 全量分形提取                                              │
│  ├── 从 L1~L3 (1w/1d/4h) 提取所有分形点                            │
│  └── L4 (15m) 分形暂存为种子池                                      │
│                                                                     │
│  Phase 2: 合并 & 共振检测                                           │
│  ├── 合并相近价位 (0.3% 容差)                                       │
│  └── 标记共振水位，计算 M_mtf 系数                                   │
│                                                                     │
│  Phase 3: ⭐ ATR 空间审计 (核心)                                    │
│  ├── 密度审计: 间距 < 0.5×ATR → 能量优先裁剪                        │
│  └── 稀疏审计: 间距 > 3.0×ATR → 递归补全                            │
│                                                                     │
│  Phase 4: 评分计算                                                  │
│  └── Final_Score = S_base × W_vol × M_mtf × T_env                  │
│                                                                     │
│  Phase 5: 输出 (按价格降序)                                         │
│  └── 所有通过 ATR 审计的水位                                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. ATR 空间硬约束协议 (Hard Gap Constraint)

> **⚠️ 系统最高物理准则**: 所有生成的候选水位必须通过此审计器。

### 3.1 ATR 参数配置

```python
# ============================================
# ATR 空间硬约束配置
# ============================================

# ATR 计算参数
ATR_PERIOD = 14              # ATR 周期 (基于 4h K 线)
ATR_TIMEFRAME = "4h"         # ATR 计算时间框架

# 间距约束 (以 ATR 为单位)
GAP_MIN_ATR_RATIO = 0.5      # 最小间距 = 0.5 × ATR
GAP_MAX_ATR_RATIO = 3.0      # 最大间距 = 3.0 × ATR
```

### 3.2 密度审计门槛 (过密裁剪)

当相邻水位间距 `< 0.5 × ATR` 时，触发**能量优先裁剪**：

```
┌─────────────────────────────────────────────────────────────────────┐
│                    密度审计流程                                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  输入: 相邻水位 A (高) 和 B (低), 间距 = |A - B|                     │
│                                                                     │
│  if 间距 < 0.5 × ATR:                                               │
│      # 触发能量优先裁剪                                              │
│      compare W_vol(A) vs W_vol(B)                                   │
│                                                                     │
│      裁剪规则:                                                       │
│      ├── 若 A 是 POC/HVN, B 是 LVN → 保留 A, 剔除 B                 │
│      ├── 若 B 是 POC/HVN, A 是 LVN → 保留 B, 剔除 A                 │
│      └── 若两者同类型 → 保留评分更高者                               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**裁剪优先级**：
```
POC (控制点) > HVN (高能量节点) > NORMAL > LVN (真空区)
```

### 3.3 稀疏审计门槛 (空隙补全)

当相邻水位间距 `> 3.0 × ATR` 时，触发**递归补全**：

```
┌─────────────────────────────────────────────────────────────────────┐
│                    空隙补全流程 (优先级递减)                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  输入: 相邻水位 A (高) 和 B (低), 空隙 = |A - B| > 3.0 × ATR        │
│                                                                     │
│  Step 1: 🎯 战术种子召回 (最高优先)                                  │
│  └── 在 L4 (15m) 种子池中搜索位于 [B, A] 区间内的最高分分形点        │
│      if found:                                                      │
│          insert(fractal_price, score=fractal_score)                │
│          goto 递归检查                                               │
│                                                                     │
│  Step 2: 💪 能量锚点召回 (次优先)                                    │
│  └── 搜索 VPVR 中位于 [B, A] 区间内的 POC/HVN 节点                   │
│      if found:                                                      │
│          insert(hvn_price, score=volume_score)                     │
│          goto 递归检查                                               │
│                                                                     │
│  Step 3: 📐 斐波那契数学兜底 (终极保底) [可配置]                     │
│  └── 若以上皆无，在 B + (A - B) × FILL_RATIO 处强制插入              │
│      fill_price = B + (A - B) * config.fibonacci_fill_ratio        │
│      insert(fill_price, score=config.fibonacci_fill_score)         │
│                                                                     │
│  📌 FILL_RATIO 可配置: 0.618 (默认) / 0.5 / 0.382                   │
│      → 建议回测验证后选择最优比例                                    │
│                                                                     │
│  递归检查: 新插入的水位是否产生新的 > 3.0×ATR 空隙                   │
│  └── 若是，递归执行 Step 1~3                                         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.4 补全水位的特殊标记

```python
@dataclass
class FilledLevel:
    """补全水位数据结构"""
    price: float
    fill_type: str  # "tactical" | "vpvr" | "fibonacci"
    score: float    # 补全水位评分
    fill_ratio: float = 0.618    # 🆕 使用的填充比例 (用于回测分析)
    forced_active: bool = True   # 兜底补全强制激活
    
# 补全水位评分规则
FILL_SCORES = {
    "tactical": "原分形评分",      # 战术种子召回，保留原始评分
    "vpvr":     "成交量得分 × 0.8", # VPVR 召回，略低于分形
    "fibonacci": "config 配置值",   # 🆕 可配置，默认 35
}
```

> **📊 回测建议**：斐波那契兜底水位需要实战验证，建议：
> 1. 记录所有 `fill_type="fibonacci"` 的水位成交情况
> 2. 对比不同 `fill_ratio` (0.382 / 0.5 / 0.618) 的胜率
> 3. 根据回测结果调整配置

### 3.5 配置示例

```yaml
# config.yaml - ATR 空间硬约束配置
level_generation:
  atr_constraint:
    enabled: true                 # 是否启用 ATR 约束
    atr_period: 14                # ATR 计算周期
    atr_timeframe: "4h"           # ATR 计算时间框架
    
    # 间距约束 (以 ATR 倍数为单位)
    gap_min_atr_ratio: 0.5        # 最小间距 = 0.5 × ATR
    gap_max_atr_ratio: 3.0        # 最大间距 = 3.0 × ATR
    
    # 补全配置
    fill_priority:                # 补全优先级
      - "tactical"                # 1. 战术种子召回
      - "vpvr"                    # 2. VPVR 能量锚点
      - "fibonacci"               # 3. 斐波那契数学兜底
    
    # 🆕 斐波那契兜底配置 (可调优)
    fibonacci_fill_ratio: 0.618   # 兜底插入位置比例
                                  # 可选: 0.382 / 0.5 / 0.618 / 0.786
    fibonacci_fill_score: 35      # 兜底水位的强制评分 (影响仓位系数)
```

---

## 4. 水位评分机制 (Scoring Matrix v3.2.5)

> **设计理念**: 评分仅作为**活跃度判定**和**密度裁剪依据**，不再影响动态回溯。

### 4.1 评分公式 (简化版)

$$
\text{Final\_Score} = S_{base} \times W_{vol} \times M_{mtf} \times T_{env}
$$

### 4.2 各维度权重

| 维度 | 计算逻辑 | 权重范围 | 用途 |
|:-----|:---------|:---------|:-----|
| **基础分 $S_{base}$** | 时间框架权重 × 周期权重 (固定) | 12 ~ 120 | 层级优先级 |
| **成交量 $W_{vol}$** | POC (1.8x) / HVN (1.5x) / LVN (0.4x) | 0.4 ~ 1.8 | ⭐ **核心裁剪依据** |
| **共振 $M_{mtf}$** | 价格重合 (0.3% 容差) | 1.0 / 1.5 / 2.0 | 多框架验证 |
| **趋势 $T_{env}$** | EMA 144/169 隧道方向修正 | 0.9 ~ 1.1 | 方向性修正 |

### 4.3 基础分计算

```python
# 时间框架权重 (v3.2.5)
TIMEFRAME_WEIGHTS = {
    "1w":  2.0,   # L1 战略层 - 最高
    "1d":  1.5,   # L2 骨架层 - 高
    "4h":  1.0,   # L3 中继层 - 基准
    "15m": 0.6,   # L4 战术层 - 辅助 (补全时生效)
}

# 回溯周期权重 (通用)
PERIOD_SCORES = {
    144: 100,  # 超长周期 (15m 专用)
    89:  80,   # 长周期
    55:  60,   # 中长周期
    34:  45,   # 中周期
    21:  35,   # 短周期
    13:  25,   # 超短周期
    8:   15,   # 极短周期
}

def calculate_base_score(timeframe: str, period: int) -> float:
    """
    计算基础分
    
    示例:
    - 1w 55周期: 2.0 × 60 = 120 (最高)
    - 1d 89周期: 1.5 × 80 = 120
    - 4h 21周期: 1.0 × 35 = 35
    - 15m 144周期: 0.6 × 100 = 60 (补全时)
    """
    tf_weight = TIMEFRAME_WEIGHTS.get(timeframe, 1.0)
    period_score = PERIOD_SCORES.get(period, 20)
    return tf_weight * period_score
```

### 4.4 成交量权重 (v3.2.5 调整)

> **⭐ 核心裁剪依据**: 成交量权重直接决定密度裁剪时的保留优先级

| 成交量类型 | 权重 | 裁剪优先级 | 说明 |
|:-----------|:-----|:-----------|:-----|
| **POC (控制点)** | **1.8x** | 最高 | VPVR 最大成交量价位 |
| **HVN (高能量节点)** | **1.5x** | 高 | 成交量前 30% 分位 |
| **NORMAL** | **1.0x** | 中 | 普通区域 |
| **LVN (真空区)** | **0.4x** | 低 | 成交量后 30% 分位，优先裁剪 |

```python
VOLUME_WEIGHTS = {
    "POC":    1.8,   # 控制点，绝对保留
    "HVN":    1.5,   # 高能量节点
    "NORMAL": 1.0,   # 普通
    "LVN":    0.4,   # 真空区，优先裁剪
}
```

### 4.5 共振系数

```python
# 共振系数 (v3.2.5)
MTF_RESONANCE = {
    4: 2.5,   # 四框架共振 (1w+1d+4h+15m) - 极稀有
    3: 2.0,   # 三框架共振
    2: 1.5,   # 双框架共振
    1: 1.0,   # 单框架
}
```

### 4.6 趋势系数

```python
# 基于 EMA 144/169 隧道
TREND_COEFFICIENTS = {
    "BULLISH": {"support": 1.1, "resistance": 0.9},
    "BEARISH": {"support": 0.9, "resistance": 1.1},
    "NEUTRAL": {"support": 1.0, "resistance": 1.0},
}
```

---

## 5. 重构触发准则 (Rebuild Rules)

### 5.1 硬门槛：锚点位移触发

> **唯一重构触发条件**: L2 (1d) 骨架层的 55x 周期锚点价格位移 `|Δ| > 3%`

```python
# 锚点计算
ANCHOR_TIMEFRAME = "1d"      # 锚点时间框架
ANCHOR_PERIOD = 55           # 锚点回溯周期
ANCHOR_DRIFT_THRESHOLD = 0.03  # 3% 位移触发重构

def check_rebuild_trigger(old_anchor: float, new_anchor: float) -> bool:
    """
    检查是否触发网格重构
    
    Args:
        old_anchor: 上次 1d 55x 锚点价格
        new_anchor: 当前 1d 55x 锚点价格
    
    Returns:
        True 如果需要重构
    """
    drift = abs(new_anchor - old_anchor) / old_anchor
    return drift > ANCHOR_DRIFT_THRESHOLD
```

### 5.2 静默刷新：评分更新

> **4h/15m 变化不触发重构**，仅执行静默刷新

| 时间框架 | 触发动作 | 允许修改 | 禁止修改 |
|:---------|:---------|:---------|:---------|
| **1d** | 锚点检查 | 若 `|Δ| > 3%` → 触发重构 | - |
| **4h** | 静默刷新 | ✅ 评分, qty_multiplier | ❌ 挂单价格 |
| **15m** | 静默刷新 | ✅ 评分, qty_multiplier | ❌ 挂单价格 |

```python
async def on_kline_close(timeframe: str, klines: List[Dict]):
    """K 线收盘处理"""
    
    if timeframe == "1d":
        # 计算新锚点
        new_anchor = calculate_anchor(klines, period=55)
        
        if check_rebuild_trigger(old_anchor, new_anchor):
            await trigger_grid_rebuild()  # 全量重构
        else:
            await silent_score_refresh()  # 仅刷新评分
    
    elif timeframe in ["4h", "15m"]:
        # 静默刷新：仅更新评分和仓位系数
        await silent_score_refresh()
        # ❌ 严禁修改挂单价格
```

### 5.3 重构冷冻期

```python
REBUILD_COOLDOWN_SEC = 900  # 15 分钟内不重复重构
```

### 5.4 配置示例

```yaml
# config.yaml - 重构触发配置
level_generation:
  rebuild_triggers:
    anchor_timeframe: "1d"        # 锚点时间框架
    anchor_period: 55             # 锚点回溯周期
    anchor_drift_threshold: 0.03  # 3% 触发重构
    cooldown_sec: 900             # 冷冻期 15 分钟
    
  silent_refresh:
    enabled: true                 # 启用静默刷新
    timeframes: ["4h", "15m"]     # 静默刷新的时间框架
    allow_score_update: true      # 允许更新评分
    allow_qty_update: true        # 允许更新仓位系数
    forbid_price_change: true     # 禁止修改挂单价格

---

## 6. 核心管理协议：降序索引继承

> ⚠️ **这是系统稳定性的底线逻辑，严禁使用基于价格距离的模糊匹配**

### 6.1 核心不变量

```
INVARIANT: 水位数组必须始终保持价格降序排列
           levels[0].price > levels[1].price > ... > levels[n].price
```

### 6.2 1:1 索引继承规则

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

### 6.3 状态流转规则

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

### 6.4 继承目的

当 55x 极点漂移导致水位平移时，确保持仓逻辑在**逻辑层级（第几格）**上保持连续：

```
时刻 T:  [96000, 94000, 92000] ← fill_counter = [1, 2, 0]
                ↓
时刻 T+1: [96500, 94500, 92500] ← fill_counter = [1, 2, 0] (继承)

解释: 虽然价格都上移了 500，但「第二格」的持仓逻辑保持不变
```

---

## 7. 仓位自动缩放 (Qty Scaling)

### 7.1 缩放规则

根据 `Final_Score` 动态决定下单量（支持 MTF 超高分水位）：

| 评分区间 | 仓位系数 | 含义 |
|:---------|:---------|:-----|
| **Score ≥ 100** | **1.5x** | 🆕 MTF 共振级，超重仓 |
| **60 ≤ Score < 100** | **1.2x** | 强支撑重仓 |
| **30 ≤ Score < 60** | **1.0x** | 基准仓位 |
| **Score < 30** | **0x** | 不开新仓 (若为新水位则丢弃) |

```python
def calculate_qty_multiplier(score: float) -> float:
    """
    计算仓位系数
    
    支持 MTF 共振带来的超高分 (>100)
    """
    if score >= 100:
        return 1.5   # MTF 共振级
    elif score >= 60:
        return 1.2   # 强支撑
    elif score >= 30:
        return 1.0   # 基准
    else:
        return 0.0   # 不开仓
```

### 7.2 仓位计算示例

```python
base_qty = 0.001  # 基准 BTC 数量

# MTF 共振超高分水位 (score=308)
actual_qty = base_qty * 1.5  # = 0.0015 BTC (超重仓)

# 高分水位 (score=85)
actual_qty = base_qty * 1.2  # = 0.0012 BTC

# 低分水位 (score=25)
actual_qty = 0  # 不开仓，但保留水位供映射
```

---

## 8. 数据结构定义

### 8.1 水位评分数据

```python
@dataclass
class LevelScore:
    """水位评分详情 (MTF 增强版)"""
    # 基础信息
    base_score: float                    # 基础分 (来自周期×框架)
    source_timeframes: List[str]         # 🆕 来源时间框架 ["1d", "4h"]
    source_periods: List[int]            # 来源周期列表 [21, 55]
    
    # 修正系数
    volume_weight: float                 # 成交量权重
    volume_zone: str                     # "HVN" | "LVN" | "NORMAL"
    psychology_weight: float             # 心理位权重
    psychology_anchor: Optional[float]   # 吸附的心理位价格
    trend_coefficient: float             # 趋势系数
    trend_state: str                     # "BULLISH" | "BEARISH" | "NEUTRAL"
    
    # 🆕 MTF 共振
    mtf_coefficient: float = 1.0         # MTF 共振系数
    is_resonance: bool = False           # 是否为共振水位
    
    # 最终评分
    final_score: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "base_score": self.base_score,
            "source_timeframes": self.source_timeframes,
            "source_periods": self.source_periods,
            "volume_weight": self.volume_weight,
            "volume_zone": self.volume_zone,
            "psychology_weight": self.psychology_weight,
            "psychology_anchor": self.psychology_anchor,
            "trend_coefficient": self.trend_coefficient,
            "trend_state": self.trend_state,
            "mtf_coefficient": self.mtf_coefficient,
            "is_resonance": self.is_resonance,
            "final_score": self.final_score,
        }
```

### 8.2 扩展 GridLevelState

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

### 8.3 分形点数据

```python
@dataclass
class FractalPoint:
    """分形点 (MTF 增强版)"""
    price: float
    timestamp: int
    type: str                   # "HIGH" | "LOW"
    timeframe: str              # 🆕 时间框架 "1d" | "4h" | "15m"
    period: int                 # 回溯周期 8/13/21/34/55/89
    kline_index: int            # K 线索引
    
@dataclass
class VPVRData:
    """成交量分布数据"""
    poc_price: float            # 控制价 (Point of Control)
    hvn_zones: List[Tuple[float, float]]  # 高能量区间列表
    lvn_zones: List[Tuple[float, float]]  # 真空区间列表
    total_volume: float


@dataclass
class MTFLevelCandidate:
    """🆕 MTF 水位候选"""
    price: float
    source_fractals: List[FractalPoint]  # 来源分形点列表
    source_timeframes: List[str]          # 来源时间框架
    is_resonance: bool                    # 是否共振
    merged_price: float                   # 合并后价格 (若共振则取高框架)
```

---

## 9. 模块设计与实现

### 9.1 模块架构

```
src/key_level_grid/
├── level_calculator.py     # 🆕 水位计算引擎
│   ├── FractalExtractor    # 分形提取器
│   ├── VPVRAnalyzer        # 成交量分析器
│   ├── PsychologyMatcher   # 心理位匹配器
│   ├── LevelScorer         # 评分计算器
│   └── MTFMerger           # 🆕 多时间框架融合器
│
├── mtf_kline_feed.py       # 🆕 多时间框架 K 线管理
│   ├── get_klines(timeframe, lookback)
│   ├── subscribe_multiple_timeframes()
│   └── is_synced()         # 🆕 一致性锁检查
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

### 9.2 🆕 MTFKlineFeed 与一致性锁 (Sync Lock)

> **风险点**: Gate.io 等交易所不同周期的 K 线闭合时间点在毫秒级上是不完全对齐的。如果日线还没更新（由于延迟），但 4h 已经更新了，系统可能会计算出基于旧趋势和新结构的错误共振。

#### 9.2.1 K 线同步检查

```python
from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass
class KlineSyncStatus:
    """K 线同步状态"""
    timeframe: str
    last_close_time: int      # 最新闭合 K 线的 close_time (ms)
    expected_close_time: int  # 预期的 close_time (ms)
    is_stale: bool            # 是否过期
    lag_seconds: int          # 延迟秒数

class MTFKlineFeed:
    """
    多时间框架 K 线管理器
    
    核心职责:
    1. 管理 1d/4h/15m 三个时间框架的 K 线订阅
    2. 提供一致性锁检查 (is_synced)
    3. 缓存与增量更新
    """
    
    # 各时间框架的步长 (毫秒)
    INTERVAL_MS = {
        "1d":  24 * 60 * 60 * 1000,
        "4h":  4 * 60 * 60 * 1000,
        "15m": 15 * 60 * 1000,
    }
    
    # 允许的最大延迟 (秒)
    MAX_LAG = {
        "1d":  300,   # 日线允许 5 分钟延迟
        "4h":  60,    # 4h 允许 1 分钟延迟
        "15m": 30,    # 15m 允许 30 秒延迟
    }
    
    def __init__(self, kline_feed):
        self.kline_feed = kline_feed
        self._cache: Dict[str, List[Dict]] = {}
        self._last_close: Dict[str, int] = {}
    
    def is_synced(self, reference_time: Optional[int] = None) -> bool:
        """
        检查三个时间框架的 K 线是否处于一致状态
        
        规则:
        1. 每个时间框架的"最新闭合 K 线"必须属于同一个时间逻辑轴
        2. 任何框架的延迟超过 MAX_LAG 则返回 False
        
        Args:
            reference_time: 参考时间戳 (ms)，默认为当前时间
        
        Returns:
            True if all timeframes are synced
        """
        now = reference_time or int(time.time() * 1000)
        
        for tf in ["1d", "4h", "15m"]:
            status = self._check_sync_status(tf, now)
            if status.is_stale:
                logger.warning(
                    f"⚠️ {tf} K 线未同步: "
                    f"last_close={status.last_close_time}, "
                    f"expected={status.expected_close_time}, "
                    f"lag={status.lag_seconds}s"
                )
                return False
        
        return True
    
    def _check_sync_status(self, timeframe: str, now: int) -> KlineSyncStatus:
        """检查单个时间框架的同步状态"""
        interval_ms = self.INTERVAL_MS[timeframe]
        max_lag = self.MAX_LAG[timeframe]
        
        last_close = self._last_close.get(timeframe, 0)
        
        # 计算预期的最新闭合时间
        expected_close = (now // interval_ms) * interval_ms
        
        # 计算延迟
        lag_ms = expected_close - last_close
        lag_seconds = lag_ms // 1000
        
        return KlineSyncStatus(
            timeframe=timeframe,
            last_close_time=last_close,
            expected_close_time=expected_close,
            is_stale=(lag_seconds > max_lag),
            lag_seconds=lag_seconds,
        )
    
    def get_sync_report(self) -> Dict[str, KlineSyncStatus]:
        """获取所有时间框架的同步报告"""
        now = int(time.time() * 1000)
        return {
            tf: self._check_sync_status(tf, now)
            for tf in ["1d", "4h", "15m"]
        }
    
    async def get_klines_if_synced(
        self,
        lookback: Dict[str, int],
    ) -> Optional[Dict[str, List[Dict]]]:
        """
        仅在同步状态下返回 K 线数据
        
        Args:
            lookback: {"1d": 89, "4h": 55, "15m": 55}
        
        Returns:
            K 线数据字典，或 None (如果未同步)
        """
        if not self.is_synced():
            logger.warning("K 线未同步，跳过本次计算")
            return None
        
        result = {}
        for tf, count in lookback.items():
            result[tf] = await self.kline_feed.get_klines(
                interval=tf, limit=count
            )
            if result[tf]:
                self._last_close[tf] = result[tf][-1]["close_time"]
        
        return result
```

#### 9.2.2 LevelCalculator 集成同步检查

```python
class LevelCalculator:
    def __init__(self, mtf_feed: MTFKlineFeed, ...):
        self.mtf_feed = mtf_feed
    
    async def generate_target_levels(
        self,
        current_price: float,
        role: str = "support",
        max_levels: int = 10,
    ) -> Optional[List[Tuple[float, LevelScore]]]:
        """
        生成目标水位 (带同步检查)
        
        Returns:
            水位列表，或 None (如果 K 线未同步)
        """
        # 🆕 一致性锁检查
        klines_by_tf = await self.mtf_feed.get_klines_if_synced(
            lookback={"1d": 89, "4h": 55, "15m": 55}
        )
        
        if klines_by_tf is None:
            # K 线未同步，本次跳过
            return None
        
        # 正常流程...
        return self._compute_levels(klines_by_tf, current_price, role, max_levels)
```

#### 9.2.3 同步失败的处理策略

| 场景 | 处理方式 |
|:-----|:---------|
| **日线延迟** | 跳过本次计算，保持现有水位，等待下一个 4h 周期 |
| **4h 延迟** | 跳过本次计算，记录日志，不触发告警 |
| **15m 延迟** | 可降级为只使用 1d+4h 计算，15m 权重置 0 |
| **连续多次不同步** | 超过 3 次连续不同步，推送 Telegram 警告 |

```python
async def handle_sync_failure(self, sync_report: Dict[str, KlineSyncStatus]):
    """处理同步失败"""
    stale_tfs = [tf for tf, s in sync_report.items() if s.is_stale]
    
    if "1d" in stale_tfs:
        # 日线不同步，跳过
        logger.warning("日线 K 线未同步，跳过本次水位计算")
        return
    
    if "15m" in stale_tfs and "4h" not in stale_tfs:
        # 仅 15m 不同步，可降级计算
        logger.info("15m 未同步，降级为 1d+4h 计算")
        return await self._compute_without_tactical()
    
    # 其他情况跳过
    logger.warning(f"K 线不同步: {stale_tfs}，跳过本次计算")
```

---

### 9.3 LevelCalculator 接口设计 (MTF 增强版)

```python
class LevelCalculator:
    """
    水位计算引擎 (MTF 增强版)
    
    职责:
    1. 从多时间框架 K 线数据提取分形点
    2. 检测跨框架共振
    3. 获取 VPVR 成交量分布
    4. 计算综合评分 (含 MTF 加成)
    5. 输出排序后的目标水位列表
    """
    
    def __init__(
        self,
        # 趋势层 (1d)
        trend_interval: str = "1d",
        trend_fib_lookback: List[int] = [21, 55, 89],
        # 战略层 (4h) - 主周期
        main_interval: str = "4h",
        main_fib_lookback: List[int] = [8, 21, 55],
        # 战术层 (15m)
        tactical_interval: str = "15m",
        tactical_fib_lookback: List[int] = [13, 34, 55],
        # 趋势指标
        ema_fast: int = 144,
        ema_slow: int = 169,
        # 共振检测
        resonance_tolerance: float = 0.003,
    ):
        self.timeframes = {
            "1d":  (trend_interval, trend_fib_lookback),
            "4h":  (main_interval, main_fib_lookback),
            "15m": (tactical_interval, tactical_fib_lookback),
        }
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.resonance_tolerance = resonance_tolerance
    
    async def generate_target_levels(
        self,
        klines_by_tf: Dict[str, List[Dict]],  # 🆕 多时间框架 K 线
        current_price: float,
        role: str = "support",
        max_levels: int = 10,
    ) -> List[Tuple[float, LevelScore]]:
        """
        生成目标水位列表 (MTF 版)
        
        Args:
            klines_by_tf: {"1d": [...], "4h": [...], "15m": [...]}
            current_price: 当前价格
            role: "support" | "resistance"
            max_levels: 最大水位数
        
        Returns:
            [(price, score), ...] 按价格降序排列
        """
        # 1. 从各时间框架提取分形点
        all_fractals: List[FractalPoint] = []
        for tf, (interval, lookback) in self.timeframes.items():
            klines = klines_by_tf.get(tf, [])
            if klines:
                fractals = self._extract_fractals(klines, tf, lookback)
                all_fractals.extend(fractals)
        
        # 2. 检测跨框架共振，合并候选
        candidates = self._detect_resonance_and_merge(all_fractals)
        
        # 3. 获取 VPVR 数据 (使用 4h 主周期)
        vpvr = self._analyze_vpvr(klines_by_tf.get("4h", []))
        
        # 4. 计算趋势状态
        trend = self._determine_trend(
            klines_by_tf.get("4h", []), current_price
        )
        
        # 5. 对每个候选水位评分
        scored_levels = []
        for candidate in candidates:
            score = self._calculate_mtf_score(candidate, vpvr, trend, role)
            if score.final_score >= 30:  # 过滤低分
                price = self._apply_psychology_snap(
                    candidate.merged_price,
                    klines_by_tf.get("1d", [])  # 使用日线计算 Fib
                )
                scored_levels.append((price, score))
        
        # 6. 去重、排序、截断
        return self._finalize_levels(scored_levels, max_levels)
    
    def _extract_fractals(
        self,
        klines: List[Dict],
        timeframe: str,
        lookback: List[int],
    ) -> List[FractalPoint]:
        """提取指定时间框架的多周期分形点"""
        ...
    
    def _detect_resonance_and_merge(
        self,
        fractals: List[FractalPoint],
    ) -> List[MTFLevelCandidate]:
        """
        检测跨框架共振并合并
        
        规则: 价格差异 < tolerance 视为共振
        合并时以高时间框架价格为准
        """
        ...
    
    def _calculate_mtf_score(
        self,
        candidate: MTFLevelCandidate,
        vpvr: VPVRData,
        trend: str,
        role: str,
    ) -> LevelScore:
        """
        计算 MTF 综合评分
        
        Final = S_base × W_vol × W_psy × T_env × M_mtf
        """
        # 基础分: 取最高时间框架×最长周期
        base_score = self._calc_base_score(candidate)
        
        # 各修正系数
        vol_weight = self._calc_volume_weight(candidate.merged_price, vpvr)
        psy_weight = self._calc_psychology_weight(candidate.merged_price)
        trend_coef = self._calc_trend_coefficient(trend, role)
        
        # MTF 共振系数
        mtf_coef = self._calc_mtf_coefficient(candidate.source_timeframes)
        
        final_score = (
            base_score * vol_weight * psy_weight * trend_coef * mtf_coef
        )
        
        return LevelScore(
            base_score=base_score,
            source_timeframes=candidate.source_timeframes,
            source_periods=[f.period for f in candidate.source_fractals],
            volume_weight=vol_weight,
            volume_zone=self._get_volume_zone(candidate.merged_price, vpvr),
            psychology_weight=psy_weight,
            psychology_anchor=None,  # 稍后在 snap 时填充
            trend_coefficient=trend_coef,
            trend_state=trend,
            mtf_coefficient=mtf_coef,
            is_resonance=candidate.is_resonance,
            final_score=final_score,
        )
```

### 9.4 IndexInheritor 接口设计

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
        """支持 MTF 超高分"""
        if score >= 100:
            return 1.5   # MTF 共振级
        elif score >= 60:
            return 1.2
        elif score >= 30:
            return 1.0
        return 0.0
```

### 9.5 🆕 重构时的订单处理事务性 (Atomicity)

> **风险点**: `IndexInheritor` 执行继承时涉及"先撤单、后挂单"。在网络波动或 API 延时期间，如果价格剧烈波动，可能导致旧单已撤、新单未挂，系统出现"逻辑裸奔"。

#### 9.5.1 原子性执行流程

```
┌─────────────────────────────────────────────────────────────────┐
│                  订单重构原子性执行流程                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Phase 1: 准备阶段 (不修改任何状态)                              │
│  ├── 1.1 计算 InheritanceResult                                │
│  ├── 1.2 生成撤单列表 orders_to_cancel                         │
│  ├── 1.3 生成挂单列表 orders_to_place                          │
│  └── 1.4 持久化 pending_migration.json (事务日志)              │
│                                                                 │
│  Phase 2: 撤单阶段 (可回滚)                                     │
│  ├── 2.1 批量撤单 cancel_orders(orders_to_cancel)              │
│  ├── 2.2 等待交易所确认 (最多 30s 超时)                         │
│  ├── 2.3 ❌ 任一撤单失败 → 进入 ALARM 模式，中止流程            │
│  └── 2.4 ✅ 全部成功 → 更新本地状态为 "撤单完成"                │
│                                                                 │
│  Phase 3: 挂单阶段 (部分失败可重试)                              │
│  ├── 3.1 批量挂单 place_orders(orders_to_place)                │
│  ├── 3.2 等待交易所确认                                         │
│  ├── 3.3 ⚠️ 部分挂单失败 → 记录失败订单，进入 RETRY 模式        │
│  └── 3.4 ✅ 全部成功 → 进入 Phase 4                             │
│                                                                 │
│  Phase 4: 状态同步阶段 (关键！)                                  │
│  ├── 4.1 从交易所获取最新订单状态                               │
│  ├── 4.2 比对并更新本地 state.json                              │
│  ├── 4.3 删除 pending_migration.json                           │
│  └── 4.4 发送 Telegram 通知 (成功/部分成功)                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 9.5.2 原子性检查实现

```python
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional

class RebuildPhase(str, Enum):
    """重构阶段"""
    PENDING = "PENDING"           # 准备中
    CANCELLING = "CANCELLING"     # 撤单中
    PLACING = "PLACING"           # 挂单中
    SYNCING = "SYNCING"           # 状态同步中
    COMPLETED = "COMPLETED"       # 完成
    ALARM = "ALARM"               # 告警模式
    RETRY = "RETRY"               # 重试模式

@dataclass
class PendingMigration:
    """事务日志 (持久化到 pending_migration.json)"""
    phase: RebuildPhase
    started_at: int
    orders_to_cancel: List[str]
    orders_cancelled: List[str]
    orders_to_place: List[dict]
    orders_placed: List[str]
    failed_orders: List[dict]
    error_message: Optional[str] = None

class AtomicRebuildExecutor:
    """原子性重构执行器"""
    
    def __init__(self, executor, notifier, state_path: str):
        self.executor = executor
        self.notifier = notifier
        self.state_path = state_path
        self.migration_path = state_path.replace('.json', '_migration.json')
    
    async def execute_rebuild(
        self,
        inheritance_result: InheritanceResult,
    ) -> bool:
        """
        执行原子性重构
        
        Returns:
            True if successful, False if entered ALARM mode
        """
        migration = PendingMigration(
            phase=RebuildPhase.PENDING,
            started_at=int(time.time()),
            orders_to_cancel=[o for o in inheritance_result.orders_to_cancel],
            orders_cancelled=[],
            orders_to_place=[o.to_dict() for o in inheritance_result.orders_to_place],
            orders_placed=[],
            failed_orders=[],
        )
        
        # Phase 1: 持久化事务日志
        self._save_migration(migration)
        
        # Phase 2: 撤单
        migration.phase = RebuildPhase.CANCELLING
        self._save_migration(migration)
        
        cancel_success = await self._cancel_orders(migration)
        if not cancel_success:
            migration.phase = RebuildPhase.ALARM
            migration.error_message = "撤单失败，系统进入 ALARM 模式"
            self._save_migration(migration)
            await self.notifier.send_alarm(
                f"🚨 网格重构撤单失败!\n"
                f"待撤: {len(migration.orders_to_cancel)}\n"
                f"已撤: {len(migration.orders_cancelled)}\n"
                f"请立即人工介入!"
            )
            return False
        
        # Phase 3: 挂单
        migration.phase = RebuildPhase.PLACING
        self._save_migration(migration)
        
        place_success = await self._place_orders(migration)
        if not place_success:
            migration.phase = RebuildPhase.RETRY
            self._save_migration(migration)
            await self.notifier.send_warning(
                f"⚠️ 网格重构部分挂单失败\n"
                f"成功: {len(migration.orders_placed)}\n"
                f"失败: {len(migration.failed_orders)}\n"
                f"系统将自动重试"
            )
            # 启动重试逻辑
            await self._retry_failed_orders(migration)
        
        # Phase 4: 状态同步
        migration.phase = RebuildPhase.SYNCING
        self._save_migration(migration)
        
        await self._sync_state_from_exchange()
        
        # 完成
        migration.phase = RebuildPhase.COMPLETED
        self._delete_migration()
        
        return True
    
    async def _cancel_orders(self, migration: PendingMigration) -> bool:
        """
        批量撤单 (全部成功才返回 True)
        
        关键: 撤单失败绝对不能进行新挂单
        """
        for order_id in migration.orders_to_cancel:
            try:
                await self.executor.cancel_order(order_id)
                migration.orders_cancelled.append(order_id)
                self._save_migration(migration)
            except Exception as e:
                logger.error(f"撤单失败 {order_id}: {e}")
                return False
        return True
    
    async def _place_orders(self, migration: PendingMigration) -> bool:
        """
        批量挂单 (允许部分失败)
        """
        all_success = True
        for order_req in migration.orders_to_place:
            try:
                order_id = await self.executor.place_order(
                    side=order_req["side"],
                    price=order_req["price"],
                    qty=order_req["qty"],
                )
                migration.orders_placed.append(order_id)
            except Exception as e:
                logger.error(f"挂单失败: {e}")
                order_req["error"] = str(e)
                migration.failed_orders.append(order_req)
                all_success = False
            self._save_migration(migration)
        return all_success
    
    async def _sync_state_from_exchange(self):
        """
        关键: state.json 的更新必须在交易所确认之后
        """
        exchange_orders = await self.executor.get_open_orders()
        # 比对并更新本地状态
        # ...
    
    def recover_from_crash(self) -> Optional[PendingMigration]:
        """
        冷启动时检查是否有未完成的迁移
        
        如果存在 pending_migration.json，根据 phase 决定恢复策略
        """
        if not os.path.exists(self.migration_path):
            return None
        
        migration = self._load_migration()
        
        if migration.phase == RebuildPhase.ALARM:
            logger.critical("发现未处理的 ALARM 状态，需人工介入")
            return migration
        
        if migration.phase in [RebuildPhase.CANCELLING, RebuildPhase.PLACING]:
            logger.warning(f"发现中断的迁移 (phase={migration.phase})，尝试恢复")
            # 从交易所同步最新状态，决定继续还是回滚
            return migration
        
        return None
```

#### 9.5.3 ALARM 模式行为

| 状态 | 触发条件 | 系统行为 |
|:-----|:---------|:---------|
| **ALARM** | 撤单失败 | 立即停止所有操作，推送紧急通知，等待人工介入 |
| **RETRY** | 挂单部分失败 | 自动重试失败订单（最多 3 次），每次间隔 5 秒 |
| **SYNCING** | 正常流程 | 从交易所获取最新状态，更新 state.json |

#### 9.5.4 状态同步规则

> **关键原则**: 本地 `state.json` 的更新必须发生在**交易所确认订单回执之后**。

```python
# ❌ 错误做法：先更新本地，再发送交易所请求
state.support_levels_state = new_levels
save_state(state)
await executor.cancel_orders(...)  # 可能失败！

# ✅ 正确做法：先确认交易所，再更新本地
cancel_result = await executor.cancel_orders(...)
if cancel_result.all_success:
    state.support_levels_state = new_levels
    save_state(state)
```

---

## 10. 配置参数清单 (v3.2.5)

### 10.1 核心配置示例

```yaml
# configs/config.yaml (V3.2.5)

grid:
  level_generation:
    enabled: true                     # 启用动态水位生成
    max_levels: 15                    # 最大水位数
    min_score: 30                     # 最低评分阈值
    
    # ===== 四层级时间框架配置 =====
    timeframes:
      # L1 战略层 (1w/3d) - 长期边界 [可选]
      l1_strategy:
        enabled: true                 # 🆕 可选：数据不足时可禁用
        interval: "1w"                # 可选: "1w" / "3d"
        use_3d_fallback: false        # 🆕 若 1w 数据不足，自动降级为 3d
        fib_lookback: [8, 21, 55]
        
      # L2 骨架层 (1d) - 主网格 & 锚点基准 [必须]
      l2_skeleton:
        interval: "1d"
        fib_lookback: [13, 34, 55, 89]
        enabled: true                 # 必须启用
        
      # L3 中继层 (4h) - 主交易执行 [必须]
      l3_relay:
        interval: "4h"
        fib_lookback: [8, 21, 55]
        enabled: true                 # 必须启用
        
      # L4 战术层 (15m) - 种子池 (仅用于补全) [可选]
      l4_tactical:
        enabled: true                 # 🆕 可选：可禁用以简化
        interval: "15m"
        fib_lookback: [34, 55, 144]
        triggers_rebuild: false       # 不触发重构，仅用于补全
    
    # ===== ATR 空间硬约束配置 =====
    atr_constraint:
      enabled: true                   # 启用 ATR 约束
      atr_period: 14                  # ATR 计算周期
      atr_timeframe: "4h"             # ATR 基准时间框架
      
      # 间距约束 (以 ATR 倍数为单位)
      gap_min_atr_ratio: 0.5          # 最小间距 = 0.5 × ATR
      gap_max_atr_ratio: 3.0          # 最大间距 = 3.0 × ATR
      
      # 补全配置
      fill_priority:                  # 补全优先级
        - "tactical"                  # 1. 战术种子召回
        - "vpvr"                      # 2. VPVR 能量锚点
        - "fibonacci"                 # 3. 斐波那契数学兜底
      
      # 🆕 斐波那契兜底配置 (可调优，建议回测验证)
      fibonacci_fill_ratio: 0.618     # 兜底插入位置比例
                                      # 可选: 0.382 / 0.5 / 0.618 / 0.786
      fibonacci_fill_score: 35        # 兜底水位的强制评分
      fibonacci_enabled: true         # 🆕 可完全禁用斐波那契兜底
    
    # ===== 重构触发配置 =====
    rebuild_triggers:
      anchor_timeframe: "1d"          # 锚点时间框架 (L2 骨架层)
      anchor_period: 55               # 锚点回溯周期
      anchor_drift_threshold: 0.03    # 3% 触发重构 (硬门槛)
      cooldown_sec: 900               # 冷冻期 15 分钟
    
    # ===== 静默刷新配置 =====
    silent_refresh:
      enabled: true                   # 启用静默刷新
      timeframes: ["4h", "15m"]       # 静默刷新的时间框架
      allow_score_update: true        # 允许更新评分
      allow_qty_update: true          # 允许更新仓位系数
      forbid_price_change: true       # ❌ 禁止修改挂单价格
    
    # 趋势指标
    ema_fast: 144
    ema_slow: 169
    
  # ===== 评分矩阵配置 (v3.2.5) =====
  level_scoring:
    # 时间框架权重
    timeframe_weights:
      "1w":  2.0                      # L1 战略层 - 最高
      "3d":  1.8                      # 🆕 L1 替代: 3日线 (1w 的降级选项)
      "1d":  1.5                      # L2 骨架层 - 高
      "4h":  1.0                      # L3 中继层 - 基准
      "15m": 0.6                      # L4 战术层 - 辅助
      
    # 周期基础分
    period_scores:
      144: 100                        # 超长周期 (15m 专用)
      89: 80
      55: 60
      34: 45
      21: 35
      13: 25
      8: 15
      
    # ⭐ 成交量权重 (核心裁剪依据)
    volume_weights:
      POC: 1.8                        # 控制点，绝对保留
      HVN: 1.5                        # 高能量节点
      NORMAL: 1.0                     # 普通
      LVN: 0.4                        # 真空区，优先裁剪
      
    # 共振系数
    mtf_resonance:
      4: 2.5                          # 四框架共振 (极稀有)
      3: 2.0                          # 三框架共振
      2: 1.5                          # 双框架共振
      1: 1.0                          # 单框架
      
    # 趋势系数
    trend_coefficients:
      BULLISH:
        support: 1.1
        resistance: 0.9
      BEARISH:
        support: 0.9
        resistance: 1.1
      NEUTRAL:
        support: 1.0
        resistance: 1.0
```

### 10.2 与现有系统的集成

| 组件 | 集成方式 |
|:-----|:---------|
| `GridPositionManager` | 调用 `LevelCalculator` 生成水位 |
| `KeyLevelGridStrategy` | 在 `_update_cycle` 中触发水位更新 |
| `level_manager.py` | 复用现有继承逻辑 |
| `position.py` | 扩展 `GridLevelState` 添加评分字段 |
| 🆕 `mtf_kline_feed.py` | 管理四层时间框架 K 线订阅 |
| 🆕 `atr_gap_auditor.py` | ATR 空间硬约束审计器 |

### 10.3 向后兼容

- **state.json**: 新字段 (`score`, `qty_multiplier`, `fill_type`) 可选，旧版自动默认
- **继承逻辑**: 完全复用 v2.0 的 `inherit_levels_by_index()`
- **订单执行**: 无变化，仅下单数量根据 `qty_multiplier` 调整

---

## 11. 开发执行计划 (v3.2.5)

### 11.1 阶段划分

| 阶段 | 任务 | 优先级 | 依赖 |
|:-----|:-----|:-------|:-----|
| **Phase 0** | 扩展 `MTFKlineFeed` 支持四层级 | P0 | 无 |
| **Phase 1** | 实现 `FractalExtractor` (四层级) | P0 | Phase 0 |
| **Phase 2** | 🆕 实现 `ATRGapAuditor` | **P0** | Phase 1 |
| **Phase 3** | 实现 `MTFMerger` (共振检测) | P0 | Phase 1 |
| **Phase 4** | 实现 `VPVRAnalyzer` | P1 | Phase 1 |
| **Phase 5** | 实现 `LevelScorer` (v3.2.5) | P0 | Phase 2, 3, 4 |
| **Phase 6** | 实现 `IndexInheritor` 封装 | P0 | Phase 5 |
| **Phase 7** | 集成到 `GridPositionManager` | P0 | Phase 6 |
| **Phase 8** | 配置与 UI 展示 | P2 | Phase 7 |

### 11.2 详细任务清单

#### Phase 0: MTFKlineFeed (四层级扩展)

```
P0.1: 扩展支持 1w (周线) 时间框架
P0.2: 实现四层级 K 线订阅 (1w/1d/4h/15m)
P0.3: 实现 is_synced() 一致性锁
P0.4: 编写单元测试
```

#### Phase 1: FractalExtractor (四层级)

```
P1.1: 实现 L1 战略层 (1w) 分形提取: [8, 21, 55]
P1.2: 实现 L2 骨架层 (1d) 分形提取: [13, 34, 55, 89]
P1.3: 实现 L3 中继层 (4h) 分形提取: [8, 21, 55]
P1.4: 实现 L4 战术层 (15m) 分形提取: [34, 55, 144]
P1.5: 编写单元测试
```

#### Phase 2: ATRGapAuditor (🆕 核心)

```
P2.1: 实现 ATR 计算 (基于 4h 数据)
P2.2: 实现密度审计: 间距 < 0.5×ATR → 能量优先裁剪
P2.3: 实现稀疏审计: 间距 > 3.0×ATR → 递归补全
P2.4: 实现补全优先级: 战术种子 → VPVR → 0.618 兜底
P2.5: 实现 FilledLevel 数据结构
P2.6: 编写单元测试 (覆盖各种边界情况)
```

#### Phase 3: MTFMerger

```
P3.1: 实现跨时间框架价格共振检测 (0.3% 容差)
P3.2: 实现共振水位合并逻辑 (以高框架为准)
P3.3: 实现 MTFLevelCandidate 输出
P3.4: 编写单元测试
```

#### Phase 4: VPVRAnalyzer

```
P4.1: 实现价格区间划分
P4.2: 实现成交量累积计算
P4.3: 实现 POC/HVN/LVN 区域识别
P4.4: 提供成交量类型查询接口 (供密度裁剪使用)
P4.5: 编写单元测试
```

#### Phase 5: LevelScorer (v3.2.5)

```
P5.1: 实现四层级时间框架权重 (1w:2.0, 1d:1.5, 4h:1.0, 15m:0.6)
P5.2: 实现成交量权重 (POC:1.8, HVN:1.5, LVN:0.4)
P5.3: 实现共振系数 (1~4 框架)
P5.4: 实现趋势系数 (EMA 隧道)
P5.5: 实现最终评分公式
P5.6: 编写单元测试
```

#### Phase 6: IndexInheritor 封装

```
P6.1: 封装 inherit_levels_by_index 调用
P6.2: 实现评分附加逻辑
P6.3: 实现 qty_multiplier 计算
P6.4: 实现补全水位的状态管理 (FilledLevel)
P6.5: 编写集成测试
```

#### Phase 6: 系统集成

```
P6.1: 扩展 GridLevelState 数据结构 (添加 MTF 字段)
P6.2: 修改 GridPositionManager.create_grid
P6.3: 实现 MTF 触发检查逻辑 (分层更新)
P6.4: 添加冷冻期约束 (按时间框架)
P6.5: 编写端到端测试
```

### 11.3 Cursor / AI 执行指令

1. **实现 `MTFKlineFeed`**: 负责管理多时间框架 K 线订阅与缓存
2. **实现 `LevelCalculator`**: 负责 K 线回溯、分形提取、MTF 共振检测、VPVR 映射及评分计算
3. **实现 `IndexInheritor`**: 负责执行降序排列后的 1:1 状态迁移，输出 `InheritanceResult`
4. **重构 `PositionManager`**: 根据 `ACTIVE/RETIRED` 状态执行分流对账逻辑

---

## 附录 A: 评分计算参考实现

```python
def calculate_final_score(
    fractal: FractalPoint,
    vpvr: VPVRData,
    trend: str,
    role: str,
    fib_range: Tuple[float, float],
    source_timeframes: Optional[List[str]] = None,
) -> LevelScore:
    """
    计算最终评分
    
    公式: Final = S_base × W_volume × W_psychology × T_env × M_mtf
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
    
    # 5. MTF 共振系数 (默认单框架 = 1.0)
    mtf_coef = 1.0
    if source_timeframes:
        mtf_coef = calculate_mtf_coefficient(source_timeframes)

    # 6. 最终评分
    final_score = (
        base_score * volume_weight * psychology_weight * trend_coef * mtf_coef
    )
    
    return LevelScore(
        base_score=base_score,
        source_timeframes=source_timeframes or ["4h"],
        source_periods=[fractal.period],
        volume_weight=volume_weight,
        volume_zone=volume_zone,
        psychology_weight=psychology_weight,
        psychology_anchor=psychology_anchor,
        trend_coefficient=trend_coef,
        trend_state=trend,
        mtf_coefficient=mtf_coef,
        is_resonance=mtf_coef > 1.0,
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
