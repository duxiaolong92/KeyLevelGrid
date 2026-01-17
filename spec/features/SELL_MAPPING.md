# 卖单映射与配额对账规格说明书

> **版本**: 1.0.0  
> **状态**: Draft  
> **关联宪法**: CONSTITUTION.md v1.1.1 - 原则一、原则二

---

## 1. 概述

本文档详细定义了 Key Level Grid 系统中**卖单挂单的映射算法**、**配额对账逻辑**、**异常处理规格**和**精度处理规则**。

---

## 2. 映射算法 (Sell Order Mapping Algorithm)

### 2.1 核心定义

```
支撑位集合: S = {S_1, S_2, ..., S_n}  (价格升序排列)
阻力位集合: R = {R_1, R_2, ..., R_m}  (价格升序排列)
当前价格: P_current
```

### 2.2 水位角色判定

```python
def determine_level_role(level_price: float, current_price: float) -> str:
    """
    判定水位角色
    
    规则：
    - level_price < current_price → 支撑位 (Support)
    - level_price > current_price → 阻力位 (Resistance)
    - level_price ≈ current_price → 根据 buy_price_buffer_pct 判定
    """
    buffer = current_price * buy_price_buffer_pct
    
    if level_price < current_price - buffer:
        return "support"
    elif level_price > current_price + buffer:
        return "resistance"
    else:
        return "neutral"  # 在缓冲区内，暂不参与
```

### 2.3 逐级邻位映射规则 (Progressive Level Mapping)

当支撑位 $S_n$ 发生买入成交时，系统必须在**该支撑位物理价格之上的第一个有效水位** $L_{n+1}$ 挂出卖单。

**核心原则**：
- **禁止堆叠**：严禁将低位成交的止盈单全部挂在高位阻力位
- **一格一出**：必须保持"一格一出"的局部对冲结构
- **仓位解耦**：卖单量严格执行 `base_qty × sell_quota_ratio`

```python
def find_adjacent_level_above(
    buy_price: float,
    all_levels: List[GridLevelState],
    min_profit_pct: float = 0.0001
) -> Optional[GridLevelState]:
    """
    为买入成交寻找物理邻位（上一格）
    
    映射规则：
    1. 目标水位必须 > buy_price × (1 + min_profit_pct)
    2. 选择 buy_price 上方的第一个水位（物理邻位）
    3. 不考虑当前价格，只考虑物理位置关系
    
    Args:
        buy_price: 买入成交价格
        all_levels: 所有水位列表（已按价格升序排列，包含支撑和阻力）
        min_profit_pct: 最小利润保护阈值
    
    Returns:
        物理邻位，若无有效邻位则返回 None
    """
    min_sell_price = buy_price * (1 + min_profit_pct)
    
    # 找到 buy_price 上方的第一个水位（物理邻位）
    for level in all_levels:
        if level.price > min_sell_price:
            return level
    
    return None  # 无有效邻位（买入价已是最高水位）
```

**边界情况：最高支撑位无邻位**

```
场景：$S_{max}$ (96,000) 是最高支撑位，上方无更高水位

处理方式：
  1. build_level_mapping() 返回空映射
  2. 系统触发 TG 告警：
     "⚠️ 最高支撑位 96,000 无上方邻位，止盈单无法挂出"
  3. 建议用户手动扩展网格上边界
  
代码逻辑：
  if target_level is None:
      await notify_alert(
          error_type="MappingWarning",
          error_msg=f"支撑位 {s_level.price} 无上方邻位",
          impact="该水位成交后无法自动挂止盈单"
      )
```

**与旧逻辑的对比**：

| 维度 | 旧逻辑（堆叠式） | 新逻辑（逐级式） |
|------|------------------|------------------|
| 映射目标 | 当前价格上方的最近阻力位 | 买入价上方的第一个水位 |
| 多个支撑成交 | 全部挂到同一阻力位 | 分别挂到各自的邻位 |
| 结构 | 金字塔堆叠 | 阶梯式一一对应 |

### 2.4 映射示例

