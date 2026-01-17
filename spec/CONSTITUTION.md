# Key Level Grid 项目宪法 (Constitution)

> **版本**: 1.3.0  
> **生效日期**: 2026-01-17  
> **维护者**: KeyLevelGrid Core Team

---

## 📜 宪法宗旨

本宪法是 Key Level Grid (KLG) 项目的**最高设计准则**。所有代码变更、架构决策和功能迭代必须遵守本文件中定义的原则。任何违背宪法的 PR 应被驳回，除非经过正式的宪法修订流程。

---

## 🏛️ 核心原则 (Core Principles)

### 原则一：逐级邻位止盈 (Progressive Level Mapping)

> **定义**：网格止盈逻辑**强制执行"逐级邻位映射"**，即支撑位 $S_n$ 成交后，其止盈单必须挂在该支撑位**物理价格之上的第一个有效水位** $L_{n+1}$。**严禁参考平均成本价（Avg Price）**。

**核心规则**：

| 规则 | 说明 |
|------|------|
| **邻位映射** | $S_n$ 的止盈单挂在 $L_{n+1}$（$S_n$ 上方的第一个水位） |
| **禁止堆叠** | 严禁将低位成交的止盈单全部挂在高位阻力位 |
| **一格一出** | 必须保持"一格一出"的局部对冲结构 |
| **仓位解耦** | 卖单量严格执行 `base_qty × sell_quota_ratio`，与持仓均价无关 |

**映射示例**：
```
水位列表（价格升序）: 94,000 → 94,500 → 95,000 → 95,500 → 96,000
当前价格: 95,200

支撑位成交：
  S_1 (94,500) × 2 次成交 → 挂在 95,000 (94,500 上方第一个水位)
  S_2 (94,000) × 1 次成交 → 挂在 94,500 (94,000 上方第一个水位)

✅ 正确：每个支撑位的止盈挂在其"上一格"
❌ 错误：所有止盈都堆叠到 95,500 或 96,000
```

**约束条件**：
- 止盈价格由**物理邻位**决定，而非"当前价格上方的最近阻力位"
- `avg_entry_price` **仅用于统计展示和盈亏计算**，严禁参与挂单决策
- 止盈单的存在与否，仅由"是否有对应的买入成交"决定
- 若邻位已被当前价格穿越（`L_{n+1} < current_price`），该止盈单**立即生效**（可市价卖出或等待回调）

**代码检查点**：
```
position.py::find_adjacent_level() - 寻找物理邻位
position.py::build_recon_actions() - 卖单价格必须来自邻位映射
position.py::build_event_sell_increment() - 增量卖单必须遵循逐级映射
```

**禁止的代码模式**：
```python
# ❌ 禁止：参考平均成本价
if sell_price > avg_entry_price:
    place_sell_order(...)

# ❌ 禁止：所有卖单堆叠到同一阻力位
for support in supports_with_fills:
    place_sell_order(highest_resistance.price, qty)

# ✅ 正确：逐级邻位映射
for support in supports_with_fills:
    adjacent_level = find_adjacent_level_above(support.price)
    place_sell_order(adjacent_level.price, base_qty * sell_quota_ratio)
```

---

### 原则二：动态仓位保留协议 (Dynamic Position Residual Protocol)

> **定义**：系统必须支持"止盈/保留"比例分配，确保部分仓位作为长期底仓保留。

**核心公式**：
```
卖单数量 = 成交数量 × sell_quota_ratio
保留数量 = 成交数量 × (1 - sell_quota_ratio)
```

**约束条件**：

| 约束项 | 要求 |
|--------|------|
| **配置化** | `sell_quota_ratio` 必须从配置文件读取，默认值 `0.7` |
| **灵活性** | 必须支持设置为 `1.0`（即全量止盈，不保留） |
| **精度处理** | 当卖出量低于交易所最小交易单位时，必须有明确的取整策略 |
| **底仓锁定** | `base_position_locked` 由用户手动设置，系统**不得**自动修改 |

**精度处理规则**：
```python
# 计算卖出数量
sell_qty = fill_qty * sell_quota_ratio

# 精度处理：向下取整到交易所最小单位
min_qty = exchange_min_trade_unit  # 如 BTC = 0.0001
sell_qty = math.floor(sell_qty / min_qty) * min_qty

# 防止无效小额订单
if sell_qty < min_qty:
    sell_qty = 0  # 跳过本次止盈，全部保留
```

**配置示例**：
```yaml
grid:
  sell_quota_ratio: 0.7        # 70% 止盈，30% 保留（默认）
  # sell_quota_ratio: 1.0      # 全量止盈，不保留
  base_position_locked: 0.0    # 用户手动设置的额外锁定底仓
```

