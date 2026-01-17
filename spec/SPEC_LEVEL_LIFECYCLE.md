# 水位生命周期规格说明书

> **版本**: 2.0.0  
> **状态**: Draft  
> **关联宪法**: CONSTITUTION.md v1.1.1 - 原则一、原则二  
> **关联规格**: SPEC_SELL_MAPPING.md v1.0.0

---

## 1. 概述

本文档定义了 Key Level Grid 系统中**水位（Level）的完整生命周期**，采用**按索引继承**的简洁算法，确保系统在水位变动时持仓能够"对号入座"。

**核心设计原则**：
1. **严格降序**：水位数组始终按价格从高到低排列
2. **索引对应**：新数组第 i 个继承旧数组第 i 个的状态
3. **三态管理**：ACTIVE（活跃）→ RETIRED（退役）→ DEAD（销毁）

---

## 2. 水位状态机定义

### 2.1 状态枚举

```python
class LevelLifecycleStatus(str, Enum):
    """水位生命周期状态"""
    ACTIVE = "ACTIVE"       # 活跃：允许买入和卖出
    RETIRED = "RETIRED"     # 退役：仅允许卖出清仓
    DEAD = "DEAD"           # 销毁：待物理删除
```

### 2.2 状态行为矩阵

| 状态 | 允许买入 | 允许卖出 | 可作为映射目标 | Recon 对账 |
|------|----------|----------|----------------|------------|
| **ACTIVE** | ✅ | ✅ | ✅ | 买单 + 卖单 |
| **RETIRED** | ❌ | ✅ | ✅ | 仅卖单 |
| **DEAD** | ❌ | ❌ | ❌ | 不参与 |

### 2.3 状态转换图

```
                    ┌─────────────────────────────────────┐
                    │          新水位生成                  │
                    │   (评分引擎 / 手动重建)              │
                    └──────────────┬──────────────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────────────┐
                    │       按索引继承 (Index Inherit)     │
                    │   new[i] ← old[i].fill_counter      │
                    └──────────────┬──────────────────────┘
                                   │
           ┌───────────────────────┼───────────────────────┐
           │                       │                       │
           ▼                       ▼                       ▼
    ┌─────────────┐         ┌─────────────┐         ┌─────────────┐
    │   ACTIVE    │         │   RETIRED   │         │    DEAD     │
    │  (活跃)     │────────▶│  (退役)     │────────▶│  (销毁)     │
    │             │  被挤出  │             │ fc=0    │             │
    │ 可买入/卖出 │  或缩减  │ 仅允许卖出  │ 且无单  │ 物理删除    │
    └─────────────┘         └─────────────┘         └─────────────┘
```

---

## 3. 排序基准 (Sorting Invariant)

### 3.1 严格降序约束

**核心规则**：水位数组必须始终维护**价格严格降序**排列。

```python
def validate_level_order(levels: List[GridLevelState]) -> bool:
    """
    验证水位数组是否满足降序约束
    
    Returns:
        True if levels[0].price > levels[1].price > ... > levels[n].price
    """
    for i in range(len(levels) - 1):
        if levels[i].price <= levels[i + 1].price:
            return False
    return True
```

### 3.2 排序时机

| 时机 | 动作 |
|------|------|
| **水位生成** | 评分引擎输出后立即排序 |
| **网格重建** | 新水位列表排序后再执行继承 |
| **状态加载** | 从 state.json 加载后验证排序 |

### 3.3 排序实现

```python
def sort_levels_descending(levels: List[GridLevelState]) -> List[GridLevelState]:
    """
    将水位按价格降序排列
    
    排序后：levels[0] 是最高价，levels[-1] 是最低价
    """
    return sorted(levels, key=lambda x: x.price, reverse=True)
```

---

## 4. 按索引继承算法 (Index-Based Inheritance)

### 4.1 核心原则

```
新数组:  [N_0, N_1, N_2, ..., N_m]   (价格降序)
旧数组:  [O_0, O_1, O_2, ..., O_n]   (价格降序)

继承规则:
  N_i 继承 O_i 的状态 (i = 0, 1, 2, ..., min(m, n))
  
多余处理:
  若 m > n (新增水位): N_{n+1}, N_{n+2}, ... → ACTIVE, fill_counter=0
  若 m < n (减少水位): O_{m+1}, O_{m+2}, ... → RETIRED
```

