# 水位生命周期重构计划

> **版本**: 1.0.0  
> **状态**: Ready for Implementation  
> **关联规格**: SPEC_LEVEL_LIFECYCLE.md v2.0.0  
> **预估工时**: 4-6 小时

---

## 1. 重构目标

基于《水位生命周期规格说明书 v2.0.0》，实现以下核心功能：

1. **扩展 GridLevelState**：添加 `lifecycle_status` 字段支持三态管理
2. **实现 LevelManager**：核心的 `inherit_levels_by_index()` 算法
3. **安全迁移 state.json**：向后兼容旧格式，自动升级到新格式

---

## 2. 重构优先级与任务分解

### Phase 1: 数据结构扩展 (position.py) 🔴 最高优先级

| 任务 ID | 描述 | 风险 | 工时 |
|---------|------|------|------|
| P1.1 | 新增 `LevelLifecycleStatus` 枚举 | 低 | 10min |
| P1.2 | 扩展 `GridLevelState` 添加 `lifecycle_status` 字段 | 中 | 20min |
| P1.3 | 更新 `GridLevelState.to_dict()` | 低 | 10min |
| P1.4 | 更新 `GridLevelState.from_dict()` (向后兼容) | 中 | 20min |
| P1.5 | 扩展 `GridState` 添加 `retired_levels` 字段 | 低 | 15min |
| P1.6 | 更新 `GridState.to_dict()` | 低 | 10min |

#### P1.1 新增 LevelLifecycleStatus 枚举

```python
# position.py (在 LevelStatus 之后添加)

class LevelLifecycleStatus(str, Enum):
    """水位生命周期状态"""
    ACTIVE = "ACTIVE"       # 活跃：允许买入和卖出
    RETIRED = "RETIRED"     # 退役：仅允许卖出清仓
    DEAD = "DEAD"           # 销毁：待物理删除
```

#### P1.2 扩展 GridLevelState

```python
@dataclass
class GridLevelState:
    """网格水位状态"""
    level_id: int
    price: float
    side: str  # buy | sell
    role: str = "support"  # support | resistance
    
    # 订单状态机（保持原有）
    status: LevelStatus = LevelStatus.IDLE
    
    # 🆕 生命周期状态
    lifecycle_status: LevelLifecycleStatus = LevelLifecycleStatus.ACTIVE
    
    # 原有字段（保持不变）
    active_order_id: str = ""
    order_id: str = ""
    target_qty: float = 0.0
    open_qty: float = 0.0
    filled_qty: float = 0.0
    fill_counter: int = 0
    last_action_ts: int = 0
    last_error: str = ""
    
    # 🆕 继承追踪（可选）
    inherited_from_index: Optional[int] = None
    inheritance_ts: Optional[int] = None
```

#### P1.4 向后兼容的 from_dict

```python
@classmethod
def from_dict(cls, data: dict) -> "GridLevelState":
    # 原有逻辑...
    status = data.get("status", LevelStatus.IDLE)
    try:
        status = LevelStatus(status)
    except Exception:
        status = LevelStatus.IDLE
    
    # 🆕 向后兼容：旧版数据默认为 ACTIVE
    lifecycle_status = data.get("lifecycle_status", "ACTIVE")
    try:
        lifecycle_status = LevelLifecycleStatus(lifecycle_status)
    except Exception:
        lifecycle_status = LevelLifecycleStatus.ACTIVE
    
    return cls(
        # 原有字段...
        lifecycle_status=lifecycle_status,
        inherited_from_index=data.get("inherited_from_index"),
        inheritance_ts=data.get("inheritance_ts"),
    )
```

#### P1.5 扩展 GridState

```python
@dataclass
class GridState:
    # 原有字段...
    
    # 🆕 退役水位列表
    retired_levels: List[GridLevelState] = field(default_factory=list)
```

---

### Phase 2: 创建 LevelManager 模块 🟡 高优先级

| 任务 ID | 描述 | 风险 | 工时 |
|---------|------|------|------|
| P2.1 | 创建 `level_manager.py` 文件 | 低 | 5min |
| P2.2 | 实现 `sort_levels_descending()` | 低 | 10min |
| P2.3 | 实现 `validate_level_order()` | 低 | 10min |
| P2.4 | 实现 `inherit_levels_by_index()` 核心算法 | 高 | 60min |
| P2.5 | 实现 `can_destroy_level()` | 中 | 20min |
| P2.6 | 实现 `execute_inheritance()` 异步执行 | 高 | 40min |
| P2.7 | 添加单元测试 | 中 | 30min |

