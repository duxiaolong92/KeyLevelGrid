# 库存重建逻辑优化任务清单

> **Sprint**: Inventory Rebuild Hardening  
> **关联文件**: `src/key_level_grid/position.py`  
> **预估工作量**: 3 tasks

---

## 背景

库存重建逻辑 (`validate_and_rebuild_inventory`) 在以下场景触发：
- 检测到无效记录（订单ID不在成交历史中）
- 当前库存数量 ≠ expected_count

在复盘中发现以下潜在问题需要修复。

---

## 已完成

### P0 round() 精度问题 ✅
- **状态**: [x] 已完成 (2026-01-20)
- **问题**: `expected = int(round(grid_holdings / base_qty))` 可能因浮点误差得到错误结果
- **修复**: 使用 `Decimal` + `ROUND_DOWN` 精确计算
- **代码位置**: `position.py` L1150

```python
# 修复后
from decimal import Decimal, ROUND_DOWN

d_holdings = Decimal(str(grid_holdings))
d_base = Decimal(str(base_qty))
expected = int((d_holdings / d_base).quantize(Decimal('1'), rounding=ROUND_DOWN))
```

---

## 待修复

### P1 虚拟订单问题
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **优先级**: P1 (高)
- **问题描述**: 
  - 兜底填充 `_fallback_fill_by_levels()` 会创建 `recon_xxx_xxx` 虚拟订单
  - 这些虚拟订单没有真实成交对应
  - 后续卖出时可能产生逻辑错误
- **代码位置**: `position.py` L1064-1103
- **建议方案**:
  - **方案A**: 在 `ActiveFill` 中增加 `is_virtual: bool = False` 字段，标记虚拟订单
  - **方案B (推荐)**: 取消兜底填充，接受不完整状态，仅记录警告日志
- **验收标准**:
  - [ ] 不再创建虚拟订单，或虚拟订单被正确标记
  - [ ] 卖出逻辑能正确处理虚拟订单（跳过或特殊处理）
  - [ ] 日志清晰记录库存不足情况

```python
# 方案B 示例
if len(new_inventory) < expected_count:
    self.logger.warning(
        f"⚠️ [Inventory] 成交记录不足，接受当前数量 "
        f"({len(new_inventory)} < {expected_count})"
    )
    # 不调用 _fallback_fill_by_levels，接受不完整状态
```

---

### P2 成交记录有限问题
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **优先级**: P2 (中)
- **问题描述**:
  - 交易所 API 只返回最近 N 条成交历史
  - 早期买入记录可能丢失，导致重建不完整
- **代码位置**: `position.py` L1027-1062 (`_merge_trades`)
- **建议方案**:
  - 调整合并逻辑，提升本地 `trades.jsonl` 的权重
  - 本地记录应保留完整历史，交易所记录仅作补充
- **验收标准**:
  - [ ] 本地 `trades.jsonl` 中的买入记录优先保留
  - [ ] 交易所记录用于补充/校验，而非覆盖
  - [ ] 历史买入不因 API 限制而丢失

```python
# 建议逻辑
def _merge_trades(self, recent_trades, local_trades) -> List[Dict]:
    """合并成交记录 - 本地历史优先"""
    merged = {}
    
    # 1. 先加载交易所记录
    for t in recent_trades:
        order_id = str(t.get("order_id") or t.get("id", ""))
        if order_id:
            merged[order_id] = t
    
    # 2. 本地记录补充（本地有更完整的历史）
    for t in local_trades:
        order_id = str(t.get("order_id") or t.get("id", ""))
        if order_id and t.get("side") == "buy":
            if order_id not in merged:
                # 本地有、交易所没有的历史记录 - 保留
                merged[order_id] = t
            else:
                # 合并：保留本地的 level_index
                if t.get("level_index") is not None:
                    merged[order_id]["level_index"] = t["level_index"]
    
    return list(merged.values())
```

---

### P3 level_index 丢失问题
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **优先级**: P3 (低)
- **问题描述**:
  - 若本地 `trades.jsonl` 损坏或丢失
  - 重建时只能根据价格推断 `level_index`
  - 价格推断可能因网格调整而计算错误
- **代码位置**: `position.py` L988-993 (`find_level_index_for_price`)
- **建议方案**:
  - 增加价格容差校验，避免错误归属
  - 价格偏离档位超过阈值时记录警告
- **验收标准**:
  - [ ] 价格推断时验证偏离度 (建议 ±2%)
  - [ ] 偏离过大时记录警告，不强制归属
  - [ ] 考虑增加 `trades.jsonl` 备份机制

```python
# 建议逻辑
def find_level_index_for_price(self, price: float, levels: List) -> int:
    """根据价格找到最近的档位索引"""
    if not levels:
        return 0
    
    best_idx = 0
    best_diff = float('inf')
    TOLERANCE = 0.02  # 2% 容差
    
    for i, level in enumerate(levels):
        diff = abs(level.price - price)
        relative_diff = diff / level.price if level.price > 0 else float('inf')
        
        if relative_diff < TOLERANCE and diff < best_diff:
            best_diff = diff
            best_idx = i
    
    # 若最终偏离仍超过容差，记录警告
    if best_diff / levels[best_idx].price > TOLERANCE:
        self.logger.warning(
            f"⚠️ [Inventory] 价格 {price} 偏离最近档位 {levels[best_idx].price} 超过 {TOLERANCE*100}%"
        )
    
    return best_idx
```

---

## 任务依赖关系

```
P1 (虚拟订单) ─── 独立，可优先处理
        │
        ▼
P2 (成交记录) ─── 依赖 P1 完成后验证效果
        │
        ▼
P3 (level_index) ─ 作为兜底保护，最后处理
```

---

## 进度追踪

| 任务 | 优先级 | 状态 | 完成日期 |
|------|--------|------|----------|
| P0 round() 精度 | P0 | ✅ 完成 | 2026-01-20 |
| P1 虚拟订单 | P1 | ⏳ 待开始 | - |
| P2 成交记录有限 | P2 | ⏳ 待开始 | - |
| P3 level_index 丢失 | P3 | ⏳ 待开始 | - |

---

> **创建日期**: 2026-01-20  
> **最后更新**: 2026-01-20  
> **负责人**: TBD
