# 逐级邻位映射重构任务清单

> **Sprint**: Progressive Mapping Refactor  
> **关联计划**: PLAN_PROGRESSIVE_MAPPING.md  
> **预估工作量**: 17 tasks

---

## Phase 1: 数据结构升级

### T1.1 GridState 添加 level_mapping 字段
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 在 `GridState` dataclass 中添加 `level_mapping: Dict[int, int]` 字段
- **验收标准**:
  - [ ] 字段定义: `level_mapping: Dict[int, int] = field(default_factory=dict)`
  - [ ] `to_dict()` 序列化包含 level_mapping
  - [ ] `from_dict()` 反序列化支持 level_mapping

### T1.2 ActiveFill 添加映射追踪字段
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 扩展 `ActiveFill` 以追踪止盈单状态
- **验收标准**:
  - [ ] 新增字段: `target_sell_level_id: Optional[int] = None`
  - [ ] 新增字段: `sell_order_id: Optional[str] = None`
  - [ ] 新增字段: `sell_qty: float = 0.0`
  - [ ] `to_dict()` 和 `from_dict()` 更新

### T1.3 state.json 兼容性处理
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 确保旧版 state.json 可以正常加载
- **验收标准**:
  - [ ] `from_dict()` 对缺失字段有默认值
  - [ ] 加载旧版 state.json 不报错
  - [ ] 保存后包含新字段

### T1.4 单元测试: 数据结构序列化
- **文件**: `tests/test_position.py` (新建)
- **状态**: [ ] 待开始
- **描述**: 验证数据结构的序列化/反序列化
- **验收标准**:
  - [ ] 测试 GridState 序列化包含 level_mapping
  - [ ] 测试 ActiveFill 序列化包含新字段
  - [ ] 测试兼容性：旧数据可加载

---

## Phase 2: 映射构建

### T2.1 实现 build_level_mapping() 函数
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 构建邻位映射表 $S_n \to L_{n+1}$
- **验收标准**:
  - [ ] 合并所有水位并按价格升序排列
  - [ ] 每个支撑位映射到其上方第一个水位
  - [ ] 遵守 min_profit_pct 最小利润间距
  - [ ] 返回 `Dict[int, int]` 格式
  - [ ] **边界处理**: 若 $S_{max}$ 无上方邻位，触发 TG 告警

```python
def build_level_mapping(self) -> Dict[int, int]:
    """构建邻位映射表"""
    pass

# 边界处理示例
if target_level is None:
    # 触发告警
    await self._notify_alert(
        error_type="MappingWarning",
        error_msg=f"支撑位 {s_level.price} 无上方邻位",
        impact="该水位成交后无法自动挂止盈单"
    )
```

### T2.2 在 create_grid() 中调用映射构建
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 网格创建时自动构建映射
- **验收标准**:
  - [ ] `create_grid()` 末尾调用 `build_level_mapping()`
  - [ ] 映射结果存入 `self.state.level_mapping`
  - [ ] 日志输出映射关系

### T2.3 在 force_rebuild_grid() 中重建映射
- **文件**: `src/key_level_grid/strategy.py`
- **状态**: [ ] 待开始
- **描述**: 强制重建时更新映射
- **验收标准**:
  - [ ] `force_rebuild_grid()` 调用 `build_level_mapping()`
  - [ ] Telegram 命令触发时映射同步更新

---

## Phase 3: 同步逻辑替换

### T3.1 实现 sync_mapping() 函数
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 核心同步函数，替代旧的卖单分配逻辑
- **验收标准**:
  - [ ] 遍历每个 fill_counter > 0 的支撑位
  - [ ] 查找邻位: `level_mapping[level_id]`
  - [ ] 计算期望配额: `fill_counter × base_qty × sell_quota_ratio`
  - [ ] 对比实盘挂单，生成补单/撤单动作
  - [ ] 严禁参考 avg_entry_price
  - [ ] **冲突防御**: 缺口计算必须扣除 PLACING 状态的挂单量

```python
def sync_mapping(
    self,
    current_price: float,
    open_orders: List[Dict],
    exchange_min_qty: float,
) -> List[Dict[str, Any]]:
    """逐级邻位映射同步"""
    pass

# 冲突防御公式
deficit = expected_qty - open_qty - placing_qty
# open_qty: 交易所已存在的挂单量
# placing_qty: 本地 PLACING 状态的待挂单量
```

### T3.2 实现辅助函数 _index_orders_by_level()
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 按水位索引交易所挂单
- **验收标准**:
  - [ ] 输入: 交易所挂单列表
  - [ ] 输出: `Dict[level_id, List[order]]`
  - [ ] **价格容差匹配**: 使用 `PRICE_TOLERANCE = 0.0001` (0.01%)