```
场景：当前价格 P = 95,200 USDT

全部水位列表（价格升序）:
  L_1 = 93,500
  L_2 = 94,000 (fill_counter=1) ← 支撑位，已成交 1 次
  L_3 = 94,500 (fill_counter=2) ← 支撑位，已成交 2 次
  L_4 = 95,000                  ← 支撑位（未成交）
  L_5 = 95,500                  ← 阻力位
  L_6 = 96,000                  ← 阻力位
  L_7 = 96,500                  ← 阻力位

逐级邻位映射结果：
  L_2 (94,000) × 1 次成交 → L_3 (94,500) 挂 1 × base_qty × sell_quota_ratio
  L_3 (94,500) × 2 次成交 → L_4 (95,000) 挂 2 × base_qty × sell_quota_ratio

计算（base_qty=0.001, sell_quota_ratio=0.7）：
  在 L_3 (94,500) 挂单: 1 × 0.001 × 0.7 = 0.0007 BTC
  在 L_4 (95,000) 挂单: 2 × 0.001 × 0.7 = 0.0014 BTC

❌ 错误的堆叠式映射（旧逻辑）：
  L_2 (94,000) → L_5 (95,500)  ← 跳过了中间水位
  L_3 (94,500) → L_5 (95,500)  ← 所有卖单堆叠到同一阻力位
  
✅ 正确的逐级映射（新逻辑）：
  L_2 (94,000) → L_3 (94,500)  ← 上一格
  L_3 (94,500) → L_4 (95,000)  ← 上一格
```

**特殊情况：邻位已被穿越**

```
场景：当前价格 P = 95,200

L_3 (94,500) 的邻位是 L_4 (95,000)
但 95,000 < 95,200（当前价格已穿越邻位）

处理方式：
  方案 A: 在 95,000 挂限价卖单 → 立即以 Taker 成交（≈95,200）
  方案 B: 跳过本轮，等价格回落到 95,000 以下再挂 Maker 单
  方案 C: 直接在当前价格（95,200）挂限价卖单
  
当前系统采用方案 A：
  - 限价单价格 = 邻位价格 (95,000)
  - 实际成交价 ≈ 当前市价 (95,200) 或更优
  - 订单类型 = Taker（吃单，立即成交）
  - 手续费 = Taker 费率（通常高于 Maker）
  
优点：立即锁定利润，不错过行情
缺点：Taker 手续费较高
```

---

## 3. 配额对账逻辑 (Quota Reconciliation Logic)

### 3.1 核心数据结构

```python
@dataclass
class SellQuotaState:
    """单个阻力位的卖单配额状态"""
    level_id: int
    level_price: float
    
    # 计算值
    expected_qty: float      # 应挂卖单总量
    pending_qty: float       # 当前已挂卖单量（从交易所同步）
    
    # 差异
    deficit: float           # 缺口 = max(expected - pending, 0)
    surplus: float           # 冗余 = max(pending - expected, 0)
```

### 3.2 配额计算算法（逐级邻位版）

```python
def compute_sell_quotas(
    all_levels: List[GridLevelState],  # 所有水位（含支撑和阻力）
    current_price: float,
    base_qty: float,
    sell_quota_ratio: float,
    min_profit_pct: float,
    exchange_min_qty: float
) -> Dict[int, SellQuotaState]:
    """
    计算每个水位应有的卖单配额（逐级邻位映射）
    
    算法步骤：
    1. 遍历所有有成交的水位（fill_counter > 0）
    2. 为每个成交找到其物理邻位（上一格）
    3. 在邻位累加 expected_qty
    4. 应用 sell_quota_ratio 和精度处理
    
    核心原则：
    - 一格一出：每个支撑位的止盈挂在其上一格
    - 禁止堆叠：不同支撑位的止盈分散到各自的邻位
    
    Returns:
        {level_id: SellQuotaState} 字典
    """
    # 按价格升序排列所有水位
    sorted_levels = sorted(all_levels, key=lambda x: x.price)
    
    # 初始化配额状态（所有水位都可能接收卖单）
    quota_map: Dict[int, SellQuotaState] = {}
    for level in sorted_levels:
        quota_map[level.level_id] = SellQuotaState(
            level_id=level.level_id,
            level_price=level.price,
            expected_qty=0.0,
            pending_qty=0.0,
            deficit=0.0,
            surplus=0.0
        )
    
    # 遍历有成交的水位，计算逐级邻位配额
    for i, level in enumerate(sorted_levels):
        if level.fill_counter <= 0:
            continue
        
        # 找到物理邻位（上一格）
        adjacent_level = find_adjacent_level_above(
            buy_price=level.price,
            all_levels=sorted_levels,
            min_profit_pct=min_profit_pct
        )
        
        if adjacent_level is None:
            continue  # 已是最高水位，无邻位
        
        # 在邻位累加配额（一格一出）
        raw_qty = level.fill_counter * base_qty * sell_quota_ratio
        quota_map[adjacent_level.level_id].expected_qty += raw_qty
    
    # 精度处理
    for quota in quota_map.values():
        quota.expected_qty = apply_precision(
            qty=quota.expected_qty,
            min_qty=exchange_min_qty,
            mode="floor"  # 向下取整
        )
    
    # 过滤掉 expected_qty = 0 的水位
    return {k: v for k, v in quota_map.items() if v.expected_qty > 0}
```

