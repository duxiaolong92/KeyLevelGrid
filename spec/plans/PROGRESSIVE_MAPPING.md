# 逐级邻位映射重构计划

> **版本**: 1.0.0  
> **状态**: Planning  
> **关联规格**: SPEC_SELL_MAPPING.md, CONSTITUTION.md v1.2.0

---

## 1. 冲突识别 (Conflict Identification)

### 1.1 基于"平均成本价"的陈旧代码

以下代码违反宪法**原则一：严禁参考平均成本价**：

#### 冲突点 A: `build_recon_actions()` 中的 min_profit_guard

```python
# 文件: position.py, 行 1149
min_price = self.state.avg_entry_price * (1 + self.state.min_profit_pct) \
    if self.state.avg_entry_price > 0 else 0

# 文件: position.py, 行 1206-1210
profit_threshold = min_price
if min_price and existing_orders:
    profit_threshold = min_price * 0.999  # 0.1% 迟滞缓冲

if profit_threshold and lvl.price < profit_threshold:
    # 撤销低于均价利润阈值的卖单
```

**问题**：使用 `avg_entry_price` 作为卖单挂单的先决条件，深套时会导致所有卖单被撤销。

#### 冲突点 B: `build_event_sell_increment()` 中的 min_profit_guard

```python
# 文件: position.py, 行 1313-1316
min_price = self.state.avg_entry_price * (1 + self.state.min_profit_pct) \
    if self.state.avg_entry_price > 0 else 0
```

**问题**：同上，增量卖单也受均价限制。

### 1.2 基于"总仓位"分配的陈旧代码

以下代码违反宪法**原则一：逐级邻位映射**：

#### 冲突点 C: `allocate_sell_targets()` 瀑布流分配

```python
# 文件: position.py, 行 964-997
def allocate_sell_targets(
    self,
    total_sell_qty: float,
    base_amount_per_grid: float,
    min_order_qty: float,
    levels_count: Optional[int] = None,
) -> List[float]:
    """瀑布流分配，返回每层目标数量列表"""
    # ...
    while q_rem > 0 and len(targets) < max_levels:
        q = min(q_rem, base_amount_per_grid)
        targets.append(q)
        q_rem -= q
```

**问题**：
- 将总卖单量瀑布式分配到**所有阻力位**
- 无视 $S_n \to R_{n+1}$ 的映射关系
- 导致低位成交的止盈堆叠到高位阻力

#### 冲突点 D: `build_recon_actions()` 卖单分配

```python
# 文件: position.py, 行 1146-1174
total_sell_qty = self.compute_total_sell_qty(self.state.total_position_contracts)
# ...
targets = self.allocate_sell_targets(
    total_sell_qty,
    base_amount_contracts,
    exchange_min_qty_btc,
    levels_count=len(eligible_levels),
)
```

**问题**：
- 使用总持仓计算总卖单量
- 按"阻力位数量"均匀分配，而非按"支撑位成交"映射

---

## 2. 数据流重构 (Data Flow Restructuring)

### 2.1 新增数据结构

#### 2.1.1 邻位映射表 (Adjacent Level Mapping)

在 `GridState` 中新增：

```python
@dataclass
class GridState:
    # ... 现有字段 ...
    
    # 新增：逐级邻位映射
    level_mapping: Dict[int, int] = field(default_factory=dict)
    # 格式: {support_level_id: adjacent_sell_level_id}
    # 示例: {101: 102, 102: 103, 103: 104}
```

#### 2.1.2 统一水位列表 (Unified Level List)

当前结构：
- `support_levels_state: List[GridLevelState]`
- `resistance_levels_state: List[GridLevelState]`

改为统一列表：
```python
all_levels_state: List[GridLevelState]  # 按价格升序排列
```

或保持双列表，但增加映射关系：
```python
# 在 GridLevelState 中新增
@dataclass
class GridLevelState:
    # ... 现有字段 ...
    
    # 新增：邻位映射
    adjacent_level_id: Optional[int] = None  # 上方第一个水位的 ID
    sell_quota_from: List[int] = field(default_factory=list)  # 哪些支撑位的止盈挂在此处
```

#### 2.1.3 持仓清单扩展 (ActiveFill Extension)

```python
@dataclass
class ActiveFill:
    order_id: str
    price: float
    qty: float
    level_id: int
    timestamp: int
    
    # 新增：映射信息
    target_sell_level_id: Optional[int] = None  # 止盈应挂在哪个水位
    sell_order_id: Optional[str] = None         # 已挂卖单的订单 ID
    sell_qty: float = 0.0                        # 已挂卖单数量
```

### 2.2 state.json 结构变更

**Before**:
```json
{
  "grid_state": {
    "support_levels_state": [...],
    "resistance_levels_state": [...],
    "active_inventory": [
      {"order_id": "...", "price": 94500, "qty": 0.001, "level_id": 101, "timestamp": ...}
    ]
  }
}
```