#### P2.1 文件结构

```
src/key_level_grid/
├── level_manager.py     # 🆕 新增
├── position.py          # 修改
├── strategy.py          # 修改（集成）
└── ...
```

#### P2.4 inherit_levels_by_index 核心算法

```python
# level_manager.py

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
from key_level_grid.position import (
    GridLevelState, 
    LevelLifecycleStatus,
    LevelStatus,
    ActiveFill,
)
from key_level_grid.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class OrderRequest:
    """订单请求"""
    side: str
    price: float
    qty: float
    level_id: int


@dataclass
class InheritanceResult:
    """继承结果"""
    active_levels: List[GridLevelState] = field(default_factory=list)
    retired_levels: List[GridLevelState] = field(default_factory=list)
    orders_to_cancel: List[str] = field(default_factory=list)
    orders_to_place: List[OrderRequest] = field(default_factory=list)
    inventory_updates: List[Tuple[str, int, int]] = field(default_factory=list)


def sort_levels_descending(levels: List[GridLevelState]) -> List[GridLevelState]:
    """
    将水位按价格降序排列
    """
    return sorted(levels, key=lambda x: x.price, reverse=True)


def validate_level_order(levels: List[GridLevelState]) -> bool:
    """
    验证水位数组是否满足降序约束
    """
    for i in range(len(levels) - 1):
        if levels[i].price <= levels[i + 1].price:
            return False
    return True


def _generate_level_id() -> int:
    """生成唯一的 level_id"""
    import time
    import random
    return int(time.time() * 1000) + random.randint(0, 999)


def inherit_levels_by_index(
    new_prices: List[float],
    old_levels: List[GridLevelState],
    active_inventory: List[ActiveFill],
    default_side: str = "buy",
    default_role: str = "support",
) -> InheritanceResult:
    """
    按索引继承水位状态
    
    Args:
        new_prices: 新水位价格列表（已按降序排列）
        old_levels: 旧水位列表（已按降序排列）
        active_inventory: 当前持仓记录
        default_side: 新水位默认方向
        default_role: 新水位默认角色
    
    Returns:
        InheritanceResult: 继承结果
    """
    result = InheritanceResult()
    
    m = len(new_prices)
    n = len(old_levels)
    
    logger.info(f"开始按索引继承: 新水位 {m} 个, 旧水位 {n} 个")
    
    # ========================================
    # Step 1: 按索引一一对应继承
    # ========================================
    for i in range(min(m, n)):
        new_price = new_prices[i]
        old_lvl = old_levels[i]
        
        new_level_id = _generate_level_id()
        
        inherited_level = GridLevelState(
            level_id=new_level_id,
            price=new_price,
            side=old_lvl.side,
            role=old_lvl.role,
            status=LevelStatus.IDLE,  # 重置订单状态
            lifecycle_status=LevelLifecycleStatus.ACTIVE,
            fill_counter=old_lvl.fill_counter,
            target_qty=old_lvl.target_qty,
            inherited_from_index=i,
            inheritance_ts=int(time.time()),
        )
        
        result.active_levels.append(inherited_level)
        
        logger.debug(
            f"  [继承] N[{i}]({new_price:.0f}) ← O[{i}]({old_lvl.price:.0f}): "
            f"fc={old_lvl.fill_counter}"
        )
        
        # 撤销旧订单
        if old_lvl.active_order_id:
            result.orders_to_cancel.append(old_lvl.active_order_id)
            
            # 按新价格重挂
            if old_lvl.target_qty > 0:
                result.orders_to_place.append(OrderRequest(
                    side=old_lvl.side,
                    price=new_price,
                    qty=old_lvl.target_qty,
                    level_id=new_level_id,
                ))
        
        # 更新 active_inventory 中的 level_id
        for fill in active_inventory:
            if fill.level_id == old_lvl.level_id:
                result.inventory_updates.append(
                    (fill.order_id, old_lvl.level_id, new_level_id)
                )
    
    # ========================================
    # Step 2: 处理多余的新水位 (m > n)
    # ========================================
    for i in range(n, m):
        new_price = new_prices[i]
        
        fresh_level = GridLevelState(
            level_id=_generate_level_id(),
            price=new_price,
            side=default_side,
            role=default_role,
            status=LevelStatus.IDLE,
            lifecycle_status=LevelLifecycleStatus.ACTIVE,
            fill_counter=0,
        )
        
        result.active_levels.append(fresh_level)
        
        logger.debug(f"  [新增] N[{i}]({new_price:.0f}): fc=0, ACTIVE")
    
    # ========================================
    # Step 3: 处理多余的旧水位 (m < n) → 退役
    # ========================================
    for i in range(m, n):
        old_lvl = old_levels[i]
        
        old_lvl.lifecycle_status = LevelLifecycleStatus.RETIRED
        result.retired_levels.append(old_lvl)
        
        logger.debug(
            f"  [退役] O[{i}]({old_lvl.price:.0f}): fc={old_lvl.fill_counter} → RETIRED"
        )
        
        # 若有买单挂单，撤销（退役水位禁止买入）
        if old_lvl.side == "buy" and old_lvl.active_order_id:
            result.orders_to_cancel.append(old_lvl.active_order_id)
    
    logger.info(
        f"继承完成: 活跃 {len(result.active_levels)}, "
        f"退役 {len(result.retired_levels)}, "
        f"撤单 {len(result.orders_to_cancel)}, "
        f"挂单 {len(result.orders_to_place)}"
    )
    
    return result


def can_destroy_level(
    level: GridLevelState,
    exchange_orders: List[Dict],
    level_mapping: Dict[int, int],
    price_tolerance: float = 0.0001,
) -> Tuple[bool, str]:
    """
    检查水位是否可以销毁
    
    强制条件：
    1. fill_counter == 0
    2. 交易所无该价位挂单
    3. 无其他水位的卖单映射到此
    """
    if level.fill_counter > 0:
        return False, f"fill_counter={level.fill_counter}, 有未清仓持仓"
    
    for order in exchange_orders:
        order_price = float(order.get("price", 0))
        if order_price > 0 and abs(order_price - level.price) / order_price < price_tolerance:
            return False, f"交易所存在挂单 {order.get('id')} @ {order_price}"
    
    for src_id, tgt_id in level_mapping.items():
        if tgt_id == level.level_id:
            return False, f"水位 L_{src_id} 的止盈仍映射到此"
    
    return True, "OK"
```