**总可售数量计算**：
```
可售数量 = (总持仓 - base_position_locked) × sell_quota_ratio
```

---

### 原则三：对账第一真理 (Reconciliation First Truth)

> **定义**：Recon（对账）轨道必须以**交易所实盘**为准，动态同步本地 `state.json`。交易所数据是系统的**唯一真实来源（Single Source of Truth）**。

**约束条件**：
- 交易所数据（持仓、挂单、成交）是 **Source of Truth**
- 本地状态 (`state.json`, `trades.jsonl`) 是 **缓存和辅助**
- 当本地状态与交易所不一致时，**必须以交易所为准**进行修正
- 对账周期由 `recon_interval_sec` 控制（默认 30 秒）
- 禁止任何"本地状态覆盖交易所"的逻辑

**数据优先级**：
```
1. 交易所实时 API（持仓、挂单）  → 最高权威，不可质疑
2. 本地成交账本 (trades.jsonl)   → 用于还原历史订单归属
3. 网格状态 (state.json)         → 快照缓存，随时可重建
```

**对账动作矩阵**：
| 场景 | 动作 | 说明 |
|------|------|------|
| 交易所持仓 > 本地记录 | 补齐 `active_inventory` | 优先从成交历史匹配，兜底按水位填充 |
| 交易所持仓 < 本地记录 | 移除 `active_inventory` | FIFO 销账 |
| 交易所挂单缺失 | 根据水位表补挂 | 仅补挂符合条件的订单 |
| 交易所有多余挂单 | 不主动撤销 | 可能是用户手动挂的 |
| 交易所持仓为 0 | 清空所有本地记录 | `active_inventory` 和 `fill_counter` 归零 |

**同步时机**：
- **启动时**：立即从交易所拉取全量数据，校准本地状态
- **运行时**：每 `recon_interval_sec` 秒执行一次全量对账
- **成交后**：Event Track 增量更新，不等待 Recon

---

### 原则四：模块化扩展 (Modular Extension)

> **定义**：系统架构必须支持多策略并行（如 Grid 和 DCA），且共享底层交易所适配器。

**架构约束**：
```
┌─────────────────────────────────────────────────────┐
│                   Strategy Layer                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │  KLG Grid   │  │  DCA Bot    │  │  Future...  │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │
└─────────┼────────────────┼────────────────┼─────────┘
          │                │                │
┌─────────▼────────────────▼────────────────▼─────────┐
│                  Executor Layer                      │
│  ┌─────────────────────────────────────────────────┐│
│  │              Exchange Executor                  ││
│  │   (GateExecutor / BinanceExecutor / ...)       ││
│  └─────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────┘
```

**接口契约**：
- 所有策略必须通过 `Executor` 接口与交易所交互
- `Executor` 必须实现以下方法：
  - `get_position()` - 获取持仓
  - `get_open_orders()` - 获取挂单
  - `place_order()` - 下单
  - `cancel_order()` - 撤单
  - `get_trade_history()` - 获取成交历史
- 策略之间通过**独立的状态文件**隔离，禁止共享 `state.json`

---

## 📐 架构约束 (Architectural Constraints)

### A1. 双轨驱动模型 (Dual-Track Model)

系统采用 **Recon + Event** 双轨驱动：

| 轨道 | 职责 | 触发频率 |
|------|------|----------|
| **Recon Track** | 全量对账，修正偏移，确保一致性 | 每 30 秒 |
| **Event Track** | 增量响应，成交后立即补单 | 实时 |

**约束**：
- Recon 和 Event 共享同一把 `_grid_lock`，防止并发冲突
- Event 的增量动作必须是 Recon 动作的**子集**（即 Event 不能做 Recon 不会做的事）

---

### A2. 水位状态机 (Level State Machine)

每个水位（Level）必须遵循以下状态机：

```
         ┌─────────────┐
         │    IDLE     │ ← 初始状态 / 订单撤销
         └──────┬──────┘
                │ place_order()
                ▼
         ┌─────────────┐
         │   PENDING   │ ← 挂单中
         └──────┬──────┘
                │ order_filled()
                ▼
         ┌─────────────┐
         │   FILLED    │ ← 已成交（瞬时状态）
         └──────┬──────┘
                │ mark_idle()
                ▼
         ┌─────────────┐
         │    IDLE     │ ← 等待下一轮
         └─────────────┘
```

---

### A3. 持仓清单模型 (Position Inventory Model)

持仓追踪采用 **Inventory（清单）** 模式，而非简单计数：