### 4.2 继承算法实现

```python
@dataclass
class InheritanceResult:
    """继承结果"""
    active_levels: List[GridLevelState]     # 继承后的活跃水位
    retired_levels: List[GridLevelState]    # 被挤出的退役水位
    orders_to_cancel: List[str]             # 需要撤销的订单
    orders_to_place: List[OrderRequest]     # 需要新挂的订单
    inventory_updates: List[Tuple[str, int, int]]  # (fill_id, old_level_id, new_level_id)


def inherit_levels_by_index(
    new_levels: List[TargetLevel],
    old_levels: List[GridLevelState],
    active_inventory: List[ActiveFill]
) -> InheritanceResult:
    """
    按索引继承水位状态
    
    Args:
        new_levels: 新水位列表（已按价格降序排列）
        old_levels: 旧水位列表（已按价格降序排列）
        active_inventory: 当前持仓记录
    
    Returns:
        InheritanceResult: 继承结果
    """
    result = InheritanceResult(
        active_levels=[],
        retired_levels=[],
        orders_to_cancel=[],
        orders_to_place=[],
        inventory_updates=[],
    )
    
    m = len(new_levels)  # 新数组长度
    n = len(old_levels)  # 旧数组长度
    
    # ========================================
    # Step 1: 按索引一一对应继承
    # ========================================
    for i in range(min(m, n)):
        new_lvl = new_levels[i]
        old_lvl = old_levels[i]
        
        # 创建新水位，继承旧水位的状态
        inherited_level = GridLevelState(
            level_id=generate_new_id(),
            price=new_lvl.price,                    # 使用新价格
            side=old_lvl.side,
            role=old_lvl.role,
            lifecycle_status=LevelLifecycleStatus.ACTIVE,
            fill_counter=old_lvl.fill_counter,      # 继承 fill_counter
            target_qty=old_lvl.target_qty,          # 继承目标数量
        )
        
        result.active_levels.append(inherited_level)
        
        # 撤销旧订单（价格已变化，需要重挂）
        if old_lvl.active_order_id:
            result.orders_to_cancel.append(old_lvl.active_order_id)
            
            # 按新价格重挂
            result.orders_to_place.append(OrderRequest(
                side=old_lvl.side,
                price=new_lvl.price,
                qty=old_lvl.target_qty,
                level_id=inherited_level.level_id,
            ))
        
        # 更新 active_inventory 中的 level_id
        for fill in active_inventory:
            if fill.level_id == old_lvl.level_id:
                result.inventory_updates.append(
                    (fill.order_id, old_lvl.level_id, inherited_level.level_id)
                )
    
    # ========================================
    # Step 2: 处理多余的新水位 (m > n)
    # ========================================
    for i in range(n, m):
        new_lvl = new_levels[i]
        
        # 全新水位，fill_counter = 0
        fresh_level = GridLevelState(
            level_id=generate_new_id(),
            price=new_lvl.price,
            side="buy",  # 默认为支撑位
            role="support",
            lifecycle_status=LevelLifecycleStatus.ACTIVE,
            fill_counter=0,  # 全新，无持仓
        )
        
        result.active_levels.append(fresh_level)
    
    # ========================================
    # Step 3: 处理多余的旧水位 (m < n) → 退役
    # ========================================
    for i in range(m, n):
        old_lvl = old_levels[i]
        
        # 转为 RETIRED（仅允许卖出清仓）
        old_lvl.lifecycle_status = LevelLifecycleStatus.RETIRED
        result.retired_levels.append(old_lvl)
        
        # 若有买单挂单，撤销（退役水位禁止买入）
        if old_lvl.side == "buy" and old_lvl.active_order_id:
            result.orders_to_cancel.append(old_lvl.active_order_id)
    
    return result
```

### 4.3 继承示例

#### 示例 1：等长继承

```
旧水位 (降序):
  [0] O_0 (96,000) fc=1, order=buy_001
  [1] O_1 (94,000) fc=2, order=buy_002
  [2] O_2 (92,000) fc=0, order=buy_003

新水位 (降序):
  [0] N_0 (96,500)
  [1] N_1 (94,500)
  [2] N_2 (92,500)

继承结果:
  N_0 (96,500) ← O_0: fc=1, 撤销 buy_001, 重挂 @ 96,500
  N_1 (94,500) ← O_1: fc=2, 撤销 buy_002, 重挂 @ 94,500
  N_2 (92,500) ← O_2: fc=0, 撤销 buy_003, 重挂 @ 92,500
```