---

### Phase 3: state.json 迁移策略 🟢 中优先级

| 任务 ID | 描述 | 风险 | 工时 |
|---------|------|------|------|
| P3.1 | 添加 `state_version` 字段 | 低 | 10min |
| P3.2 | 实现 `migrate_state_v1_to_v2()` | 中 | 30min |
| P3.3 | 更新 `restore_state()` 添加版本检测和迁移 | 中 | 20min |
| P3.4 | 实现 `backup_state()` 备份机制 | 低 | 15min |

#### P3.1 状态版本定义

```python
# position.py

STATE_VERSION = 2  # 当前版本

# state.json 格式:
{
    "state_version": 2,  # 🆕
    "grid_state": {
        "symbol": "BTCUSDT",
        "support_levels_state": [
            {
                "level_id": 1234567890,
                "price": 94000.0,
                "lifecycle_status": "ACTIVE",  # 🆕
                "fill_counter": 2,
                # ...
            }
        ],
        "retired_levels": [  # 🆕
            {
                "level_id": 1234567891,
                "price": 92000.0,
                "lifecycle_status": "RETIRED",
                "fill_counter": 1,
            }
        ],
        # ...
    },
    "trade_history": [...]
}
```

#### P3.2 迁移函数

```python
# position.py

def migrate_state_v1_to_v2(data: dict) -> dict:
    """
    将 v1 格式的 state.json 迁移到 v2 格式
    
    变更:
    1. 添加 state_version = 2
    2. 为所有 GridLevelState 添加 lifecycle_status = "ACTIVE"
    3. 添加空的 retired_levels 列表
    """
    # 设置版本
    data["state_version"] = 2
    
    grid_state = data.get("grid_state", {})
    
    # 迁移 support_levels_state
    for level in grid_state.get("support_levels_state", []):
        if "lifecycle_status" not in level:
            level["lifecycle_status"] = "ACTIVE"
    
    # 迁移 resistance_levels_state
    for level in grid_state.get("resistance_levels_state", []):
        if "lifecycle_status" not in level:
            level["lifecycle_status"] = "ACTIVE"
    
    # 添加 retired_levels
    if "retired_levels" not in grid_state:
        grid_state["retired_levels"] = []
    
    return data


def backup_state(state_file: Path) -> Optional[Path]:
    """
    备份当前状态文件
    
    Returns:
        备份文件路径，失败返回 None
    """
    if not state_file.exists():
        return None
    
    import shutil
    from datetime import datetime
    
    backup_name = f"{state_file.stem}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    backup_path = state_file.parent / backup_name
    
    try:
        shutil.copy2(state_file, backup_path)
        logger.info(f"状态备份: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"备份失败: {e}")
        return None
```