**逐级映射 vs 堆叠映射对比**：

```
场景：L_2(94,000)成交1次, L_3(94,500)成交2次, base_qty=0.001, ratio=0.7

┌─────────────────────────────────────────────────────────────┐
│  堆叠式映射（❌ 错误）                                        │
│                                                             │
│  L_2 ──┐                                                    │
│        ├──→ L_5 (95,500): expected = 0.0021 BTC            │
│  L_3 ──┘                                                    │
│                                                             │
│  所有卖单堆叠到同一阻力位                                     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  逐级映射（✅ 正确）                                          │
│                                                             │
│  L_2 (94,000) ──→ L_3 (94,500): expected = 0.0007 BTC      │
│  L_3 (94,500) ──→ L_4 (95,000): expected = 0.0014 BTC      │
│                                                             │
│  每格的止盈挂在其上一格                                       │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 价格容差匹配规范

交易所价格可能存在微小偏移（如 94500.000001），直接 `==` 匹配会失效。

**全局常量定义**：
```python
# 价格容差：0.01%（或 0.1 个最小跳动单位）
PRICE_TOLERANCE = 0.0001

def price_matches(p1: float, p2: float, tolerance: float = PRICE_TOLERANCE) -> bool:
    """判断两个价格是否匹配"""
    if p2 == 0:
        return False
    return abs(p1 - p2) / p2 < tolerance
```

**应用场景**：
- 挂单匹配水位
- 成交价匹配支撑位
- 订单去重检查

### 3.4 挂单冲突防御

**潜在风险**：`sync_mapping` 生成补单动作时，如果 Event 轨道也在挂单，可能导致双倍下单。

**防御公式**：
```
实际缺口 = 期望配额 - 正在处理中的挂单量(PLACING) - 已存在的挂单量(OPEN)
```

**核心数据结构**：
```python
@dataclass
class SellQuotaState:
    level_id: int
    level_price: float
    
    expected_qty: float      # 期望配额（来自 fill_counter 计算）
    open_qty: float          # 已存在的挂单量（交易所 OPEN 状态）
    placing_qty: float       # 正在处理中的挂单量（本地 PLACING 状态）
    
    @property
    def effective_pending(self) -> float:
        """有效已挂/待挂量"""
        return self.open_qty + self.placing_qty
    
    @property
    def deficit(self) -> float:
        """实际缺口"""
        return max(self.expected_qty - self.effective_pending, 0)
    
    @property
    def surplus(self) -> float:
        """实际冗余"""
        return max(self.effective_pending - self.expected_qty, 0)