```python
@dataclass
class ActiveFill:
    order_id: str      # 订单 ID（唯一标识）
    price: float       # 成交价格
    qty: float         # 成交数量
    level_id: int      # 归属水位 ID
    timestamp: int     # 成交时间戳
```

**约束**：
- `fill_counter` 是 `active_inventory` 的**视图**，而非独立状态
- 所有对 `fill_counter` 的修改必须通过 `_update_fill_counters_from_inventory()` 同步
- 卖出时采用 **FIFO** 销账（先买先卖）

---

### A4. 本地账本 (Local Trade Ledger)

系统必须维护一份本地成交账本 (`trades.jsonl`)：

**写入时机**：
- 买入成交时：立即追加记录
- 卖出成交时：立即追加记录

**用途**：
- 启动时还原 `active_inventory`
- 提供 `order_id` → `level_id` 的永久映射
- 审计和收益分析

**格式**：
```jsonl
{"timestamp":1705423286,"order_id":"360...458","side":"buy","price":94500,"qty":0.001,"level_id":102}
{"timestamp":1705423386,"order_id":"360...459","side":"sell","price":95000,"qty":0.001}
```

---

## 🛡️ 状态管理准则 (State Management)

> **目的**：防止 AI 或人工重构过程中破坏水位生命周期逻辑，确保持仓数据完整性。

### S1. 严禁物理删除 (No Physical Deletion)

> **准则**：任何 `fill_counter > 0` 的水位实例**严禁**从内存或持久化状态中移除。

**约束条件**：

| 约束项 | 要求 |
|--------|------|
| **删除前检查** | 必须验证 `fill_counter == 0` 且无关联挂单 |
| **退役替代** | 不满足删除条件时，必须转为 `RETIRED` 状态 |
| **审计追踪** | 任何状态变更必须记录到日志 |
| **持久化保护** | `state.json` 保存时必须包含所有非 `DEAD` 水位 |

**代码检查点**：
```python
# ✅ 正确：删除前必须检查
def can_destroy_level(level: GridLevelState, inventory: List[ActiveFill]) -> bool:
    if level.fill_counter > 0:
        return False
    if level.active_order_id:
        return False
    # 检查是否有关联的卖单
    for fill in inventory:
        if fill.target_sell_level_id == level.level_id:
            return False
    return True

# ❌ 禁止：直接删除
active_levels = [l for l in levels if l.price != target_price]  # 危险！
```

---

### S2. 退役优先保护 (Retirement Protection)

> **准则**：重构（Rebuild）逻辑必须能够处理"非连续索引"的退役水位。即使新生成的网格只有 10 格，而旧网格中有 3 个处于退役状态的持仓水位，系统必须同时管理这 **10 + 3** 个状态点。

**约束条件**：

| 约束项 | 要求 |
|--------|------|
| **双列表管理** | `active_levels` 存放活跃水位，`retired_levels` 存放退役水位 |
| **独立生命周期** | 退役水位独立于网格重构，仅当 `fill_counter == 0` 且无挂单时转为 `DEAD` |
| **卖单执行** | 退役水位仍可作为卖单映射目标（只卖不买） |
| **统计分离** | 退役水位不计入"当前网格格数"，但计入"总管理水位数" |

**状态流转**：
```
ACTIVE ──────┬──────────────────────────────────▶ RETIRED
             │  (被挤出索引范围 或 评分 < 30)       │
             │                                     │
             │                                     ▼
             │                              fill_counter == 0
             │                              且无关联挂单？
             │                                     │
             │                    ┌────────────────┴────────────────┐
             │                    │ 是                              │ 否
             │                    ▼                                 │
             │                  DEAD                                │
             │               (物理删除)                        保持 RETIRED
             │                                                      │
             └──────────────────────────────────────────────────────┘
```

**数据结构**：
```python
@dataclass
class GridState:
    active_levels: List[GridLevelState]    # 活跃水位（参与买卖）
    retired_levels: List[GridLevelState]   # 退役水位（只卖不买）
    # ... 其他字段
```

---

## ⚙️ 配置准则 (Configuration & Hard-coding)

> **目的**：确保所有可调参数集中管理，便于策略调优和 AI 理解上下文。

### C1. 参数解耦 (Parameter Decoupling)

> **准则**：**禁止**在代码中直接使用魔数（Magic Numbers）。必须通过 `self.config` 访问所有策略参数。

**核心参数清单**：