**After**:
```json
{
  "grid_state": {
    "support_levels_state": [...],
    "resistance_levels_state": [...],
    "level_mapping": {
      "101": 102,
      "102": 103,
      "103": 104
    },
    "active_inventory": [
      {
        "order_id": "...",
        "price": 94500,
        "qty": 0.001,
        "level_id": 101,
        "timestamp": ...,
        "target_sell_level_id": 102,
        "sell_order_id": "...",
        "sell_qty": 0.0007
      }
    ]
  }
}
```

### 2.3 映射计算时机

```
┌─────────────────────────────────────────────────────────────┐
│  create_grid() / rebuild_grid()                             │
│    ↓                                                        │
│  build_level_mapping()  ← 新增：构建邻位映射表               │
│    ↓                                                        │
│  state.level_mapping = {S_1: L_2, S_2: L_3, ...}           │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 原子化操作设计 (Atomic Operation Design)

### 3.1 新函数：`sync_mapping()`

替代旧的 `build_recon_actions()` 中的卖单分配逻辑。

```python
def sync_mapping(
    self,
    current_price: float,
    open_orders: List[Dict],
    exchange_min_qty: float,
) -> List[Dict[str, Any]]:
    """
    逐级邻位映射同步
    
    核心逻辑：
    1. 遍历每个有成交的支撑位 (fill_counter > 0)
    2. 查找其邻位 (level_mapping[level_id])
    3. 计算该邻位应有的卖单配额
    4. 对比实盘挂单，生成补单/撤单动作
    
    原则：
    - 禁止参考 avg_entry_price
    - 一格一出，不堆叠
    - 卖单量 = fill_counter × base_qty × sell_quota_ratio
    
    Returns:
        List of actions: [{"action": "place/cancel", ...}]
    """
```

### 3.2 伪代码实现

```python
def sync_mapping(
    self,
    current_price: float,
    open_orders: List[Dict],
    exchange_min_qty: float,
) -> List[Dict[str, Any]]:
    if not self.state:
        return []
    
    actions = []
    
    # Step 1: 构建交易所挂单索引
    sell_orders_by_level = self._index_orders_by_level(open_orders, side="sell")
    
    # Step 2: 计算每个邻位的期望卖单配额
    expected_quotas = {}  # {target_level_id: expected_qty}
    
    for s_level in self.state.support_levels_state:
        if s_level.fill_counter <= 0:
            continue
        
        # 找到邻位
        target_level_id = self.state.level_mapping.get(s_level.level_id)
        if not target_level_id:
            continue
        
        # 累加配额：fill_counter × base_qty × sell_quota_ratio
        qty = s_level.fill_counter * self.state.base_amount_per_grid * self.state.sell_quota_ratio
        expected_quotas[target_level_id] = expected_quotas.get(target_level_id, 0) + qty
    
    # Step 3: 遍历所有可能的卖单水位，同步配额
    for level_id, expected_qty in expected_quotas.items():
        target_level = self._get_level_by_id(level_id)
        if not target_level:
            continue
        
        # 精度处理
        expected_qty = self._apply_precision(expected_qty, exchange_min_qty, mode="floor")
        
        # 获取实盘挂单量
        existing_orders = sell_orders_by_level.get(level_id, [])
        pending_qty = sum(float(o.get("remaining_qty", 0)) for o in existing_orders)
        
        # Step 4: 生成动作
        deficit = expected_qty - pending_qty
        
        if deficit > exchange_min_qty:
            # 补单
            actions.append({
                "action": "place",
                "side": "sell",
                "price": target_level.price,
                "qty": deficit,
                "level_id": level_id,
                "reason": "mapping_deficit",
            })
        elif deficit < -exchange_min_qty:
            # 冗余撤单 (FIFO)
            surplus = abs(deficit)
            for order in existing_orders:
                if surplus <= 0:
                    break
                order_qty = float(order.get("remaining_qty", 0))
                if order_qty <= surplus:
                    actions.append({
                        "action": "cancel",
                        "side": "sell",
                        "order_id": order.get("id", ""),
                        "level_id": level_id,
                        "reason": "mapping_surplus",
                    })
                    surplus -= order_qty
        # else: 符合预期，静默
    
    # Step 5: 清理无配额但有挂单的水位
    for level_id, orders in sell_orders_by_level.items():
        if level_id not in expected_quotas:
            for order in orders:
                # 检查是否是用户手动挂的单（可选：跳过）
                actions.append({
                    "action": "cancel",
                    "side": "sell",
                    "order_id": order.get("id", ""),
                    "level_id": level_id,
                    "reason": "no_mapping_quota",
                })
    
    return actions
```

### 3.3 辅助函数

```python
def build_level_mapping(self) -> Dict[int, int]:
    """
    构建邻位映射表
    
    规则：每个水位映射到其价格上方的第一个水位
    """
    if not self.state:
        return {}
    
    # 合并所有水位并按价格升序排列
    all_levels = sorted(
        self.state.support_levels_state + self.state.resistance_levels_state,
        key=lambda x: x.price
    )
    
    mapping = {}
    for i, level in enumerate(all_levels):
        # 只为支撑位（或有成交的水位）建立映射
        if level.role == "support" or level.fill_counter > 0:
            # 找到下一个水位
            for j in range(i + 1, len(all_levels)):
                next_level = all_levels[j]
                # 确保有最小利润空间
                if next_level.price > level.price * (1 + self.state.min_profit_pct):
                    mapping[level.level_id] = next_level.level_id
                    break
    
    return mapping