#### P3.3 更新 restore_state

```python
def restore_state(self, current_price: float, price_tolerance: float = 0.02) -> bool:
    """恢复网格状态（支持版本迁移）"""
    if not self.state_file.exists():
        return False
    
    try:
        with self.state_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        self.logger.error(f"读取网格状态失败: {e}")
        return False
    
    # 🆕 版本检测和迁移
    state_version = data.get("state_version", 1)
    
    if state_version < STATE_VERSION:
        self.logger.info(f"检测到旧版状态 v{state_version}，开始迁移到 v{STATE_VERSION}")
        
        # 备份旧状态
        backup_path = backup_state(self.state_file)
        if backup_path:
            self.logger.info(f"旧状态已备份: {backup_path}")
        
        # 执行迁移
        if state_version == 1:
            data = migrate_state_v1_to_v2(data)
        
        # 保存迁移后的状态
        with self.state_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        self.logger.info(f"状态迁移完成: v{state_version} → v{STATE_VERSION}")
    
    # 继续原有的恢复逻辑...
```

---

### Phase 4: 集成到 Strategy 🟢 中优先级

| 任务 ID | 描述 | 风险 | 工时 |
|---------|------|------|------|
| P4.1 | 在 `force_rebuild_grid()` 中集成继承逻辑 | 高 | 40min |
| P4.2 | 在 `_run_recon_track()` 中集成生命周期检查 | 中 | 30min |
| P4.3 | 更新 Telegram 通知 | 低 | 20min |

---

## 3. 文件修改清单

| 文件 | 修改类型 | 影响范围 |
|------|----------|----------|
| `position.py` | **修改** | 数据结构扩展 |
| `level_manager.py` | **新增** | 核心算法 |
| `strategy.py` | **修改** | 集成继承逻辑 |
| `tests/test_level_lifecycle.py` | **新增** | 单元测试 |

---

## 4. 测试计划

### 4.1 单元测试