| 参数名 | 默认值 | 说明（中文） |
|--------|--------|--------------|
| `ANCHOR_DRIFT_THRESHOLD` | `0.03` | 锚点偏移阈值：触发网格重构的价格变化百分比 |
| `FIBONACCI_LOOKBACK` | `[8, 21, 55]` | 斐波那契回溯序列：定义短/中/长线周期 |
| `MIN_SCORE_THRESHOLD_ACTIVE` | `50` | 活跃评分门槛：低于此分数的水位转为退役 |
| `MIN_SCORE_THRESHOLD_ENTRY` | `30` | 入场评分门槛：低于此分数的新水位不开仓 |
| `REBUILD_COOLDOWN_SEC` | `900` | 重构冷冻期：两次网格重构的最小间隔（秒） |

**代码规范**：
```python
# ❌ 禁止：硬编码魔数
if price_change > 0.03:  # 魔数！
    trigger_rebuild()

if score < 50:  # 魔数！
    level.status = RETIRED

# ✅ 正确：通过配置访问
if price_change > self.config.anchor_drift_threshold:
    trigger_rebuild()

if score < self.config.min_score_threshold_active:
    level.lifecycle_status = LevelLifecycleStatus.RETIRED
```

---

### C2. 结构透明性 (Structural Transparency)

> **准则**：所有评分修正系数（Volume, Psychology, Trend, MTF）必须在 `config.yaml` 中显式定义，且**必须配上中文说明**。

**配置示例**：
```yaml
# ============================================================
# 水位评分系数配置 (Level Scoring Coefficients)
# ============================================================
scoring:
  # --- 成交量权重 (Volume Weight) ---
  volume_weight_hvn: 1.3        # 高成交量节点（HVN/POC）：筹码密集区，支撑/阻力强
  volume_weight_lvn: 0.6        # 低成交量节点（LVN）：真空区，易被突破

  # --- 心理位吸附权重 (Psychology Weight) ---
  psychology_weight_aligned: 1.2  # 与斐波那契回撤位或大整数位对齐时的加成

  # --- 趋势系数 (Trend Coefficient) ---
  trend_coef_bullish_support: 1.3   # 多头趋势下的支撑位加成
  trend_coef_bullish_resistance: 0.8  # 多头趋势下的阻力位削弱
  trend_coef_bearish_support: 0.8   # 空头趋势下的支撑位削弱
  trend_coef_bearish_resistance: 1.3  # 空头趋势下的阻力位加成
  trend_coef_neutral: 1.0           # 震荡趋势下不修正

  # --- MTF 共振系数 (Multi-TimeFrame Resonance) ---
  mtf_resonance_triple: 1.5   # 三周期共振（1d + 4h + 15m）：最高确定性
  mtf_resonance_double: 1.2   # 双周期共振
  mtf_resonance_single: 1.0   # 单周期：基准值

# ============================================================
# 触发器配置 (Trigger Configuration)
# ============================================================
triggers:
  anchor_drift_threshold: 0.03    # 锚点偏移阈值（3%）：触发网格重构的必要条件
  boundary_alert_levels: 1        # 覆盖告急：现价距离边界仅剩 N 个水位时触发
  rebuild_cooldown_sec: 900       # 重构冷冻期（秒）：15 分钟内不重复重构

# ============================================================
# 斐波那契配置 (Fibonacci Configuration)
# ============================================================
fibonacci:
  lookback_sequence: [8, 21, 55]  # 回溯序列：短线(8x) / 中线(21x) / 长线(55x)
  base_scores:
    fib_55: 80    # 长线(55x)基础分：战略级防线
    fib_21: 50    # 中线(21x)基础分：核心震荡带
    fib_8: 20     # 短线(8x)基础分：高灵敏低抗噪

# ============================================================
# 仓位缩放配置 (Position Scaling)
# ============================================================
position_scaling:
  score_threshold_1_5x: 80    # 评分 >= 80：1.5 倍标准仓位
  score_threshold_1_0x: 50    # 评分 >= 50：1.0 倍标准仓位（基准）
  score_threshold_skip: 30    # 评分 < 30：不开新仓
```

**AI 可读性要求**：
- 每个配置项必须有**英文键名**（供代码访问）
- 每个配置项必须有**中文注释**（供 AI 和开发者理解）
- 配置分组必须有**分隔线和组名**

---

## 🚫 禁止事项 (Prohibitions)