#### 示例 2：新增水位 (m > n)

```
旧水位:
  [0] O_0 (96,000) fc=1
  [1] O_1 (94,000) fc=2

新水位:
  [0] N_0 (96,500)
  [1] N_1 (94,500)
  [2] N_2 (92,500)   ← 新增
  [3] N_3 (90,500)   ← 新增

继承结果:
  N_0 (96,500) ← O_0: fc=1
  N_1 (94,500) ← O_1: fc=2
  N_2 (92,500): fc=0, ACTIVE (全新)
  N_3 (90,500): fc=0, ACTIVE (全新)
```

#### 示例 3：减少水位 (m < n)

```
旧水位:
  [0] O_0 (96,000) fc=1
  [1] O_1 (94,000) fc=2
  [2] O_2 (92,000) fc=1
  [3] O_3 (90,000) fc=0

新水位:
  [0] N_0 (96,500)
  [1] N_1 (94,500)

继承结果:
  N_0 (96,500) ← O_0: fc=1, ACTIVE
  N_1 (94,500) ← O_1: fc=2, ACTIVE
  
  O_2 (92,000) fc=1 → RETIRED (等待清仓)
  O_3 (90,000) fc=0 → RETIRED → 可立即销毁
```

---

## 5. 执行状态机 (State Machine Behaviors)

### 5.1 ACTIVE 状态行为

```python
class ActiveLevelBehavior:
    """活跃水位的行为"""
    
    def can_place_buy(self, level: GridLevelState, current_price: float, config: GridConfig) -> bool:
        """
        是否允许挂买单
        
        条件：
        1. lifecycle_status == ACTIVE
        2. 角色为 support（price < current_price）
        3. 满足 buy_price_buffer_pct
        4. fill_counter < max_fill_per_level
        """
        if level.lifecycle_status != LevelLifecycleStatus.ACTIVE:
            return False
        
        if level.price >= current_price:
            return False
        
        buffer_price = level.price * (1 + config.buy_price_buffer_pct)
        if current_price <= buffer_price:
            return False
        
        if level.fill_counter >= config.max_fill_per_level:
            return False
        
        return True
    
    def can_place_sell(self, level: GridLevelState, avg_price: float, config: GridConfig) -> bool:
        """
        是否允许挂卖单
        
        条件：
        1. lifecycle_status == ACTIVE
        2. 满足 min_profit_pct（利润守卫）
        """
        if level.lifecycle_status != LevelLifecycleStatus.ACTIVE:
            return False
        
        min_sell_price = avg_price * (1 + config.min_profit_pct)
        return level.price > min_sell_price
    
    def can_be_mapping_target(self) -> bool:
        """是否可作为止盈映射目标"""
        return True
```

### 5.2 RETIRED 状态行为

```python
class RetiredLevelBehavior:
    """退役水位的行为"""
    
    def can_place_buy(self, *args, **kwargs) -> bool:
        """退役水位禁止买入"""
        return False
    
    def can_place_sell(self, level: GridLevelState, avg_price: float, config: GridConfig) -> bool:
        """
        是否允许挂卖单
        
        条件：
        1. fill_counter > 0（有存量需要清仓）
        2. 满足 min_profit_pct
        """
        if level.fill_counter <= 0:
            return False
        
        min_sell_price = avg_price * (1 + config.min_profit_pct)
        return level.price > min_sell_price
    
    def can_be_mapping_target(self) -> bool:
        """
        是否可作为止盈映射目标
        
        RETIRED 水位仍可作为目标，只要有盈利即可（Taker 成交）
        """
        return True
    
    def should_transition_to_dead(
        self,
        level: GridLevelState,
        exchange_orders: List[Dict],
        level_mapping: Dict[int, int]
    ) -> Tuple[bool, str]:
        """
        是否应该转为 DEAD
        
        条件：
        1. fill_counter == 0
        2. 交易所无该价位挂单
        3. 无其他水位的卖单映射到此
        """
        if level.fill_counter > 0:
            return False, f"fill_counter={level.fill_counter}"
        
        for order in exchange_orders:
            if price_matches(float(order["price"]), level.price):
                return False, f"存在挂单 {order['id']}"
        
        for src_id, tgt_id in level_mapping.items():
            if tgt_id == level.level_id:
                return False, f"水位 L_{src_id} 的止盈映射到此"
        
        return True, "OK"
```

