# KeyLevelGrid V3.0 渐进式上线指南

> **版本**: 3.0.0
> **更新日期**: 2026-01-17
> **适用环境**: Gate.io 合约交易

---

## 📋 上线前检查清单

### 代码检查

```bash
# 运行所有测试
python -m pytest tests/ -v

# 验证 V3.0 模块导入
PYTHONPATH=src python -c "
from key_level_grid.level_calculator import LevelCalculator
from key_level_grid.data.feeds import MTFKlineFeed
from key_level_grid.strategy.grid import AtomicRebuildExecutor
print('✅ All V3.0 modules OK')
"
```

### 配置检查

```bash
# 验证配置文件语法
python -c "import yaml; yaml.safe_load(open('configs/config_v3_staging.yaml'))"
```

---

## 🚀 三阶段上线流程

### 阶段 1: 模拟环境测试 (1-3 天)

**目标**: 验证 V3.0 功能正确性，无 ALARM 告警

**配置**:
```yaml
mode: "staging"
v3_features:
  level_generation_enabled: true
  score_refresh_only: true  # 🔒 仅评分刷新，不实际重构
```

**监控命令**:
```bash
# 启动策略 (模拟模式)
python scripts/run/single.py --config configs/config_v3_staging.yaml --dry-run

# 监控日志
tail -f logs/v3_staging.log | grep -E "(ALARM|ERROR|Generated.*levels)"
```

**验收标准**:
- [ ] `LevelCalculator` 生成 3-10 个有效水位
- [ ] `MTFKlineFeed.is_synced()` 返回 True
- [ ] 无 `ALARM` 级别日志
- [ ] 无 Python 异常

---

### 阶段 2: 小仓位实盘测试 (3-7 天)

**目标**: 验证订单执行和状态管理

**配置**:
```yaml
mode: "production"
grid:
  per_grid_amount: 10   # 最小仓位 10 USDT
  max_levels: 5         # 最多 5 格

v3_features:
  level_generation_enabled: true
  atomic_rebuild_enabled: true
  score_refresh_only: false  # 🔓 允许实际重构
```

**监控命令**:
```bash
# 启动策略
python scripts/run/single.py --config configs/config_v3_staging.yaml

# 监控重构日志
watch -n 60 'cat state/key_level_grid/gate/btcusdt_state.json | jq ".rebuild_logs[-3:]"'

# 监控订单
watch -n 30 'cat state/key_level_grid/gate/btcusdt_state.json | jq ".support_levels_state | length"'
```

**验收标准**:
- [ ] 订单正确挂载到交易所
- [ ] `fill_counter` 正确递增
- [ ] `RETIRED` 水位正确管理
- [ ] 无"逻辑裸奔"告警

---

### 阶段 3: 正式上线

**目标**: 恢复正常仓位，持续监控

**配置**:
```yaml
mode: "production"
grid:
  per_grid_amount: 100  # 正常仓位
  max_levels: 10        # 正常格数

v3_features:
  level_generation_enabled: true
  atomic_rebuild_enabled: true
  kline_sync_enabled: true
```

**监控**:
- 每日检查 `rebuild_logs` 分析重构原因
- 每周对比 V3 vs V2 水位质量

---

## 🔧 回滚方案

### 快速回滚 (功能关闭)

```yaml
# 修改 config.yaml
v3_features:
  level_generation_enabled: false  # 关闭 V3.0
```

重启策略后自动使用 V2.0 逻辑。

### 完全回滚 (代码回退)

```bash
# 记录当前版本
git log --oneline -1 > /tmp/current_version.txt

# 回退到 V2.0
git checkout v2.0.0

# 重启策略
python scripts/run/single.py --config configs/config.yaml
```

---

## 📊 监控指标

### 关键指标

| 指标 | 正常范围 | 告警阈值 |
|:-----|:---------|:---------|
| 水位生成数 | 5-10 | < 3 |
| 锚点偏移 | < 3% | > 5% |
| 重构频率 | 1-2 次/天 | > 5 次/天 |
| 同步延迟 | < 60s | > 120s |
| ALARM 告警 | 0 | > 0 |

### 日志关键词

```bash
# 正常日志
grep "Generated.*levels" logs/*.log
grep "Rebuild completed" logs/*.log

# 警告日志
grep "WARN" logs/*.log

# 告警日志 (需立即处理)
grep "ALARM\|CRITICAL" logs/*.log
```

---

## 🆘 常见问题

### Q1: 水位生成数为 0

**原因**: K 线数据不足或不同步

**解决**:
```python
# 检查 K 线数据
feed.get_sync_status()

# 增加回溯周期
config["level_generation"]["fibonacci_lookback"] = [8, 13, 21, 34, 55]
```

### Q2: 频繁触发 ALARM

**原因**: 网络问题或 API 限流

**解决**:
```yaml
# 增加重试次数
atomic_rebuild:
  max_retries: 5
  retry_delay_sec: 3
```

### Q3: 水位评分过低

**原因**: 评分阈值过高

**解决**:
```yaml
scoring:
  min_score_threshold: 20  # 降低阈值
```

---

## 📚 参考文档

- [CONSTITUTION.md](../spec/CONSTITUTION.md) - 项目宪法
- [LEVEL_GENERATION.md](../spec/features/LEVEL_GENERATION.md) - 水位生成规格
- [V3_REFACTOR_PLAN.md](../spec/plans/V3_REFACTOR_PLAN.md) - 重构计划

---

## ✅ 上线确认

上线前请确认以下负责人签字:

- [ ] **开发负责人**: _________________ 日期: _______
- [ ] **测试负责人**: _________________ 日期: _______
- [ ] **运维负责人**: _________________ 日期: _______
