# 📑 KeyLevelGrid V3.0 水位生成与管理核心规格说明书

> **版本**: v3.1.0  
> **状态**: Draft  
> **创建日期**: 2026-01-17  
> **更新日期**: 2026-01-17  
> **基于**: SPEC_LEVEL_LIFECYCLE.md v2.0.0

---

## 目录

1. [核心设计哲学](#1-核心设计哲学)
2. [多时间框架体系 (MTF)](#2-多时间框架体系-mtf)
3. [环境参数与计算引擎](#3-环境参数与计算引擎)
4. [水位评分机制](#4-水位评分机制-scoring-matrix)
5. [核心管理协议：降序索引继承](#5-核心管理协议降序索引继承)
6. [仓位自动缩放](#6-仓位自动缩放-qty-scaling)
7. [更新触发规则](#7-更新触发规则-event-triggers)
8. [数据结构定义](#8-数据结构定义)
9. [模块设计与实现](#9-模块设计与实现)
10. [与现有系统的集成](#10-与现有系统的集成)
11. [开发执行计划](#11-开发执行计划)

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
5. **🆕 层次性**: 多时间框架协同，战略/战术/趋势三位一体

---

## 2. 多时间框架体系 (MTF)

### 2.1 三层时间框架设计

系统采用「**趋势位 → 战略位 → 战术位**」的层级结构：

```
┌─────────────────────────────────────────────────────────────────┐
│                     多时间框架协同体系                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  📊 趋势层 (1d)                                                 │
│  ├── 职责: 定义大级别趋势方向与极限边界                           │
│  ├── 回溯: 21/55/89 根日线                                      │
│  ├── 权重: ⭐⭐⭐⭐⭐ (最高优先级)                                │
│  └── 触发: 日线收盘时更新                                        │
│                                                                 │
│  🎯 战略层 (4h) ← 主周期                                        │
│  ├── 职责: 核心网格布局，主要交易执行层                           │
│  ├── 回溯: 8/21/55 根 4h 线                                     │
│  ├── 权重: ⭐⭐⭐⭐ (高优先级)                                    │
│  └── 触发: 每 4 小时更新                                         │
│                                                                 │
│  ⚡ 战术层 (15m)                                                │
│  ├── 职责: 精细入场点位，日内波动捕捉                             │
│  ├── 回溯: 13/34/55 根 15m 线                                   │
│  ├── 权重: ⭐⭐ (辅助参考)                                       │
│  └── 触发: 实时/每 15 分钟更新                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 时间框架职责划分

| 框架 | 周期 | 角色 | 核心职责 | 更新频率 |
|:-----|:-----|:-----|:---------|:---------|
| **趋势层** | 1d | 大方向锚定 | 定义趋势方向、极限支撑/阻力 | 日线收盘 |
| **战略层** | 4h | 主交易层 | 网格核心布局、订单执行 | 每 4 小时 |
| **战术层** | 15m | 精细调优 | 优化入场时机、日内微调 | 每 15 分钟 |

### 2.3 跨框架共振加成

当同一价位在多个时间框架中被识别为分形点时，触发**共振加成**：

```python
# 共振加成规则
RESONANCE_BONUS = {
    ("1d", "4h"):      1.5,   # 趋势+战略 = 强共振
    ("1d", "15m"):     1.3,   # 趋势+战术 = 中共振
    ("4h", "15m"):     1.2,   # 战略+战术 = 弱共振
    ("1d", "4h", "15m"): 2.0, # 三框架共振 = 超强
}
```

**共振检测容差**: 两个时间框架的分形点价格差异 < 0.3% 视为共振

### 2.4 MTF 水位融合策略

```
┌────────────────────────────────────────────────────────────┐
│                    水位融合流程                             │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Step 1: 分别提取各时间框架的分形点                         │
│          1d_fractals, 4h_fractals, 15m_fractals            │
│                                                            │
│  Step 2: 检测跨框架共振                                     │
│          resonance_pairs = detect_resonance(all_fractals)  │
│                                                            │
│  Step 3: 合并 & 去重 (以高时间框架为准)                     │
│          merged = merge_by_priority(1d > 4h > 15m)         │
│                                                            │
│  Step 4: 计算综合评分 (含 MTF 加成)                         │
│          final_score = base * volume * psy * trend * mtf   │
│                                                            │
│  Step 5: 输出按价格降序排列的目标水位                       │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

---

## 3. 环境参数与计算引擎

### 3.1 多时间框架参数

```python
# ============================================
# 多时间框架配置 (MTF)
# ============================================

# 趋势层 (1d) - 大级别方向锚定
TREND_INTERVAL = "1d"
TREND_FIB_LOOKBACK = [21, 55, 89]
# 物理含义:
# - 21d: ~1 个月，识别月度趋势
# - 55d: ~2.5 个月，季度级支撑
# - 89d: ~4 个月，长期结构边界

# 战略层 (4h) - 主交易执行层 [主周期]
MAIN_INTERVAL = "4h"
MAIN_FIB_LOOKBACK = [8, 21, 55]
# 物理含义:
# - 8x  (32h): ~1.3天，日内波动
# - 21x (84h): ~3.5天，周内震荡
# - 55x (220h): ~9天，系统安全边际

# 战术层 (15m) - 精细入场优化
TACTICAL_INTERVAL = "15m"
TACTICAL_FIB_LOOKBACK = [13, 34, 55]
# 物理含义:
# - 13x (195m): ~3.25小时，超短线
# - 34x (510m): ~8.5小时，日内结构
# - 55x (825m): ~13.75小时，日内边界
```

### 3.2 分形识别参数

```python
# 分形定义: 比前后 N 根 K 线都高/低的极值点
FRACTAL_WINDOW = {
    "1d":  3,    # 日线: 左右各 3 根 (更稳健)
    "4h":  2,    # 4h: 左右各 2 根 (默认)
    "15m": 2,    # 15m: 左右各 2 根
}

# 分形提取数量 (每周期每框架)
MAX_FRACTALS = {
    "1d":  3,    # 日线: 最多 3 个 (精选)
    "4h":  5,    # 4h: 最多 5 个 (主力)
    "15m": 7,    # 15m: 最多 7 个 (补充)
}

# 共振检测容差
RESONANCE_TOLERANCE = 0.003  # 0.3%
```

### 3.3 VPVR 参数

```python
# 成交量分布分析
VPVR_BINS = 50               # 价格区间划分数
HVN_THRESHOLD = 0.7          # 高成交量节点阈值 (前70%分位)
LVN_THRESHOLD = 0.3          # 低成交量真空区阈值 (前30%分位)
```

### 3.4 趋势参考指标

```python
# EMA 隧道指标 (基于 4h 主周期)
EMA_FAST = 144               # 快速 EMA
EMA_SLOW = 169               # 慢速 EMA
```

---

## 4. 水位评分机制 (Scoring Matrix)

### 4.1 评分公式

$$
\text{Final\_Score} = S_{base} \times W_{volume} \times W_{psychology} \times T_{env} \times M_{mtf}
$$

> 🆕 **新增 \(M_{mtf}\)**: 多时间框架共振系数

### 4.2 基础分 \(S_{base}\): 结构尺度 × 时间框架

基础分由「**时间框架层级**」和「**斐波那契回溯周期**」共同决定：

```
基础分 = 时间框架权重 × 回溯周期权重
```

#### 4.2.1 时间框架权重

| 时间框架 | 权重 | 角色 |
|:---------|:-----|:-----|
| **1d (趋势层)** | **1.5** | 大级别锚点，最高可信度 |
| **4h (战略层)** | **1.0** | 基准层，主交易执行 |
| **15m (战术层)** | **0.6** | 精细调优，灵敏但噪声多 |

#### 4.2.2 回溯周期权重 (各框架通用)

| 回溯级别 | 权重 | 含义 |
|:---------|:-----|:-----|
| **长周期** (55/89) | 80 | 战略级防线 |
| **中周期** (21/34) | 50 | 核心震荡区 |
| **短周期** (8/13) | 20 | 灵敏捕捉 |

#### 4.2.3 基础分计算示例

```python
# 基础分配置
TIMEFRAME_WEIGHTS = {
    "1d":  1.5,   # 趋势层
    "4h":  1.0,   # 战略层
    "15m": 0.6,   # 战术层
}

PERIOD_SCORES = {
    # 趋势层 (1d)
    89: 80, 55: 80, 21: 50,
    # 战略层 (4h)
    55: 80, 21: 50, 8: 20,
    # 战术层 (15m)
    55: 80, 34: 50, 13: 20,
}

def calculate_base_score(timeframe: str, period: int) -> float:
    """
    计算基础分
    
    示例:
    - 1d 55周期: 1.5 × 80 = 120
    - 4h 21周期: 1.0 × 50 = 50
    - 15m 13周期: 0.6 × 20 = 12
    """
    tf_weight = TIMEFRAME_WEIGHTS.get(timeframe, 1.0)
    period_score = PERIOD_SCORES.get(period, 20)
    return tf_weight * period_score
```

### 4.3 修正系数 \(W\): 能量与心理

#### 4.3.1 成交量权重 \(W_{volume}\)

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

#### 4.3.2 心理位吸附 \(W_{psychology}\)

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

### 4.4 环境加成 \(T_{env}\): 趋势干预

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

### 4.5 🆕 MTF 共振系数 \(M_{mtf}\)

当水位在多个时间框架中被识别时，触发共振加成：

| 共振组合 | 系数 | 含义 |
|:---------|:-----|:-----|
| **1d + 4h + 15m** | **2.0** | 全框架共振，超强信号 |
| **1d + 4h** | **1.5** | 趋势+战略共振，强信号 |
| **1d + 15m** | **1.3** | 跨层共振，中强信号 |
| **4h + 15m** | **1.2** | 战略+战术共振，一般信号 |
| **单框架** | **1.0** | 无共振，基础信号 |

```python
MTF_RESONANCE = {
    frozenset(["1d", "4h", "15m"]): 2.0,  # 三框架共振
    frozenset(["1d", "4h"]):        1.5,  # 趋势+战略
    frozenset(["1d", "15m"]):       1.3,  # 趋势+战术
    frozenset(["4h", "15m"]):       1.2,  # 战略+战术
}

def calculate_mtf_coefficient(source_timeframes: List[str]) -> float:
    """
    计算 MTF 共振系数
    
    Args:
        source_timeframes: 该水位被哪些时间框架识别
    
    Returns:
        共振系数 (1.0 ~ 2.0)
    """
    tf_set = frozenset(source_timeframes)
    return MTF_RESONANCE.get(tf_set, 1.0)
```

### 4.6 评分示例

#### 示例 1: 单框架水位

```
场景: BTC 在 4h 图上于 $94,000 发现一个 21x 分形低点
      该价位处于 VPVR 的 HVN 区域，且接近 0.618 回撤位
      当前为多头趋势

计算:
  S_base = 1.0 × 50 = 50 (4h 战略层, 21周期)
  W_volume = 1.3 (HVN)
  W_psychology = 1.2 (0.618 回撤)
  T_env = 1.1 (多头支撑)
  M_mtf = 1.0 (单框架，无共振)

  Final_Score = 50 × 1.3 × 1.2 × 1.1 × 1.0 = 85.8

结果: 高分水位，执行 1.2x 仓位
```

#### 示例 2: 多框架共振水位

```
场景: $92,500 同时出现在:
      - 1d 图: 55周期分形低点
      - 4h 图: 21周期分形低点
      位于 HVN 区域，接近整数位 $92,500

计算:
  S_base = max(1.5×80, 1.0×50) = 120 (取最高: 1d 55周期)
  W_volume = 1.3 (HVN)
  W_psychology = 1.2 (整数位)
  T_env = 1.1 (多头支撑)
  M_mtf = 1.5 (1d + 4h 共振)

  Final_Score = 120 × 1.3 × 1.2 × 1.1 × 1.5 = 308.88

结果: 超高分水位 (>100)，执行 1.5x 仓位，并标记为「战略级支撑」
```

---

## 5. 核心管理协议：降序索引继承

> ⚠️ **这是系统稳定性的底线逻辑，严禁使用基于价格距离的模糊匹配**

### 5.1 核心不变量

```
INVARIANT: 水位数组必须始终保持价格降序排列
           levels[0].price > levels[1].price > ... > levels[n].price
```

### 5.2 1:1 索引继承规则

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

### 5.3 状态流转规则

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

### 5.4 继承目的

当 55x 极点漂移导致水位平移时，确保持仓逻辑在**逻辑层级（第几格）**上保持连续：

```
时刻 T:  [96000, 94000, 92000] ← fill_counter = [1, 2, 0]
                ↓
时刻 T+1: [96500, 94500, 92500] ← fill_counter = [1, 2, 0] (继承)

解释: 虽然价格都上移了 500，但「第二格」的持仓逻辑保持不变
```

---

## 6. 仓位自动缩放 (Qty Scaling)

### 6.1 缩放规则

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

### 6.2 仓位计算示例

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

## 7. 更新触发规则 (Event Triggers)

### 7.1 ⚠️ 风险控制：防止频繁撤挂单

> **风险点**: 15m 级别分形点变动频繁，若直接触发 `inherit_levels_by_index`，会产生大量撤挂单操作，磨损手续费。

**解决方案**: 严格区分「继承触发」和「评分更新」

| 操作类型 | 触发层级 | 是否撤挂单 | 说明 |
|:---------|:---------|:-----------|:-----|
| **网格重构** | 1d / 4h | ✅ 是 | 执行完整 `inherit_levels_by_index` |
| **评分刷新** | 15m | ❌ 否 | 仅更新 `score`，不改变水位价格 |

### 7.2 触发条件

#### 7.2.1 网格重构触发条件 (会产生撤挂单)

**规则修正**: 网格重构必须满足 **必要条件 + 次要触发** 两级门槛，解决“必要条件 vs 多触发”的冲突。

**必要条件 (硬门槛)**:
- **🔴 锚点偏移**: 55x 周期最高/最低点位移 **\|Δ\| > 3%**  
  → 市场结构重大重组，**必须满足**

**次要触发 (满足任一即可触发重构)**:

| 触发器 | 条件 | 说明 |
|:-------|:-----|:-----|
| **🟡 覆盖告急** | 现价距最近水位 ≤ 1 格 | 边界防护，触发重构 |
| **🟢 定时重构 (1d)** | 每日 UTC 0:00 | 日线收盘后全量校准 |
| **🟣 手动触发** | 手动发起 | 运营强制重构 |

> ⚠️ **锚点偏移阈值 |Δ| > 3%** 为硬门槛。**绝对值**计算，无论上涨或下跌，低于此阈值即使触发“覆盖告急/定时刷新”，也只能走“评分刷新”路径，不触发重构。

#### 7.2.2 评分刷新触发条件 (不产生撤挂单)

| 触发器 | 条件 | 说明 |
|:-------|:-----|:-----|
| **定时刷新 (4h)** | 每 4 小时 | 更新评分、调整仓位系数 |
| **定时刷新 (15m)** | 每 15 分钟 (可配置) | 仅更新战术层评分，不改价格 |

> 备注: 当“覆盖告急/定时重构”触发但**未满足锚点偏移硬门槛**时，强制降级为评分刷新路径。

### 7.3 MTF 分层更新策略

```
┌────────────────────────────────────────────────────────────┐
│              多时间框架分层更新策略                          │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  📊 趋势层 (1d):                                           │
│     ├── 触发: 每日 UTC 0:00 / 锚点偏移 |Δ| > 3%            │
│     ├── 动作: 仅当 |Δ|>3% 时执行重构，否则评分刷新         │
│     └── 影响: ✅ 仅在满足硬门槛时撤挂单                    │
│                                                            │
│  🎯 战略层 (4h):                                           │
│     ├── 触发: 每 4 小时 / 覆盖告急                         │
│     ├── 动作: 仅当 |Δ|>3% 且触发成立时重构                │
│     │         否则仅刷新评分                               │
│     └── 影响: 🟡 仅在满足硬门槛时撤挂单                    │
│                                                            │
│  ⚡ 战术层 (15m):                                          │
│     ├── 触发: 每 15 分钟 (可关闭)                          │
│     ├── 动作: 仅更新水位评分 (score)，不改变价格           │
│     └── 影响: ❌ 不产生撤挂单                              │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 7.4 冷冻期约束

```python
# 网格重构冷冻期 (严格)
REBUILD_COOLDOWN = 4 * 60 * 60  # 4 小时

# 评分刷新冷冻期 (宽松)
SCORE_REFRESH_COOLDOWN = {
    "4h":  15 * 60,       # 战略层: 15 分钟
    "15m": 5 * 60,        # 战术层: 5 分钟
}

# 锚点偏移阈值 (绝对值)
ANCHOR_DRIFT_THRESHOLD = 0.03  # |Δ| > 3%

def should_rebuild_grid(
    current_anchor: float,
    last_anchor: float,
    last_rebuild_ts: int,
) -> bool:
    """
    判断是否应该重构网格
    
    必须同时满足:
    1. 锚点偏移 |Δ| > 3% (绝对值，无论涨跌)
    2. 距上次重构 > 4 小时
    """
    now = int(time.time())
    
    # 冷冻期检查
    if (now - last_rebuild_ts) < REBUILD_COOLDOWN:
        return False
    
    # 锚点偏移检查 (绝对值，必要条件)
    if last_anchor > 0:
        drift = abs(current_anchor - last_anchor) / last_anchor  # 绝对值
        return drift > ANCHOR_DRIFT_THRESHOLD
    
    return False

def can_refresh_score(
    timeframe: str,
    last_refresh_ts: int,
) -> bool:
    """判断是否可以刷新评分 (不触发重构)"""
    now = int(time.time())
    cooldown = SCORE_REFRESH_COOLDOWN.get(timeframe, 15 * 60)
    return (now - last_refresh_ts) >= cooldown
```

**设计理由**:
1. **3% 锚点偏移阈值**: 确保只有市场结构发生重大变化时才重构网格
2. **4 小时冷冻期**: 防止短时间内反复重构
3. **15m 层仅刷新评分**: 利用高频数据优化仓位系数，但不改变水位价格

### 7.5 🆕 手动边界设置

> **问题**: 如果 90% 的重构都是因为"覆盖告急"而不是"锚点偏移"，说明网格覆盖范围可能太窄。

**解决方案**: 支持手动设置网格上下边界，覆盖自动分形提取的结果。

```python
@dataclass
class ManualBoundary:
    """手动边界设置"""
    enabled: bool = False
    upper_price: Optional[float] = None  # 手动上边界 (阻力位)
    lower_price: Optional[float] = None  # 手动下边界 (支撑位)
    
def apply_manual_boundary(
    auto_levels: List[float],
    manual: ManualBoundary,
) -> List[float]:
    """
    应用手动边界
    
    规则:
    - 若 manual.upper_price 设置，则确保 levels[0] >= upper_price
    - 若 manual.lower_price 设置，则确保 levels[-1] <= lower_price
    - 自动在边界处插入水位（如果缺失）
    """
    if not manual.enabled:
        return auto_levels
    
    result = list(auto_levels)
    
    # 确保上边界
    if manual.upper_price and (not result or result[0] < manual.upper_price):
        result.insert(0, manual.upper_price)
    
    # 确保下边界
    if manual.lower_price and (not result or result[-1] > manual.lower_price):
        result.append(manual.lower_price)
    
    # 保持降序
    return sorted(set(result), reverse=True)
```

**配置示例**:

```yaml
grid:
  level_generation:
    # 手动边界设置 (优先于自动分形)
    manual_boundary:
      enabled: true
      upper_price: 105000    # 手动设置上边界
      lower_price: 85000     # 手动设置下边界
```

**使用场景**:
- 自动分形覆盖范围不足时
- 有明确的技术分析支撑/阻力位
- 想要固定网格的交易区间

### 7.6 🆕 重构日志记录

> **目的**: 记录每次重构的触发原因，便于后续分析和参数调优。

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional

class RebuildTrigger(str, Enum):
    """重构触发原因"""
    ANCHOR_DRIFT = "ANCHOR_DRIFT"       # 锚点偏移 > 3%
    BOUNDARY_ALERT = "BOUNDARY_ALERT"   # 覆盖告急
    DAILY_REFRESH = "DAILY_REFRESH"     # 每日定时刷新
    MANUAL_REBUILD = "MANUAL_REBUILD"   # 手动触发
    COLD_START = "COLD_START"           # 冷启动

@dataclass
class RebuildLog:
    """重构日志"""
    timestamp: int
    trigger: RebuildTrigger
    anchor_before: float
    anchor_after: float
    drift_pct: float                    # 锚点偏移百分比
    levels_before: int                  # 重构前水位数
    levels_after: int                   # 重构后水位数
    orders_cancelled: int               # 撤销订单数
    orders_placed: int                  # 新挂订单数
    detail: Optional[str] = None        # 额外说明

def log_rebuild(
    trigger: RebuildTrigger,
    state_before: GridState,
    state_after: GridState,
    inheritance_result: InheritanceResult,
) -> RebuildLog:
    """
    记录重构日志
    """
    drift_pct = 0.0
    if state_before.anchor_price > 0:
        drift_pct = abs(
            state_after.anchor_price - state_before.anchor_price
        ) / state_before.anchor_price * 100
    
    log = RebuildLog(
        timestamp=int(time.time()),
        trigger=trigger,
        anchor_before=state_before.anchor_price,
        anchor_after=state_after.anchor_price,
        drift_pct=drift_pct,
        levels_before=len(state_before.support_levels_state),
        levels_after=len(state_after.support_levels_state),
        orders_cancelled=len(inheritance_result.orders_to_cancel),
        orders_placed=len(inheritance_result.orders_to_place),
    )
    
    # 记录到日志和状态
    logger.info(
        f"🔄 网格重构: trigger={trigger.value}, "
        f"drift={drift_pct:.2f}%, "
        f"levels={log.levels_before}→{log.levels_after}, "
        f"orders: -{log.orders_cancelled}/+{log.orders_placed}"
    )
    
    return log
```

**日志分析示例**:

```python
def analyze_rebuild_logs(logs: List[RebuildLog]) -> dict:
    """
    分析重构日志，识别优化方向
    """
    trigger_counts = {}
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
    }
    
    # 诊断建议
    boundary_pct = trigger_counts.get(RebuildTrigger.BOUNDARY_ALERT, 0) / total * 100 if total > 0 else 0
    if boundary_pct > 50:
        analysis["suggestion"] = (
            f"⚠️ {boundary_pct:.0f}% 的重构由'覆盖告急'触发，"
            "建议: 1) 扩大手动边界 2) 增加 55x 分形提取范围"
        )
    
    return analysis
```

**诊断输出示例**:

```
📊 重构日志分析 (最近 30 天):
- 总重构次数: 45
- 按触发原因:
  - ANCHOR_DRIFT: 12 (26.7%)
  - BOUNDARY_ALERT: 28 (62.2%) ⚠️
  - DAILY_REFRESH: 5 (11.1%)
- 平均锚点偏移: 4.2%
- 平均每次重构订单变动: 8.3

⚠️ 62% 的重构由'覆盖告急'触发，建议:
  1) 扩大手动边界
  2) 增加 55x 分形提取范围
```

### 7.7 触发流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                      触发检查流程 (分层)                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────── 网格重构路径 (会撤挂单) ────────────────────┐    │
│  │                                                            │    │
│  │  1. 检查重构冷冻期 ────── 未到期 ────────▶ 走评分路径     │    │
│  │         │                                                  │    │
│  │         ▼ 已到期                                           │    │
│  │                                                            │    │
│  │  2. 检查硬门槛: 锚点偏移 |Δ| ≥ 3% ?                       │    │
│  │         │                                                  │    │
│  │         ├─ 否 ───────────────────────────▶ 走评分路径     │    │
│  │         │                                                  │    │
│  │         ▼ 是                                               │    │
│  │                                                            │    │
│  │  3. 检查次要触发:                                          │    │
│  │     ├─ 覆盖告急 ──────────▶ trigger = BOUNDARY_ALERT      │    │
│  │     ├─ 定时重构 (1d) ────▶ trigger = DAILY_REFRESH        │    │
│  │     └─ 手动触发 ──────────▶ trigger = MANUAL_REBUILD      │    │
│  │         │                                                  │    │
│  │         ▼ 满足任一条件                                      │    │
│  │                                                            │    │
│  │  4. 执行 LevelCalculator.generate()                       │    │
│  │         │                                                  │    │
│  │         ▼                                                  │    │
│  │                                                            │    │
│  │  5. 应用手动边界 (若启用) ← apply_manual_boundary()       │    │
│  │         │                                                  │    │
│  │         ▼                                                  │    │
│  │                                                            │    │
│  │  6. 执行 inherit_levels_by_index() ← 核心继承             │    │
│  │         │                                                  │    │
│  │         ▼                                                  │    │
│  │                                                            │    │
│  │  7. 执行订单调整 (撤单 + 重挂) ← 产生手续费               │    │
│  │         │                                                  │    │
│  │         ▼                                                  │    │
│  │                                                            │    │
│  │  8. 🆕 记录重构日志 ← log_rebuild(trigger, ...)           │    │
│  │         │                                                  │    │
│  │         ▼                                                  │    │
│  │                                                            │    │
│  │  9. 更新 last_rebuild_ts, anchor_price                    │    │
│  │                                                            │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  ┌─────────────── 评分刷新路径 (不撤挂单) ───────────────────┐    │
│  │                                                            │    │
│  │  1. 检查评分刷新冷冻期 ─── 未到期 ────▶ 跳过              │    │
│  │         │                                                  │    │
│  │         ▼ 已到期                                           │    │
│  │                                                            │    │
│  │  2. 重新计算各水位评分 (含 15m MTF)                       │    │
│  │         │                                                  │    │
│  │         ▼                                                  │    │
│  │                                                            │    │
│  │  3. 更新 score, qty_multiplier ← 仅内存更新               │    │
│  │         │                                                  │    │
│  │         ▼                                                  │    │
│  │                                                            │    │
│  │  4. 更新 last_score_refresh_ts                            │    │
│  │                                                            │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
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

## 10. 与现有系统的集成

### 10.1 集成点

| 组件 | 集成方式 |
|:-----|:---------|
| `GridPositionManager` | 调用 `LevelCalculator` 生成水位 |
| `KeyLevelGridStrategy` | 在 `_update_cycle` 中触发水位更新 |
| `level_manager.py` | 复用现有继承逻辑 |
| `position.py` | 扩展 `GridLevelState` 添加评分字段 |
| 🆕 `mtf_kline_feed.py` | 管理多时间框架 K 线订阅 |

### 10.2 向后兼容

- **state.json**: 新字段 (`score`, `qty_multiplier`) 可选，旧版自动默认
- **继承逻辑**: 完全复用 v2.0 的 `inherit_levels_by_index()`
- **订单执行**: 无变化，仅下单数量根据 `qty_multiplier` 调整

### 10.3 配置扩展

```yaml
# configs/config.yaml

grid:
  # V3.1 水位生成配置 (MTF 增强版)
  level_generation:
    enabled: true                     # 启用动态水位生成
    max_levels: 10                    # 最大水位数
    min_score: 30                     # 最低评分阈值
    
    # 🆕 触发规则配置 (防频繁撤挂单)
    triggers:
      # 网格重构触发条件
      anchor_drift_threshold: 0.03    # 锚点偏移阈值 |Δ| > 3% (绝对值，必要条件)
      rebuild_cooldown_sec: 14400     # 重构冷冻期 4 小时
      
      # 评分刷新冷冻期
      score_refresh_cooldown:
        "4h": 900                     # 战略层: 15 分钟
        "15m": 300                    # 战术层: 5 分钟
    
    # 🆕 手动边界设置 (可选，优先于自动分形)
    manual_boundary:
      enabled: false                  # 是否启用手动边界
      upper_price: null               # 手动上边界 (阻力位)
      lower_price: null               # 手动下边界 (支撑位)
    
    # 🆕 重构日志配置
    rebuild_logging:
      enabled: true                   # 启用重构日志
      max_logs: 100                   # 最大保留日志数
      analyze_interval_days: 30       # 分析周期 (天)
    
    # 🆕 多时间框架配置
    mtf:
      enabled: true                   # 启用 MTF
      resonance_tolerance: 0.003      # 共振检测容差 (0.3%)
      
      # 趋势层 (1d)
      trend:
        interval: "1d"
        fib_lookback: [21, 55, 89]
        max_fractals: 3
        update_at: "00:00"            # UTC 0:00 更新
        
      # 战略层 (4h) - 主周期
      main:
        interval: "4h"
        fib_lookback: [8, 21, 55]
        max_fractals: 5
        
      # 战术层 (15m)
      tactical:
        interval: "15m"
        fib_lookback: [13, 34, 55]
        max_fractals: 7
        enabled: true                 # 可关闭以减少复杂度
        triggers_rebuild: false       # 🆕 15m 不触发重构，仅刷新评分
    
    # 趋势指标
    ema_fast: 144
    ema_slow: 169
    
  level_scoring:
    # 时间框架权重
    timeframe_weights:
      "1d":  1.5
      "4h":  1.0
      "15m": 0.6
      
    # 周期基础分
    period_scores:
      89: 80
      55: 80
      34: 50
      21: 50
      13: 20
      8: 20
      
    # 成交量权重
    volume_weights:
      HVN: 1.3
      NORMAL: 1.0
      LVN: 0.6
      
    # 心理位权重
    psychology_weight: 1.2
    
    # 趋势系数
    trend_coefficients:
      BULLISH:
        support: 1.1
        resistance: 0.9
      BEARISH:
        support: 0.9
        resistance: 1.1
    
    # 🆕 MTF 共振系数
    mtf_resonance:
      "1d+4h+15m": 2.0
      "1d+4h": 1.5
      "1d+15m": 1.3
      "4h+15m": 1.2
```

---

## 11. 开发执行计划

### 11.1 阶段划分

| 阶段 | 任务 | 优先级 | 依赖 |
|:-----|:-----|:-------|:-----|
| **Phase 0** | 🆕 实现 `MTFKlineFeed` | P0 | 无 |
| **Phase 1** | 实现 `FractalExtractor` | P0 | Phase 0 |
| **Phase 2** | 🆕 实现 `MTFMerger` (共振检测) | P0 | Phase 1 |
| **Phase 3** | 实现 `VPVRAnalyzer` | P1 | Phase 1 |
| **Phase 4** | 实现 `LevelScorer` (MTF 版) | P0 | Phase 2, 3 |
| **Phase 5** | 实现 `IndexInheritor` 封装 | P0 | Phase 4 |
| **Phase 6** | 集成到 `GridPositionManager` | P0 | Phase 5 |
| **Phase 7** | 配置与 UI 展示 | P2 | Phase 6 |

### 11.2 详细任务清单

#### Phase 0: MTFKlineFeed (🆕)

```
P0.1: 设计多时间框架 K 线数据结构
P0.2: 实现 get_klines(timeframe, lookback) 接口
P0.3: 实现 K 线缓存与自动刷新机制
P0.4: 集成到现有 gate_kline_feed.py
P0.5: 编写单元测试
```

#### Phase 1: FractalExtractor

```
P1.1: 实现 K 线数据结构解析
P1.2: 实现分形识别算法 (左右 N 根比较)
P1.3: 实现多周期分形提取 (支持 8/13/21/34/55/89)
P1.4: 支持多时间框架 (1d/4h/15m)
P1.5: 编写单元测试
```

#### Phase 2: MTFMerger (🆕)

```
P2.1: 实现跨时间框架价格共振检测
P2.2: 实现共振水位合并逻辑 (以高框架为准)
P2.3: 实现 MTFLevelCandidate 输出
P2.4: 编写单元测试
```

#### Phase 3: VPVRAnalyzer

```
P3.1: 实现价格区间划分
P3.2: 实现成交量累积计算
P3.3: 实现 HVN/LVN 区域识别
P3.4: 实现 POC (控制价) 计算
P3.5: 编写单元测试
```

#### Phase 4: LevelScorer (MTF 版)

```
P4.1: 实现基础分计算 (时间框架权重 × 周期权重)
P4.2: 实现成交量权重计算
P4.3: 实现心理位吸附 (Fib + 整数位)
P4.4: 实现趋势系数计算 (EMA 隧道)
P4.5: 实现 MTF 共振系数计算
P4.6: 实现最终评分公式
P4.7: 编写单元测试
```

#### Phase 5: IndexInheritor 封装

```
P5.1: 封装 inherit_levels_by_index 调用
P5.2: 实现评分附加逻辑
P5.3: 实现 qty_multiplier 计算 (支持 1.5x 超重仓)
P5.4: 编写集成测试
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