### 5.3 DEAD 状态处理

```python
def process_dead_level(level: GridLevelState, state: GridState) -> None:
    """
    处理 DEAD 状态的水位 → 物理删除
    """
    # 从活跃列表移除
    state.support_levels_state = [
        lvl for lvl in state.support_levels_state
        if lvl.level_id != level.level_id
    ]
    state.resistance_levels_state = [
        lvl for lvl in state.resistance_levels_state
        if lvl.level_id != level.level_id
    ]
    
    # 从退役列表移除
    state.retired_levels = [
        lvl for lvl in state.retired_levels
        if lvl.level_id != level.level_id
    ]
    
    # 清理映射表
    state.level_mapping = {
        k: v for k, v in state.level_mapping.items()
        if k != level.level_id and v != level.level_id
    }
    
    logger.info(f"水位已销毁: L_{level.level_id} @ {level.price}")
```

---

## 6. 销毁保护机制

### 6.1 销毁前置条件

```python
def can_destroy_level(
    level: GridLevelState,
    exchange_orders: List[Dict],
    level_mapping: Dict[int, int]
) -> Tuple[bool, str]:
    """
    检查水位是否可以销毁
    
    强制条件：
    1. fill_counter == 0
    2. 交易所无该价位挂单
    3. 无其他水位的卖单映射到此（卖单未平仓不能销毁）
    """
    if level.fill_counter > 0:
        return False, f"fill_counter={level.fill_counter}, 有未清仓持仓"
    
    for order in exchange_orders:
        if price_matches(float(order["price"]), level.price):
            return False, f"交易所存在挂单 {order['id']}"
    
    for src_id, tgt_id in level_mapping.items():
        if tgt_id == level.level_id:
            return False, f"水位 L_{src_id} 的止盈仍映射到此"
    
    return True, "OK"
```

### 6.2 保护逻辑图示

```
┌─────────────────────────────────────────────────────────────────┐
│                      销毁保护逻辑                                │
└─────────────────────────────────────────────────────────────────┘

场景：L_sell (94,500) 是 L_buy (94,000) 的止盈目标

情况 A: L_sell 被挤出，需要退役
  → L_sell 转为 RETIRED
  → 仍可作为 L_buy 的止盈目标
  → L_buy 成交后，止盈挂单仍挂在 L_sell

情况 B: L_sell 准备销毁
  → 检查：是否有水位的止盈映射到 L_sell？
  → 若有（L_buy.fill_counter > 0）→ ❌ 禁止销毁
  → 若无（L_buy.fill_counter == 0）→ ✅ 允许销毁

情况 C: L_sell 被新水位继承
  → L_sell_new 接管 L_sell 的索引位置
  → 映射自动转移到 L_sell_new
  → L_sell 不存在于新数组中
```

---

## 7. 执行流程

### 7.1 继承执行顺序（原子性保证）

```python
async def execute_inheritance(
    result: InheritanceResult,
    executor: BaseExecutor,
    state: GridState
) -> None:
    """
    执行继承（原子性保证）
    
    顺序：
    1. 持久化迁移计划
    2. 批量撤销旧订单
    3. 更新内存状态
    4. 批量挂出新订单
    5. 更新 active_inventory
    6. 持久化最终状态
    7. 清理迁移计划
    """
    # Step 1: 持久化迁移计划（用于回滚）
    plan = MigrationPlan(
        timestamp=int(time.time()),
        active_levels=result.active_levels,
        retired_levels=result.retired_levels,
        orders_to_cancel=result.orders_to_cancel,
        orders_to_place=result.orders_to_place,
    )
    save_migration_plan(plan)
    
    try:
        # Step 2: 批量撤销旧订单
        if result.orders_to_cancel:
            await executor.batch_cancel_orders(result.orders_to_cancel)
        
        # Step 3: 更新内存状态
        state.support_levels_state = [
            lvl for lvl in result.active_levels
            if lvl.role == "support"
        ]
        state.resistance_levels_state = [
            lvl for lvl in result.active_levels
            if lvl.role == "resistance"
        ]
        state.retired_levels = result.retired_levels
        
        # Step 4: 批量挂出新订单
        if result.orders_to_place:
            await executor.batch_place_orders(result.orders_to_place)
        
        # Step 5: 更新 active_inventory
        for fill_id, old_id, new_id in result.inventory_updates:
            for fill in state.active_inventory:
                if fill.order_id == fill_id:
                    fill.level_id = new_id
                    break
        
        # Step 6: 持久化最终状态
        save_state(state)
        
        # Step 7: 清理迁移计划
        clear_migration_plan()
        
    except Exception as e:
        logger.error(f"继承执行失败: {e}")
        await rollback_migration(plan, executor, state)
        raise
```

