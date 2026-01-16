# Key Level Grid 需求规格说明书 (V3.0 - 终极整合版)

## 1. 系统架构：双轨异步驱动机制

系统基于 `asyncio.Lock` 维护核心状态 `GridMap`，通过两套并行逻辑确保响应速度与最终一致性。

### 1.1 事件驱动轨道 (Event Track) - 微观反射

- **触发源**：WebSocket 订单成交事件 (`FILLED`)。
- **核心逻辑**：
  - **买成交反射**：立即重算均价，按 `sell_quota_ratio` 在上方最近有效阻力位执行“增量补卖”。
  - **卖成交反射**：释放对应支撑位的 `fill_counter` 配额，并清理该阻力位状态。
- **特点**：低延迟、单点处理、不扫描全局。

### 1.2 全局对账轨道 (Recon Track) - 宏观守卫

- **执行周期**：`recon_interval_sec` (默认 60s)。
- **核心逻辑**：
  - **极性与空间对账**：根据现价划分水位角色，并校验买/卖缓冲区。
  - **瀑布流重排**：基于“利润避让”原则，确保 70% 止盈单在满足利润的水位上物理对齐。
  - **自愈机制**：修复漏单、多单、及因均价漂移导致的无效止盈单。

---

## 2. 核心逻辑算法 (Algorithms)

### 2.1 物理极性判定与空间守卫

水位角色随现价实时变化，但挂单受缓冲区保护：

- **支撑位 (Support)**：`Level_Price < Current_Price`。
- **买单挂出条件**：角色为 Support，且 `Current_Price > Level_Price * (1 + buy_price_buffer_pct)`。

- **阻力位 (Resistance)**：`Level_Price > Current_Price`。
- **卖单挂出条件**：角色为 Resistance，且
  - 空间校验：`Current_Price < Level_Price * (1 - sell_price_buffer_pct)`，
  - 利润校验：`Level_Price > Avg_Entry_Price * (1 + min_profit_pct)`。

### 2.2 水位配额计数器 (Quota Counter)

- **限额逻辑**：每个水位拥有 `fill_counter`。买单成交则 `fill_counter += 1`。
- **回补逻辑**：当 `fill_counter >= max_fill_per_level`（默认 1）时，禁止在该水位补买单。
- **释放逻辑**：仅当该水位产生的筹码在阻力位被卖出后，计数器重置为 `0`。

### 2.3 利润避让瀑布流 (Profit-Guard Waterfall)

在分配 `Total_Sell_Qty` 时，执行以下流程：

1. **利润过滤**：跳过所有 `Level_Price <= Avg_Entry_Price * (1 + min_profit_pct)` 的水位。
2. **物理分配**：从最近的合格阻力位开始，按 `base_amount_per_grid` 阶梯填充。
3. **增量改单**：若目标数量与实盘数量不符，执行“物理合并改单”（撤旧挂新或 Amend）。

---

## 3. 详细动作分解 (Execution Details)

### 3.1 增量改单 (Incremental Adjustment)

- **定义**：当水位已存在活跃订单时，不创建新订单，而是修改原单。
- **公式**：`delta_qty = target_qty - open_qty`。
- **执行**：
  - 若 `delta_qty > 0`：执行增量补单；
  - 若 `delta_qty < 0`：执行减量改单（不支持 Amend 则撤旧挂新）。

### 3.2 极性翻转处理 (Polarity Flip)

- **防冲突撤单**：当水位从 Support 变为 Resistance 时，Recon 必须先撤销所有买单。
- **角色重置**：清理 `fill_counter` 状态（视策略可选），重新根据瀑布流算法评估是否挂卖单。

---

## 4. 状态机与冲突防御 (FR-STATE)

| 状态 | 说明 | 轨道冲突规则 |
| --- | --- | --- |
| **IDLE** | 无任务 | 两轨均可发起 `PLACING` |
| **PLACING** | API 调用中 | 互斥锁锁定。另一轨道必须跳过此水位 |
| **ACTIVE** | 已挂单 | Event 等待成交；Recon 负责增量对齐或撤单 |
| **FILLED** | 待处理 | Event 独占处理补单反射，完成后转 `IDLE` |
| **CANCELING** | 撤单中 | 互斥锁锁定。禁止其他一切操作 |

---

## 5. 风控与异常处理 (FR-RISK)

1. **止损全覆盖**：Recon 每周期更新 `reduce_only` 止损单，数量覆盖 `Current_Holdings`。
2. **API 容错**：若 Event 补单失败，不进行重试，标记水位为 `IDLE`，由 Recon 在下一分钟自动修复。
3. **资金保护**：若 `Total_Sell_Qty` 计算结果异常，或所有阻力位均不满足利润避让，系统应停止挂出任何卖单。

---

## 6. AI 开发指令 (Developer Directives)

1. **原子化操作**：所有 API 写入（下单/撤单）必须封装在 Level-Level Lock 异步锁中。
2. **幂等性设计**：Recon 每次同步前必须重新从交易所拉取 Open Orders 镜像，严禁依赖内存状态做减法。
3. **数据口径**：内部逻辑使用“币数量”，物理执行前转换为“张数”。最小单位不足一格时向下合并。
4. **持久化要求**：`fill_counter`、`Avg_Price` 必须实时落地 JSON，确保重启后“配额锁定”依然有效。

---

## 7. 验收场景建议

- **震荡市测试**：验证在买单成交且对应卖单未成交前，同一水位是否保持 `IDLE` 且不挂单。
- **利润规避测试**：手动拉高 `min_profit_pct`，验证 Recon 是否自动撤销过于接近均价的低位卖单。
- **暴力极性测试**：手动向交易所下入大额头寸，验证 Recon 是否在上方阻力位按瀑布流铺开卖单。

---

# 补充需求：网格重置与冷启动对齐逻辑 (FR-RESET)

## 1. 重置场景定义

系统必须能够处理以下三种重置场景：

1. **冷启动恢复 (Cold Start)**：程序崩溃或手动重启。
2. **清仓重置 (Zero-Position Reset)**：由于止损或手动清仓导致持仓归零。
3. **手动强制重置 (Manual Override)**：用户通过 Telegram 指令强制清空计数器。

## 2. 核心处理逻辑

### 2.1 冷启动：实盘反向推导 (Reverse Reconciliation)

- **原则**：以交易所持仓为准，反向锁定水位。
- **逻辑流程**：
  1. 获取当前账户 `Total_Holdings`。
  2. 计算需要锁定的配额总数：`Total_Holdings / base_amount_per_grid`（向下取整）。
  3. 从当前价格向下支撑位按从远到近锁定 `fill_counter`，直到达到配额总数。

### 2.2 自动归零逻辑 (Auto-Clear)

- **触发点**：Recon 周期发现 `Total_Holdings == 0`。
- **动作**：清空所有水位 `fill_counter`。

### 2.3 局部释放逻辑 (Incremental Reset)

- **触发点**：Event 捕获到止盈单成交。
- **动作**：按 `filled_qty / base_amount_per_grid` 释放配额。
- **优先级**：优先释放价格最低的支撑位 `fill_counter`。

## 3. 状态一致性守卫 (Consistency Guard)

- `sum(fill_counter) * base_amount_per_grid` 必须与实盘持仓量一致。
- 若差异超过 1 个网格单位，Recon 必须告警并触发反向推导。
- 重置操作需持有全局 `GridLock`。
