# 开发规范指南

本文档定义了 KeyLevelGrid 项目的开发规范和最佳实践。

---

## 1. 禁止硬编码

### 规则

**所有可配置的值必须从配置文件或参数中获取，禁止在代码中硬编码。**

### 反面示例 ❌

```python
# 错误：周期硬编码为 "4h"
levels = self._calculate_single_timeframe(klines, price, direction, "4h")

# 错误：直接写死数值
max_orders = 10
leverage = 5
```

### 正确示例 ✅

```python
# 正确：从配置中读取周期
primary_tf = self.config.kline_config.primary_timeframe.value
levels = self._calculate_single_timeframe(klines, price, direction, primary_tf)

# 正确：从配置或参数获取
max_orders = self.config.grid_config.max_orders
leverage = self.config.trading.leverage
```

### 适用范围

- 时间周期（timeframe）
- 杠杆倍数
- 网格档位数量
- 价格阈值
- 交易对符号
- API 端点
- 超时时间
- 任何可能变化的业务参数

---

## 2. 配置优先级

配置加载优先级（从高到低）：

1. 命令行参数
2. 环境变量 (`.env`)
3. 配置文件 (`config.yaml`)
4. 代码中的默认值

---

## 3. 状态持久化

### 注意事项

- 持久化状态文件按 `symbol` 存储在 `state/key_level_grid/{symbol}_state.json`
- 修改配置后，旧状态会被恢复，可能导致新配置不生效
- 如需强制使用新配置，需删除状态文件或通过 Telegram 更新网格

---

## 4. 代码风格

### 命名规范

- 类名：`PascalCase`（如 `KeyLevelGridStrategy`）
- 函数/变量：`snake_case`（如 `calculate_resistance_levels`）
- 常量：`UPPER_SNAKE_CASE`（如 `MAX_RETRY_COUNT`）
- 私有方法：`_leading_underscore`（如 `_update_cycle`）

### 日志规范

```python
# 信息日志：使用 emoji 前缀便于识别
self.logger.info("✅ 网格创建成功")
self.logger.info("🔄 正在更新网格...")
self.logger.warning("⚠️ 配置不完整")
self.logger.error("❌ 订单提交失败")
```

---

## 5. 错误处理

### 规则

- 所有外部 API 调用必须有 try-except
- 捕获具体异常，避免 bare except
- 记录详细错误信息，便于排查

```python
# 正确
try:
    result = await self.api.submit_order(order)
except ccxt.InsufficientFunds as e:
    self.logger.error(f"余额不足: {e}")
except ccxt.NetworkError as e:
    self.logger.error(f"网络错误: {e}")
except Exception as e:
    self.logger.error(f"未知错误: {e}", exc_info=True)
```

---

## 6. 新增功能检查清单

添加新功能前，确认以下事项：

- [ ] 所有可配置值是否从配置获取？
- [ ] 是否添加了必要的日志？
- [ ] 是否有完善的错误处理？
- [ ] 是否需要更新状态持久化？
- [ ] 是否需要添加 Telegram 通知？
- [ ] 是否需要更新配置文件示例？

---

## 7. 提交规范

### Commit Message 格式

```
<type>: <简短描述>

<详细说明（可选）>
```

### Type 类型

- `feat`: 新功能
- `fix`: Bug 修复
- `refactor`: 重构（不改变功能）
- `docs`: 文档更新
- `style`: 代码格式调整
- `test`: 测试相关
- `chore`: 构建/工具相关