| 编号 | 禁止行为 | 原因 |
|------|----------|------|
| P1 | 使用 `avg_entry_price` 决定是否挂止盈单 | 违反原则一 |
| P2 | 自动修改 `base_position_locked` | 违反原则二 |
| P3 | 产生低于交易所最小单位的无效订单 | 违反原则二精度要求 |
| P4 | 本地状态覆盖交易所数据 | 违反原则三 |
| P5 | 在 Event Track 中执行全量对账 | 违反 A1 |
| P6 | 直接修改 `fill_counter` 而不更新 `active_inventory` | 违反 A3 |
| P7 | 在策略层直接调用交易所 API（绕过 Executor） | 违反原则四 |
| P8 | 使用价格相似度匹配代替 `order_id` 匹配 | 破坏对账精度 |
| P9 | 物理删除 `fill_counter > 0` 的水位 | 违反 S1 |
| P10 | 在代码中使用魔数替代配置参数 | 违反 C1 |
| P11 | 配置项缺少中文说明 | 违反 C2 |

---

## 📝 配置契约 (Configuration Contract)

以下配置项具有**宪法级约束**，修改需谨慎：

```yaml
grid:
  # 原则二：动态仓位保留协议
  sell_quota_ratio: 0.7           # [0.5, 1.0] 止盈比例，1.0 = 全量止盈
  base_position_locked: 0.0       # >= 0, 用户手动设置的额外锁定底仓
  
  # 原则一：邻位对冲映射
  min_profit_pct: 0.0001          # [0, 0.01] 利润过滤阈值（水位价格基础上）
  buy_price_buffer_pct: 0.002     # [0, 0.02] 买单缓冲
  
  # 网格基础配置
  base_amount_per_grid: 0.001     # > 0, 单格数量
  max_fill_per_level: 3           # [1, 10] 单水位最大补买
  
  # 原则三：对账第一真理
  recon_interval_sec: 30          # [10, 300] 对账周期
  restore_state_enabled: true     # 是否从持久化恢复
```

**配置项约束说明**：

| 配置项 | 取值范围 | 默认值 | 宪法约束 |
|--------|----------|--------|----------|
| `sell_quota_ratio` | [0.5, 1.0] | 0.7 | 原则二：支持 1.0（全量止盈） |
| `base_position_locked` | >= 0 | 0.0 | 原则二：仅用户可修改 |
| `min_profit_pct` | [0, 0.01] | 0.0001 | 原则一：仅作为过滤器 |
| `recon_interval_sec` | [10, 300] | 30 | 原则三：不得禁用对账 |

---

## 🔄 修订流程 (Amendment Process)

1. **提案**：在 `spec/amendments/` 目录下创建修订提案文档
2. **审查**：至少 7 天的审查期
3. **表决**：需要核心维护者一致同意
4. **生效**：更新本文件并递增版本号

---

## 📚 附录：术语表 (Glossary)

| 术语 | 定义 |
|------|------|
| **水位 (Level)** | 支撑位或阻力位，是网格的基本单元 |
| **逐级邻位映射 (Progressive Level Mapping)** | 买入水位 $S_n$ 的止盈挂在其物理价格上方的第一个水位 $L_{n+1}$ |
| **物理邻位 (Adjacent Level)** | 某水位在价格序列中紧邻的上一个/下一个水位 |
| **一格一出** | 每个支撑位的止盈只挂在其上一格，不堆叠到更高位置 |
| **Recon** | Reconciliation（对账），用于修正本地与交易所的偏移 |
| **Event** | 成交事件驱动的增量更新 |
| **Inventory** | 持仓清单，记录每一笔未平仓的买入 |
| **FIFO** | First In First Out，先进先出 |
| **Source of Truth** | 唯一真实来源，在本系统中指交易所实盘数据 |
| **sell_quota_ratio** | 止盈比例，每次成交后用于止盈的仓位百分比（默认 0.7） |
| **base_position_locked** | 用户手动锁定的长期底仓，不参与止盈计算 |
| **ACTIVE** | 水位生命周期状态：活跃，可执行买入和卖出 |
| **RETIRED** | 水位生命周期状态：退役，仅允许卖出清仓，禁止新买入 |
| **DEAD** | 水位生命周期状态：已销毁，可从内存移除 |
| **锚点偏移 (Anchor Drift)** | 市场结构关键点（如 55x 周期极点）的价格位移 |
| **重构冷冻期 (Rebuild Cooldown)** | 两次网格重构之间的最小时间间隔 |
| **MTF (Multi-TimeFrame)** | 多时间框架分析，综合 1d/4h/15m 周期数据 |
| **HVN (High Volume Node)** | 高成交量节点，VPVR 中的筹码密集区 |
| **LVN (Low Volume Node)** | 低成交量节点，VPVR 中的成交量真空区 |
| **魔数 (Magic Number)** | 代码中直接写死的常量，应通过配置文件管理 |

---

> **最后更新**: 2026-01-17 (v1.3.0 新增状态管理准则与配置准则)  
> **下次审查**: 2026-04-17