```

### 3.5 Recon 对账流程（含冲突防御）

```python
async def recon_sell_orders(
    quota_map: Dict[int, SellQuotaState],
    exchange_orders: List[Dict],
    local_level_states: List[GridLevelState],
    price_tolerance: float = PRICE_TOLERANCE
) -> List[ReconAction]:
    """
    Recon 卖单对账主流程（含冲突防御）
    
    步骤：
    1. 同步交易所已挂单到 quota_map.open_qty
    2. 同步本地 PLACING 状态到 quota_map.placing_qty
    3. 计算实际 deficit 和 surplus
    4. 生成补单/撤单动作
    """
    actions: List[ReconAction] = []
    
    # Step 1: 同步交易所挂单 (OPEN)
    for order in exchange_orders:
        if order["side"] != "sell":
            continue
        
        order_price = float(order["price"])
        order_qty = float(order["remaining_qty"])
        
        matched_level = match_order_to_level(order_price, local_level_states, price_tolerance)
        
        if matched_level and matched_level.level_id in quota_map:
            quota_map[matched_level.level_id].open_qty += order_qty
    
    # Step 2: 同步本地 PLACING 状态（防止双倍下单）
    for lvl in local_level_states:
        if lvl.status == LevelStatus.PLACING and lvl.level_id in quota_map:
            quota_map[lvl.level_id].placing_qty += lvl.target_qty
    
    # Step 3: 计算差异（使用 effective_pending）
    # deficit 和 surplus 由 @property 自动计算
    
    # Step 4: 生成动作
    for quota in quota_map.values():
        # 4a. 补单（实际缺口）
        if quota.deficit > exchange_min_qty:
            actions.append(ReconAction(
                action="place",
                side="sell",
                price=quota.level_price,
                qty=quota.deficit,
                reason=f"sell_deficit_at_{quota.level_price}"
            ))
        
        # 4b. 撤单（实际冗余） - 仅撤销 OPEN 状态的订单
        if quota.surplus > exchange_min_qty:
            actions.extend(
                generate_cancel_actions(quota, exchange_orders, price_tolerance)
            )
    
    return actions
```

**冲突防御示例**：
```
场景：L_4 (95,000) 期望配额 = 0.0014 BTC

情况 A：无冲突
  open_qty = 0, placing_qty = 0
  deficit = 0.0014 - 0 - 0 = 0.0014 → 补单 0.0014

情况 B：Event 正在挂单
  open_qty = 0, placing_qty = 0.0014 (PLACING 状态)
  deficit = 0.0014 - 0 - 0.0014 = 0 → 静默，不重复下单

情况 C：部分已挂
  open_qty = 0.0007, placing_qty = 0
  deficit = 0.0014 - 0.0007 - 0 = 0.0007 → 补单 0.0007
```

### 3.4 配额计算示例（逐级邻位版）

```
输入状态：
  current_price = 95,200
  base_qty = 0.001 BTC
  sell_quota_ratio = 0.7
  exchange_min_qty = 0.0001 BTC

全部水位（价格升序）:
  L_1 (93,500): fill_counter = 0
  L_2 (94,000): fill_counter = 1  ← 有成交
  L_3 (94,500): fill_counter = 2  ← 有成交
  L_4 (95,000): fill_counter = 0
  L_5 (95,500): fill_counter = 0
  L_6 (96,000): fill_counter = 0

逐级邻位映射计算：
  L_2 (94,000) → L_3 (94,500): 1 × 0.001 × 0.7 = 0.0007 BTC
  L_3 (94,500) → L_4 (95,000): 2 × 0.001 × 0.7 = 0.0014 BTC

配额结果：
  L_3 (94,500) expected_qty = 0.0007 BTC
  L_4 (95,000) expected_qty = 0.0014 BTC

交易所当前挂单：
  L_3 (94,500): pending_qty = 0.0007 BTC  ← 已挂足
  L_4 (95,000): pending_qty = 0.0000 BTC  ← 缺口

对账结果：
  L_3 (94,500) deficit = 0.0007 - 0.0007 = 0 BTC (无需动作)
  L_4 (95,000) deficit = 0.0014 - 0.0000 = 0.0014 BTC (需补单)
  