### 7.2 Recon 周期中的生命周期检查

```python
async def recon_lifecycle_check(
    state: GridState,
    exchange_orders: List[Dict]
) -> None:
    """
    Recon 周期中执行生命周期检查
    
    动作：
    1. 检查 RETIRED 水位是否可转 DEAD
    2. 清理 DEAD 水位
    """
    for level in list(state.retired_levels):
        can_destroy, reason = can_destroy_level(
            level, exchange_orders, state.level_mapping
        )
        
        if can_destroy:
            level.lifecycle_status = LevelLifecycleStatus.DEAD
            process_dead_level(level, state)
            logger.info(f"RETIRED → DEAD: L_{level.level_id} @ {level.price}")
        else:
            logger.debug(f"L_{level.level_id} 暂不能销毁: {reason}")
```

---

## 8. 数据结构

### 8.1 GridLevelState 扩展

```python
@dataclass
class GridLevelState:
    """网格水位状态"""
    level_id: int
    price: float
    side: str  # buy | sell
    role: str = "support"  # support | resistance
    
    # 订单状态机
    status: LevelStatus = LevelStatus.IDLE
    
    # 生命周期状态
    lifecycle_status: LevelLifecycleStatus = LevelLifecycleStatus.ACTIVE
    
    # 核心字段
    fill_counter: int = 0
    active_order_id: str = ""
    target_qty: float = 0.0
    open_qty: float = 0.0
    
    # 继承追踪（可选）
    inherited_from_index: Optional[int] = None  # 继承自旧数组的哪个索引
    inheritance_ts: Optional[int] = None
```

### 8.2 GridState 扩展

```python
@dataclass
class GridState:
    """网格状态"""
    symbol: str
    
    # 活跃水位（按价格降序排列）
    support_levels_state: List[GridLevelState] = field(default_factory=list)
    resistance_levels_state: List[GridLevelState] = field(default_factory=list)
    
    # 退役水位（等待清仓）
    retired_levels: List[GridLevelState] = field(default_factory=list)
    
    # 持仓记录
    active_inventory: List[ActiveFill] = field(default_factory=list)
    
    # 邻位映射 {buy_level_id: sell_level_id}
    level_mapping: Dict[int, int] = field(default_factory=dict)
    
    # ... 其他字段
```

---

## 9. 边界情况处理

### 9.1 价格剧烈变动

```
场景：
  旧水位: [96,000, 94,000, 92,000]
  新水位: [100,000, 98,000, 96,000]  ← 整体上移 4000 点

处理：
  按索引继承，不考虑价格差异
  N[0](100,000) ← O[0](96,000) 的 fill_counter
  N[1](98,000)  ← O[1](94,000) 的 fill_counter
  N[2](96,000)  ← O[2](92,000) 的 fill_counter

优点：
  保持"相对位置"的连续性
  最高位继承最高位，次高位继承次高位
```

### 9.2 系统重启恢复

```python
async def recover_from_restart(state_path: Path, executor: BaseExecutor) -> GridState:
    """
    系统重启后的恢复流程
    """
    # 1. 加载状态
    state = load_state(state_path)
    
    # 2. 检查未完成的迁移计划
    migration_plan = load_migration_plan()
    if migration_plan:
        await recover_from_migration_plan(migration_plan, executor, state)
    
    # 3. 验证排序约束
    if not validate_level_order(state.support_levels_state):
        state.support_levels_state = sort_levels_descending(state.support_levels_state)
        logger.warning("支撑位排序已修复")
    
    # 4. 同步交易所状态
    exchange_orders = await executor.get_open_orders()
    await recon_lifecycle_check(state, exchange_orders)
    
    return state
```