def _get_level_by_id(self, level_id: int) -> Optional[GridLevelState]:
    """根据 ID 获取水位"""
    for lvl in self.state.support_levels_state + self.state.resistance_levels_state:
        if lvl.level_id == level_id:
            return lvl
    return None

def _index_orders_by_level(
    self,
    orders: List[Dict],
    side: str,
    price_tolerance: float = 0.0001
) -> Dict[int, List[Dict]]:
    """按水位索引挂单"""
    result = {}
    for order in orders:
        if order.get("side") != side:
            continue
        
        order_price = float(order.get("price", 0))
        matched_level = self._match_price_to_level(order_price, price_tolerance)
        if matched_level:
            if matched_level.level_id not in result:
                result[matched_level.level_id] = []
            result[matched_level.level_id].append(order)
    
    return result
```

---

## 4. 重构步骤 (Refactoring Steps)

### Phase 1: 数据结构升级
1. [ ] 在 `GridState` 中添加 `level_mapping: Dict[int, int]`
2. [ ] 在 `ActiveFill` 中添加 `target_sell_level_id`, `sell_order_id`, `sell_qty`
3. [ ] 更新 `to_dict()` 和 `from_dict()` 方法
4. [ ] 更新 state.json 兼容性处理

### Phase 2: 映射构建
5. [ ] 实现 `build_level_mapping()` 函数
6. [ ] 在 `create_grid()` 末尾调用 `build_level_mapping()`
7. [ ] 在 `force_rebuild_grid()` 中重建映射

### Phase 3: 同步逻辑替换
8. [ ] 实现 `sync_mapping()` 函数
9. [ ] 在 `build_recon_actions()` 中用 `sync_mapping()` 替代旧的卖单分配逻辑
10. [ ] 移除 `allocate_sell_targets()` 的调用（保留函数备用）
11. [ ] 移除基于 `avg_entry_price` 的 min_profit_guard 逻辑

### Phase 4: 增量事件适配
12. [ ] 更新 `build_event_sell_increment()` 使用逐级映射
13. [ ] 买入成交时自动更新 `ActiveFill.target_sell_level_id`

### Phase 5: 测试与验证
14. [ ] 单元测试：映射构建正确性
15. [ ] 单元测试：同步逻辑补单/撤单
16. [ ] 集成测试：完整 Recon 周期
17. [ ] 实盘验证：小资金测试

---

## 5. 风险与回滚

### 5.1 风险点

| 风险 | 描述 | 缓解措施 |
|------|------|----------|
| **映射缺失** | $S_{max}$ 无上方邻位 | TG 告警提示，建议扩展网格 |
| **挂单冲突** | Event 和 Recon 同时挂单 | `deficit = expected - open - placing` |
| **价格偏移** | 交易所价格微小差异 | `PRICE_TOLERANCE = 0.0001` |
| **精度问题** | 多次成交累加的浮点数 | `apply_precision()` 统一处理 |
| **状态迁移** | 旧 state.json 兼容性 | `from_dict()` 字段默认值 |

### 5.2 边界情况处理

**5.2.1 最高支撑位无邻位**
```python
if target_level is None:
    await notify_alert(
        error_type="MappingWarning",
        error_msg=f"支撑位 {s_level.price} 无上方邻位",
        impact="该水位成交后无法自动挂止盈单"
    )
```

**5.2.2 挂单冲突防御**
```python
# 核心公式
deficit = expected_qty - open_qty - placing_qty

# open_qty: 交易所已存在的挂单量 (OPEN)
# placing_qty: 本地正在处理的挂单量 (PLACING 状态)
```

**5.2.3 价格容差匹配**
```python
PRICE_TOLERANCE = 0.0001  # 0.01%

def price_matches(p1: float, p2: float) -> bool:
    return abs(p1 - p2) / p2 < PRICE_TOLERANCE
```

### 5.3 回滚方案
- 保留旧函数 `allocate_sell_targets()` 不删除
- 在配置中增加开关 `use_progressive_mapping: bool`
- 异常时自动回退到旧逻辑

---

## 6. 代码变更清单

| 文件 | 函数/类 | 变更类型 | 说明 |
|------|---------|----------|------|
| `position.py` | `GridState` | 修改 | 添加 `level_mapping` 字段 |
| `position.py` | `ActiveFill` | 修改 | 添加 `target_sell_level_id` 等字段 |
| `position.py` | `build_level_mapping()` | 新增 | 构建邻位映射表 |
| `position.py` | `sync_mapping()` | 新增 | 替代旧的卖单分配逻辑 |
| `position.py` | `build_recon_actions()` | 修改 | 集成 `sync_mapping()` |
| `position.py` | `build_event_sell_increment()` | 修改 | 使用逐级映射 |
| `position.py` | `create_grid()` | 修改 | 调用 `build_level_mapping()` |

---

> **最后更新**: 2026-01-17  
> **下一步**: 等待审核后开始 Phase 1 实现