```python
# 全局常量
PRICE_TOLERANCE = 0.0001  # 0.01%

def price_matches(p1: float, p2: float, tolerance: float = PRICE_TOLERANCE) -> bool:
    """判断两个价格是否匹配"""
    if p2 == 0:
        return False
    return abs(p1 - p2) / p2 < tolerance
```

### T3.3 集成 sync_mapping() 到 build_recon_actions()
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 替换旧的卖单分配逻辑
- **验收标准**:
  - [ ] 移除 `allocate_sell_targets()` 调用
  - [ ] 移除基于 `avg_entry_price` 的 min_profit_guard
  - [ ] 使用 `sync_mapping()` 返回的卖单动作
  - [ ] 保留买单逻辑不变

### T3.4 移除 avg_entry_price 依赖
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 清理所有卖单决策中的均价依赖
- **代码位置**:
  - [ ] L1149: `min_price = avg_entry_price * (1 + min_profit_pct)` → 删除
  - [ ] L1206-1210: profit_threshold 逻辑 → 删除
  - [ ] L1313-1316: build_event_sell_increment 中的 min_price → 删除
- **验收标准**:
  - [ ] `avg_entry_price` 仅用于展示/统计
  - [ ] 卖单挂单不受均价影响

---

## Phase 4: 增量事件适配

### T4.1 更新 build_event_sell_increment()
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 使用逐级映射处理增量卖单
- **验收标准**:
  - [ ] 买入成交时查找邻位映射
  - [ ] 在邻位挂出 `base_qty × sell_quota_ratio` 卖单
  - [ ] 不再使用总仓位计算

### T4.2 买入成交时更新 ActiveFill.target_sell_level_id
- **文件**: `src/key_level_grid/position.py`
- **状态**: [ ] 待开始
- **描述**: 记录每笔买入对应的卖单目标水位
- **验收标准**:
  - [ ] `increment_fill_counter_by_order()` 中设置 `target_sell_level_id`
  - [ ] 从 `level_mapping` 查找目标水位
  - [ ] 持久化到 state.json

---

## Phase 5: 测试与验证

### T5.1 单元测试: build_level_mapping()
- **文件**: `tests/test_position.py`
- **状态**: [ ] 待开始
- **测试用例**:
  - [ ] 正常映射: 3 支撑位 + 3 阻力位
  - [ ] 边界: 最高支撑位无邻位
  - [ ] 密集水位: 间距 < min_profit_pct

### T5.2 单元测试: sync_mapping()
- **文件**: `tests/test_position.py`
- **状态**: [ ] 待开始
- **测试用例**:
  - [ ] 补单: 有成交但无卖单
  - [ ] 撤单: 卖单量 > 期望
  - [ ] 静默: 卖单量 = 期望
  - [ ] 精度: 小于 min_qty 的情况

### T5.3 集成测试: 完整 Recon 周期
- **文件**: `tests/test_integration.py`
- **状态**: [ ] 待开始
- **测试场景**:
  - [ ] 多个支撑位成交 → 各自邻位挂卖单
  - [ ] 卖单成交 → 支撑位回补买单
  - [ ] 重建网格 → 映射重新计算

### T5.4 实盘验证: 小资金测试
- **状态**: [ ] 待开始
- **验收标准**:
  - [ ] 使用 0.001 BTC base_qty 测试
  - [ ] 观察 3 个以上 Recon 周期
  - [ ] 确认卖单挂在正确的邻位
  - [ ] 无异常撤单/补单循环

---

## 任务依赖关系

```
T1.1 ──┬── T1.3 ── T1.4
T1.2 ──┘
       │
       ▼
T2.1 ── T2.2 ── T2.3
       │
       ▼
T3.1 ── T3.2 ── T3.3 ── T3.4
                │
                ▼
           T4.1 ── T4.2
                │
                ▼
      T5.1 ── T5.2 ── T5.3 ── T5.4
```

---

## 进度追踪

| Phase | 任务数 | 完成 | 进度 |
|-------|--------|------|------|
| Phase 1 | 4 | 0 | 0% |
| Phase 2 | 3 | 0 | 0% |
| Phase 3 | 4 | 0 | 0% |
| Phase 4 | 2 | 0 | 0% |
| Phase 5 | 4 | 0 | 0% |
| **Total** | **17** | **0** | **0%** |

---

> **最后更新**: 2026-01-17  
> **负责人**: TBD