动作：在 95,000 补挂 0.0014 BTC 卖单
```

**对比旧逻辑的差异**：

| 维度 | 旧逻辑（堆叠） | 新逻辑（逐级） |
|------|----------------|----------------|
| L_3 挂单量 | 0 | 0.0007 BTC |
| L_4 挂单量 | 0 | 0.0014 BTC |
| L_5 挂单量 | 0.0021 BTC | 0 |
| 止盈分布 | 集中在最高阻力位 | 分散在各自邻位 |

---

## 4. 异常处理规格 (Exception Handling Specification)

### 4.1 缺口补单逻辑 (Deficit Replenishment)

**触发条件**：`quota.deficit > exchange_min_qty`

```python
def handle_sell_deficit(
    quota: SellQuotaState,
    exchange_min_qty: float
) -> Optional[ReconAction]:
    """
    处理卖单缺口
    
    规则：
    1. deficit <= 0: 无需补单
    2. deficit > 0 但 < min_qty: 丢弃（见 Section 5）
    3. deficit >= min_qty: 生成补单动作
    """
    if quota.deficit <= 0:
        return None
    
    # 精度处理
    补单数量 = apply_precision(quota.deficit, exchange_min_qty, mode="floor")
    
    if 补单数量 < exchange_min_qty:
        # 缺口太小，无法补单，记录日志
        logger.debug(
            f"卖单缺口过小，跳过: level={quota.level_price}, "
            f"deficit={quota.deficit}, min_qty={exchange_min_qty}"
        )
        return None
    
    return ReconAction(
        action="place",
        side="sell",
        price=quota.level_price,
        qty=补单数量,
        reason="deficit_replenishment"
    )
```

### 4.2 冗余撤单逻辑 (Surplus Cancellation)

**触发条件**：`quota.surplus > 0`

```python
def generate_cancel_actions(
    quota: SellQuotaState,
    exchange_orders: List[Dict],
    price_tolerance: float
) -> List[ReconAction]:
    """
    处理卖单冗余 - 仅撤销冗余部分
    
    策略：FIFO 撤单（优先撤销最早的订单）
    
    规则：
    1. 计算需要撤销的总量 = surplus
    2. 从匹配的订单中按时间顺序撤销
    3. 每次撤销一整个订单（不支持部分撤销）
    4. 若撤销后仍有微小冗余（< min_qty），容忍不撤
    """
    actions = []
    remaining_surplus = quota.surplus
    
    # 找到该价位的所有挂单，按时间排序
    matching_orders = [
        o for o in exchange_orders
        if o["side"] == "sell" and 
           abs(float(o["price"]) - quota.level_price) / quota.level_price < price_tolerance
    ]
    matching_orders.sort(key=lambda x: x.get("create_time", 0))  # 最早的在前
    
    for order in matching_orders:
        if remaining_surplus <= 0:
            break
        
        order_qty = float(order["remaining_qty"])
        
        # 判断是否需要撤销这个订单
        if order_qty <= remaining_surplus:
            # 完全冗余，撤销整个订单
            actions.append(ReconAction(
                action="cancel",
                order_id=order["id"],
                reason=f"surplus_cancel_at_{quota.level_price}"
            ))
            remaining_surplus -= order_qty
        else:
            # 部分冗余，但不支持部分撤销
            # 选择：容忍冗余 OR 撤销整个订单
            # 当前策略：容忍小额冗余
            logger.info(
                f"卖单部分冗余，容忍: level={quota.level_price}, "
                f"order_qty={order_qty}, remaining_surplus={remaining_surplus}"
            )
            break
    
    return actions
```

### 4.3 异常场景处理矩阵

| 场景 | 条件 | 处理方式 |
|------|------|----------|
| 卖单缺口 | `deficit >= min_qty` | 补挂 deficit 数量的卖单 |
| 卖单微小缺口 | `0 < deficit < min_qty` | 丢弃，记录日志 |
| 卖单完全冗余 | `order_qty <= surplus` | FIFO 撤销整个订单 |
| 卖单部分冗余 | `order_qty > surplus` | 容忍，不撤销 |
| 无有效阻力位 | `find_target_resistance() = None` | 跳过，不挂卖单 |
| 阻力位被穿越 | `r_level.price < current_price` | 该阻力位不参与配额计算 |
| 交易所订单匹配失败 | 价格容差外 | 视为用户手动订单，不处理 |

---

## 5. 精度处理规格 (Precision Handling Specification)

### 5.1 核心函数

```python
import math

def apply_precision(
    qty: float,
    min_qty: float,
    mode: str = "floor"
) -> float:
    """
    精度处理函数
    
    Args:
        qty: 原始数量
        min_qty: 交易所最小交易单位
        mode: 取整模式
            - "floor": 向下取整（默认，保守策略）
            - "ceil": 向上取整
            - "round": 四舍五入
    
    Returns:
        处理后的数量
    """
    if min_qty <= 0:
        return qty
    
    steps = qty / min_qty
    
    if mode == "floor":
        steps = math.floor(steps)
    elif mode == "ceil":
        steps = math.ceil(steps)
    elif mode == "round":
        steps = round(steps)
    else:
        raise ValueError(f"Unknown mode: {mode}")
    
    return steps * min_qty
