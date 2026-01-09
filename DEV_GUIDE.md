# 开发指南 & 架构索引 (Developer Guide & Architecture Index)

本文档定义了 KeyLevelGrid 项目的开发规范、架构说明和最佳实践。

---

## 1. 系统架构与目录映射 (Architecture & Directory Map)

KeyLevelGrid 采用模块化分层架构。以下是核心目录及关键文件说明：

### `src/key_level_grid/` (核心模块)

| 文件/目录 | 说明 |
|-----------|------|
| `strategy.py` | **核心**。策略主逻辑，包括网格创建、重建、信号处理 |
| `position.py` | 仓位管理器，网格状态持久化 |
| `resistance.py` | 支撑/阻力位计算器（多周期融合） |
| `models.py` | 数据模型定义（Kline, PriceLevel, Timeframe 等） |
| `kline_feed.py` | K 线数据源（Binance WebSocket） |
| `executor/` | 订单执行层 |
| `telegram/` | Telegram Bot 通知模块 |

### `src/key_level_grid/executor/` (执行层)

| 文件 | 说明 |
|------|------|
| `base.py` | 执行器基类 |
| `gate_executor.py` | **核心**。Gate.io 专用执行器，封装 API 调用、重试、错误处理 |

### `src/key_level_grid/telegram/` (通知层)

| 文件 | 说明 |
|------|------|
| `bot.py` | Telegram Bot 命令处理、菜单交互 |
| `notify.py` | 通知管理器，消息模板 |

### `scripts/` (运行脚本)

| 文件 | 说明 |
|------|------|
| `run.py` | 策略启动脚本，包含 Rich UI 显示 |

### `configs/` (配置文件)

| 文件 | 说明 |
|------|------|
| `config.yaml` | 主配置文件 |

### `state/` (状态持久化)

| 目录 | 说明 |
|------|------|
| `key_level_grid/` | 网格状态文件 (`{symbol}_state.json`) |

---

## 2. 策略概述 (Strategy Overview)

### 关键位网格策略 (Key Level Grid)

**逻辑**: 基于**支撑位/阻力位**的智能网格交易策略。

- **价位来源**: 
  - SW (摆动点)
  - VOL (成交量密集区)
  - FIB (斐波那契)
  - PSY (心理关口)
- **多周期融合**: 主周期 + 辅助周期共振增强强度
- **仓位管理**: 强支撑位分配更大仓位，递减策略防止深套
- **风控**: 跌破网格底线止损，仓位上限保护

**启动方式**:
```bash
PYTHONPATH=src python scripts/run.py --config configs/config.yaml
```

---

## 3. 开发规范 (Development Rules)

### 3.1. 禁止硬编码 (No Hardcoding) ⚠️ Critical

**所有可配置的值必须从配置文件或参数中获取，严禁在代码中硬编码。**

#### 反面示例 ❌

```python
# 错误：周期硬编码
levels = self._calculate_single_timeframe(klines, price, direction, "4h")

# 错误：直接写死数值
max_orders = 10
leverage = 5
contract_size = 0.0001
```

#### 正确示例 ✅

```python
# 正确：从配置中读取
primary_tf = self.config.kline_config.primary_timeframe.value
levels = self._calculate_single_timeframe(klines, price, direction, primary_tf)

# 正确：从配置或 API 获取
max_orders = self.config.grid_config.max_orders
leverage = self.config.trading.leverage
contract_size = market_info.get('contractSize', 1.0)
```

#### 适用范围

- 时间周期（timeframe）
- 杠杆倍数
- 网格档位数量
- 价格阈值
- 交易对符号
- 合约大小 (contract_size)
- API 端点
- 超时时间
- 任何可能变化的业务参数

### 3.2. 精度与数量计算 (Precision) ⚠️ Critical

**严禁**手动进行 `round()` 或随意的精度计算。

- 必须通过交易所 API 获取 `contractSize`、精度等参数
- 持仓数量计算: `size (币) = contracts (张) * contractSize`
- 持仓价值计算: `value (USDT) = size (币) * price`

### 3.3. Gate.io 特殊规则 (Gate-Specifics)

| 规则 | 说明 |
|------|------|
| **下单单位** | `size` 字段指"张数"，真实币量需用 `contractSize` 换算 |
| **市价单 TIF** | 必须设为 `IOC`，否则报错 `AUTO_INVALID_PARAM_INITIAL_TIF` |
| **爆仓价为 0** | 全仓低风险状态下 API 返回 `liquidationPrice` 可能为 0 |
| **杠杆锁定** | 持仓时禁止修改杠杆或保证金模式 |
| **Reduce-Only 额度** | Limit 止盈单和 Trigger 止损单共享"可减仓额度" |

#### Reduce-Only 与止盈止损并存

- **额度互斥**: Gate.io 的 `reduce_only` 机制严格。Limit 止盈单和 Trigger 止损单共享同一个"可减仓额度"
- **典型报错**: `REDUCE_ONLY_FAIL` 或 `position size X and pending order -X while reduce order -X`
- **操作原则**: **提交紧急止损前，必须先撤销同方向的 Limit 挂单**

#### 计划委托 (Trigger/Plan Orders)

- **API 区别**: 创建条件单必须使用 `POST /futures/{settle}/price_orders`
- **参数陷阱**:
  - `expiration`: 持续时长（秒），非时间戳。正确示例: `2592000` (30 天)
  - `rule`: 触发方向，`1` (>=), `2` (<=)
  - `size`: 必须带符号；正=买，负=卖
  - `price`: 触发后的委托价；市价止损设为 `0` 且 `tif=ioc`

### 3.4. 状态管理与重启恢复 (State Management)

#### 持久化机制

