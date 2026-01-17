# Key Level Grid 项目宪法 (Constitution)

> **版本**: 1.2.0  
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

---

> **最后更新**: 2026-01-17  
> **下次审查**: 2026-04-17