```python
# tests/test_level_lifecycle.py

import pytest
from key_level_grid.level_manager import (
    inherit_levels_by_index,
    sort_levels_descending,
    validate_level_order,
    can_destroy_level,
)
from key_level_grid.position import (
    GridLevelState,
    LevelLifecycleStatus,
    LevelStatus,
)


class TestSortAndValidate:
    def test_sort_descending(self):
        levels = [
            GridLevelState(level_id=1, price=92000, side="buy"),
            GridLevelState(level_id=2, price=96000, side="buy"),
            GridLevelState(level_id=3, price=94000, side="buy"),
        ]
        sorted_levels = sort_levels_descending(levels)
        assert [l.price for l in sorted_levels] == [96000, 94000, 92000]
    
    def test_validate_order_valid(self):
        levels = [
            GridLevelState(level_id=1, price=96000, side="buy"),
            GridLevelState(level_id=2, price=94000, side="buy"),
            GridLevelState(level_id=3, price=92000, side="buy"),
        ]
        assert validate_level_order(levels) is True
    
    def test_validate_order_invalid(self):
        levels = [
            GridLevelState(level_id=1, price=94000, side="buy"),
            GridLevelState(level_id=2, price=96000, side="buy"),  # 错误：应该更小
        ]
        assert validate_level_order(levels) is False


class TestInheritByIndex:
    def test_equal_length(self):
        """等长继承"""
        old_levels = [
            GridLevelState(level_id=1, price=96000, side="buy", fill_counter=1),
            GridLevelState(level_id=2, price=94000, side="buy", fill_counter=2),
        ]
        new_prices = [96500, 94500]
        
        result = inherit_levels_by_index(new_prices, old_levels, [])
        
        assert len(result.active_levels) == 2
        assert result.active_levels[0].price == 96500
        assert result.active_levels[0].fill_counter == 1
        assert result.active_levels[1].price == 94500
        assert result.active_levels[1].fill_counter == 2
        assert len(result.retired_levels) == 0
    
    def test_expand_grid(self):
        """扩展网格 (m > n)"""
        old_levels = [
            GridLevelState(level_id=1, price=96000, side="buy", fill_counter=1),
        ]
        new_prices = [96500, 94500, 92500]
        
        result = inherit_levels_by_index(new_prices, old_levels, [])
        
        assert len(result.active_levels) == 3
        assert result.active_levels[0].fill_counter == 1  # 继承
        assert result.active_levels[1].fill_counter == 0  # 新增
        assert result.active_levels[2].fill_counter == 0  # 新增
    
    def test_shrink_grid(self):
        """收缩网格 (m < n)"""
        old_levels = [
            GridLevelState(level_id=1, price=96000, side="buy", fill_counter=1),
            GridLevelState(level_id=2, price=94000, side="buy", fill_counter=2),
            GridLevelState(level_id=3, price=92000, side="buy", fill_counter=1),
        ]
        new_prices = [96500, 94500]
        
        result = inherit_levels_by_index(new_prices, old_levels, [])
        
        assert len(result.active_levels) == 2
        assert len(result.retired_levels) == 1
        assert result.retired_levels[0].lifecycle_status == LevelLifecycleStatus.RETIRED
        assert result.retired_levels[0].fill_counter == 1


class TestCanDestroy:
    def test_can_destroy_empty(self):
        level = GridLevelState(
            level_id=1, price=94000, side="buy",
            fill_counter=0,
            lifecycle_status=LevelLifecycleStatus.RETIRED,
        )
        can, reason = can_destroy_level(level, [], {})
        assert can is True
    
    def test_cannot_destroy_has_counter(self):
        level = GridLevelState(
            level_id=1, price=94000, side="buy",
            fill_counter=1,
            lifecycle_status=LevelLifecycleStatus.RETIRED,
        )
        can, reason = can_destroy_level(level, [], {})
        assert can is False
        assert "fill_counter" in reason
    
    def test_cannot_destroy_has_mapping(self):
        level = GridLevelState(
            level_id=100, price=94000, side="buy",
            fill_counter=0,
        )
        level_mapping = {50: 100}  # 水位 50 的止盈映射到 100
        can, reason = can_destroy_level(level, [], level_mapping)
        assert can is False
        assert "映射" in reason
```

### 4.2 集成测试

```bash
# 测试步骤
1. 备份现有 state.json
2. 启动策略，验证迁移日志
3. 手动触发 /rebuild，验证继承逻辑
4. 检查退役水位的清仓行为
5. 验证 RETIRED → DEAD 转换
```

---

## 5. 回滚计划

### 5.1 代码回滚

```bash
# 如果出现严重问题，回滚到上一个提交
git revert HEAD
```

### 5.2 状态回滚

```bash
# 恢复备份的状态文件
cp state/key_level_grid/gate/btcusdt_state_backup_*.json \
   state/key_level_grid/gate/btcusdt_state.json
```

---

## 6. 实施顺序

```
┌─────────────────────────────────────────────────────────────────┐
│ Step 1: position.py 数据结构扩展                                 │
│         - LevelLifecycleStatus                                   │
│         - GridLevelState 扩展                                    │
│         - to_dict/from_dict 向后兼容                             │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 2: 创建 level_manager.py                                   │
│         - inherit_levels_by_index()                             │
│         - sort/validate 函数                                    │
│         - can_destroy_level()                                   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 3: state.json 迁移                                         │
│         - 版本检测                                               │
│         - 自动迁移                                               │
│         - 备份机制                                               │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 4: 单元测试                                                 │
│         - test_level_lifecycle.py                               │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 5: Strategy 集成                                            │
│         - force_rebuild_grid()                                   │
│         - _run_recon_track()                                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 6: 集成测试 & 提交                                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. 检查清单

### 完成标准

- [ ] `LevelLifecycleStatus` 枚举已添加
- [ ] `GridLevelState.lifecycle_status` 字段已添加
- [ ] `GridLevelState.from_dict()` 向后兼容旧格式
- [ ] `GridState.retired_levels` 字段已添加
- [ ] `level_manager.py` 已创建
- [ ] `inherit_levels_by_index()` 已实现并测试
- [ ] `can_destroy_level()` 已实现
- [ ] `state.json` 迁移逻辑已实现
- [ ] 单元测试通过
- [ ] 集成测试通过
- [ ] 代码已提交

---

> **最后更新**: 2026-01-17  
> **审核状态**: Ready for Implementation