```

### 5.2 精度处理规则表

| 场景 | 取整模式 | 原因 |
|------|----------|------|
| 卖单配额计算 | `floor` | 避免卖超，保守策略 |
| 买单数量计算 | `ceil` | 确保达到最小单位 |
| 对账缺口计算 | `floor` | 避免过度补单 |
| 仓位保留计算 | `floor` | 保留数量向下取整 |

### 5.3 边界处理

```python
def validate_order_qty(
    qty: float,
    min_qty: float,
    action: str
) -> Tuple[bool, float, str]:
    """
    验证订单数量是否有效
    
    Returns:
        (is_valid, final_qty, reason)
    """
    if qty <= 0:
        return (False, 0, "qty_zero_or_negative")
    
    if qty < min_qty:
        # 低于最小单位
        return (False, 0, f"qty_below_min: {qty} < {min_qty}")
    
    # 精度对齐
    aligned_qty = apply_precision(qty, min_qty, mode="floor")
    
    if aligned_qty < min_qty:
        # 对齐后低于最小单位
        return (False, 0, f"aligned_qty_below_min: {aligned_qty} < {min_qty}")
    
    return (True, aligned_qty, "ok")
```

### 5.4 精度处理示例

```
场景：BTC 最小交易单位 = 0.0001

示例 1：正常情况
  原始 qty = 0.00073
  floor(0.00073 / 0.0001) × 0.0001 = 7 × 0.0001 = 0.0007
  结果：有效，下单 0.0007 BTC

示例 2：低于最小单位
  原始 qty = 0.00005
  floor(0.00005 / 0.0001) × 0.0001 = 0 × 0.0001 = 0
  结果：无效，丢弃订单

示例 3：边界情况
  原始 qty = 0.00015
  floor(0.00015 / 0.0001) × 0.0001 = 1 × 0.0001 = 0.0001
  结果：有效，下单 0.0001 BTC
```

---

## 6. 完整 Recon 流程图

```
┌─────────────────────────────────────────────────────────────┐
│                    Recon Sell Orders                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 1: 获取交易所数据                                      │
│  - get_position() → holdings_btc                            │
│  - get_open_orders() → exchange_orders                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 2: 计算卖单配额                                        │
│  for each support_level with fill_counter > 0:              │
│    target_r = find_target_resistance(support_level.price)   │
│    quota_map[target_r].expected_qty += fill × base × ratio  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 3: 同步交易所挂单                                      │
│  for each sell_order in exchange_orders:                    │
│    matched_level = match_order_to_level(order.price)        │
│    quota_map[matched_level].pending_qty += order.qty        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 4: 计算差异                                            │
│  for each quota in quota_map:                               │
│    quota.deficit = max(expected - pending, 0)               │
│    quota.surplus = max(pending - expected, 0)               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 5: 生成动作                                            │
│  for each quota:                                            │
│    if deficit >= min_qty:                                   │
│      actions.append(PLACE sell @ quota.price, qty=deficit)  │
│    if surplus > 0:                                          │
│      actions.extend(CANCEL matching orders FIFO)            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 6: 执行动作                                            │
│  for each action in actions:                                │
│    if action.type == "place":                               │
│      executor.place_order(...)                              │
│    elif action.type == "cancel":                            │
│      executor.cancel_order(...)                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 7. 代码检查点

| 文件 | 函数 | 对应章节 |
|------|------|----------|
| `position.py` | `find_target_resistance()` | Section 2.3 |
| `position.py` | `compute_sell_quotas()` | Section 3.2 |
| `position.py` | `build_recon_actions()` | Section 3.3 |
| `position.py` | `_match_orders()` | Section 3.3 Step 1 |
| `position.py` | `apply_precision()` | Section 5.1 |
| `strategy.py` | `_run_recon_track()` | Section 6 |
| `strategy.py` | `_execute_recon_actions()` | Section 6 Step 6 |

---

> **最后更新**: 2026-01-17  
> **审核状态**: Pending Review