- 状态文件: `state/key_level_grid/{symbol}_state.json`
- 按 `symbol` 存储，修改配置后旧状态会被恢复
- **如需强制使用新配置**:
  1. 删除状态文件: `rm -f state/key_level_grid/{symbol}_state.json`
  2. 或通过 Telegram "更新网格" 按钮

#### 重要原则

- **禁止盲目全撤**: 启动时不要直接 `cancel_all_orders`，避免止盈止损丢失
- **禁止意外平仓**: 平仓前确认 `position.size > 0`
- **状态接管**: 启动时同步交易所真实挂单，增量更新
- **热身保护**: 引入 `tick_count` 等预热机制，避免启动瞬间数据缺失导致误判

### 3.5. 防御性编程 (Defensive Programming)

```python
# 数据就绪检查
if not klines or len(klines) < 50:
    self.logger.warning("K线数据不足")
    return

# 价格有效性检查
if current_price <= 0:
    self.logger.warning("当前价格无效")
    return

# 字段安全访问
timeframe = getattr(level, 'timeframe', 'unknown')
strength = level.get('strength', 0) if isinstance(level, dict) else getattr(level, 'strength', 0)

# 捕获单笔订单失败，不让策略崩溃
try:
    await self._submit_order(order)
except Exception as e:
    self.logger.error(f"订单提交失败: {e}")
    # 继续处理下一个订单
```

### 3.6. 异步 IO 规范 (Async IO Rules)

- **禁止阻塞 Loop**: `async` 函数中禁止直接调用同步 I/O
- **正确做法**: 使用 `loop.run_in_executor` 包裹同步调用

```python
# 错误：直接调用同步方法
result = self._exchange.fetch_balance()

# 正确：使用 run_in_executor
loop = asyncio.get_event_loop()
result = await loop.run_in_executor(None, self._exchange.fetch_balance)
```

### 3.7. 错误处理 (Error Handling)

- 所有外部 API 调用必须有 try-except
- 捕获具体异常，避免 bare except
- 记录详细错误信息，便于排查

```python
try:
    result = await self.api.submit_order(order)
except ccxt.InsufficientFunds as e:
    self.logger.error(f"❌ 余额不足: {e}")
except ccxt.NetworkError as e:
    self.logger.error(f"❌ 网络错误: {e}")
except Exception as e:
    self.logger.error(f"❌ 未知错误: {e}", exc_info=True)
```

### 3.8. 状态变量初始化

新增状态变量必须在 `__init__` 初始化，避免 `AttributeError`。

```python
class Strategy:
    def __init__(self):
        # 必须初始化所有状态变量
        self._grid_created = False
        self._tp_orders_submitted = False
        self._last_rebuild_ts = 0
        self._tg_bot = None
```

---

## 4. 配置规范 (Configuration)

### 4.1. 配置优先级

从高到低：

1. 命令行参数
2. 环境变量 (`.env`)
3. 配置文件 (`config.yaml`)
4. 代码中的默认值

### 4.2. 配置一致性

当多个配置项表示同一含义时，必须确保一致性：

```python
# 杠杆配置：trading.leverage 和 position.max_leverage 必须一致
trading_leverage = trading.get('leverage', 3)
position_leverage = pos_raw.get('max_leverage', trading_leverage)

if position_leverage != trading_leverage:
    logging.warning(
        f"position.max_leverage({position_leverage}) 与 trading.leverage({trading_leverage}) 不一致，"
        f"使用 trading.leverage={trading_leverage}"
    )
    position_leverage = trading_leverage
```

---

## 5. 代码风格 (Code Style)

### 5.1. 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 类名 | PascalCase | `KeyLevelGridStrategy` |
| 函数/变量 | snake_case | `calculate_resistance_levels` |
| 常量 | UPPER_SNAKE_CASE | `MAX_RETRY_COUNT` |
| 私有方法 | _leading_underscore | `_update_cycle` |

### 5.2. 日志规范

使用 emoji 前缀便于识别：

```python
self.logger.info("✅ 网格创建成功")
self.logger.info("🔄 正在更新网格...")
self.logger.info("📊 数据统计...")
self.logger.warning("⚠️ 配置不完整")
self.logger.error("❌ 订单提交失败")
```

---

## 6. 新增功能检查清单 (Checklist)

添加新功能前，确认以下事项：

- [ ] 所有可配置值是否从配置获取？（禁止硬编码）
- [ ] 是否添加了必要的日志？
- [ ] 是否有完善的错误处理？
- [ ] 状态变量是否在 `__init__` 初始化？
- [ ] 是否需要更新状态持久化？
- [ ] 是否需要添加 Telegram 通知？
- [ ] 是否需要更新配置文件示例？
- [ ] 异步代码是否避免了阻塞调用？

---

## 7. 提交规范 (Commit Convention)

### Commit Message 格式

```
<type>: <简短描述>

<详细说明（可选）>
```

### Type 类型

| Type | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构（不改变功能） |
| `docs` | 文档更新 |
| `style` | 代码格式调整 |
| `test` | 测试相关 |
| `chore` | 构建/工具相关 |

---

## 8. 常见问题 (FAQ)

### Q: 修改配置后不生效？

**A**: 系统使用持久化状态，会恢复旧配置的计算结果。

解决方案：
1. 删除状态文件: `rm -f state/key_level_grid/{symbol}_state.json`
2. 或通过 Telegram "更新网格" 按钮

### Q: 关键价位周期显示错误？

**A**: 检查是否硬编码了周期值。所有周期应从 `self.config.kline_config.primary_timeframe.value` 获取。

### Q: Telegram Bot 菜单无响应？

**A**: 
1. 检查 Bot 是否正常运行: 查看日志中的 polling 状态
2. 重启策略
3. 检查网络连接