---

## 10. 完整示例

### 场景：网格收缩（3 → 2 水位）

```
┌─────────────────────────────────────────────────────────────────┐
│ 时间 T0: 初始状态                                                │
└─────────────────────────────────────────────────────────────────┘

旧水位数组 (降序):
  [0] L_0 (96,000) fc=1, ACTIVE, order=buy_96k
  [1] L_1 (94,000) fc=2, ACTIVE, order=buy_94k
  [2] L_2 (92,000) fc=1, ACTIVE, order=buy_92k

active_inventory:
  fill_001: level_id=L_0, price=95,800, qty=0.001
  fill_002: level_id=L_1, price=93,900, qty=0.001
  fill_003: level_id=L_1, price=94,100, qty=0.001
  fill_004: level_id=L_2, price=92,100, qty=0.001

level_mapping:
  L_0 → L_sell_0 (97,000)
  L_1 → L_sell_1 (95,000)
  L_2 → L_sell_2 (93,000)

┌─────────────────────────────────────────────────────────────────┐
│ 时间 T1: 评分引擎触发，生成新水位                                  │
└─────────────────────────────────────────────────────────────────┘

新水位数组 (降序):
  [0] N_0 (96,500)
  [1] N_1 (94,500)
  
  ↑ 只有 2 个水位，旧数组有 3 个

┌─────────────────────────────────────────────────────────────────┐
│ 时间 T2: 执行按索引继承                                           │
└─────────────────────────────────────────────────────────────────┘

Step 1: 按索引继承 (i = 0, 1)
  N_0 (96,500) ← L_0: fc=1
    - 撤销 buy_96k
    - 重挂 @ 96,500
    - 更新 fill_001.level_id → N_0
    
  N_1 (94,500) ← L_1: fc=2
    - 撤销 buy_94k
    - 重挂 @ 94,500
    - 更新 fill_002.level_id → N_1
    - 更新 fill_003.level_id → N_1

Step 2: 处理多余旧水位 (i = 2)
  L_2 (92,000) fc=1 → RETIRED
    - 撤销 buy_92k（退役禁止买入）
    - fill_004.level_id 保持 L_2（需要在 L_2 清仓）

┌─────────────────────────────────────────────────────────────────┐
│ 时间 T3: 最终状态                                                 │
└─────────────────────────────────────────────────────────────────┘

活跃水位:
  [0] N_0 (96,500) fc=1, ACTIVE
  [1] N_1 (94,500) fc=2, ACTIVE

退役水位:
  L_2 (92,000) fc=1, RETIRED ← 等待 fill_004 的止盈成交

active_inventory:
  fill_001: level_id=N_0
  fill_002: level_id=N_1
  fill_003: level_id=N_1
  fill_004: level_id=L_2 (RETIRED)

┌─────────────────────────────────────────────────────────────────┐
│ 时间 T4: L_2 清仓完成                                             │
└─────────────────────────────────────────────────────────────────┘

fill_004 的止盈单成交
  → L_2.fill_counter = 0
  → Recon 检测: can_destroy_level(L_2) = True
  → L_2 → DEAD → 物理删除
```

---

## 11. 配置参数

```yaml
# config.yaml

level_lifecycle:
  # 继承模式
  inheritance_mode: "index"       # index: 按索引继承
  
  # 销毁保护
  destroy_audit_enabled: true     # 是否启用销毁前审计
  
  # 迁移计划
  migration_plan_ttl_sec: 3600    # 迁移计划保留时间
  
  # 退役宽限期
  retired_grace_period_sec: 0     # 退役后立即可参与销毁检查
```

---

## 12. 代码检查点

| 文件 | 函数/类 | 对应章节 |
|------|---------|----------|
| `position.py` | `LevelLifecycleStatus` | Section 2.1 |
| `position.py` | `GridLevelState` | Section 8.1 |
| `level_manager.py` | `sort_levels_descending()` | Section 3.3 |
| `level_manager.py` | `inherit_levels_by_index()` | Section 4.2 |
| `level_manager.py` | `can_destroy_level()` | Section 6.1 |
| `strategy.py` | `execute_inheritance()` | Section 7.1 |
| `strategy.py` | `recon_lifecycle_check()` | Section 7.2 |

---

> **最后更新**: 2026-01-17  
> **审核状态**: Pending Review
