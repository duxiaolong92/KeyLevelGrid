# Key Level Grid - 技术方案

## 技术栈

| 组件 | 选型 | 版本 | 说明 |
|------|------|------|------|
| 语言 | Python | 3.12+ | 异步支持好 |
| 数据源 | Binance WebSocket | - | K线数据稳定 |
| 执行交易 | Gate.io (ccxt) | 4.x | 支持计划委托 |
| 通知 | python-telegram-bot | 20.x | Telegram Bot |
| 配置 | YAML + .env | - | 敏感信息分离 |
| 日志 | logging + Rich | - | 终端 + 文件 |
| 终端UI | Rich | 13.x | 实时面板 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Key Level Grid Strategy                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  Kline Feed  │───▶│  Resistance  │───▶│   Position   │  │
│  │  (Binance)   │    │  Calculator  │    │   Manager    │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                   │                   │           │
│         ▼                   ▼                   ▼           │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  Indicator   │    │   Grid       │    │   Executor   │  │
│  │  Calculator  │    │   Builder    │    │   (Gate.io)  │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Telegram Bot (通知 & 交互)               │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## 模块依赖

```
kline_feed.py (Binance WebSocket)
      │
      ▼
resistance.py (支撑/阻力计算)
      │
      ▼
strategy.py (核心逻辑)
      │
      ├──▶ gate_executor.py (订单执行)
      │
      └──▶ telegram/notify.py (通知推送)
```

---

## 架构决策记录 (ADR)

### ADR-001: 数据源与执行分离

**状态**: 已采纳

**背景**: 需要选择获取行情数据和执行交易的交易所。

**决策**: Binance 获取 K 线数据，Gate.io 执行交易。

**理由**:
- Binance WebSocket 数据更稳定，延迟更低
- Gate.io API 支持 Trigger Order（计划委托），功能更灵活
- 分离后可独立替换任一组件

**后果**:
- 需要处理两个交易所的连接
- 价格可能有微小差异（可接受）

---

### ADR-002: 使用 Trigger Order 实现止损

**状态**: 已采纳 (2026-01-10)

**背景**: 需要实现止损功能，有两种方案：
1. 本地监控价格，触发后提交市价单
2. 交易所 Trigger Order（计划委托）

**决策**: 采用方案 2 - 交易所 Trigger Order

**理由**:
- 策略崩溃/断网时止损仍有效（关键！）
- 减少延迟（交易所直接执行）
- 减少本地监控负担

**后果**:
- 需要正确处理 Gate.io Trigger Order API
- 止损单占用保证金（reduce_only 部分解决）

---

### ADR-003: 状态持久化到 JSON

**状态**: 已采纳

**背景**: 需要保存策略状态以支持重启恢复。

**决策**: 使用本地 JSON 文件存储状态。

**理由**:
- 简单，无外部依赖
- 易于调试和手动编辑
- 足够满足单实例需求

**后果**:
- 不支持多实例共享状态
- 需要定期保存（已实现）

**状态文件路径**: `state/key_level_grid/{exchange}/{symbol}_state.json`

---

### ADR-004: 日志双输出

**状态**: 已采纳 (2026-01-10)

**背景**: Rich Live UI 会覆盖终端日志输出。

**决策**: 日志同时输出到终端和文件。

**理由**:
- 终端：实时查看（被 Rich 覆盖时可忽略）
- 文件：持久保存，便于问题排查

**日志文件路径**: `logs/key_level_grid.log`

---

## 关键配置

### 配置文件结构

```yaml
# configs/config.yaml
trading:
  symbol: "BTCUSDT"
  timeframe: "4h"           # 主周期
  aux_timeframes: ["1d"]    # 辅助周期
  leverage: 10

grid:
  max_grids: 8
  grid_capital_pct: 0.1     # 每格资金比例
  
position:
  max_position_usd: 8000    # 最大持仓 USDT
  max_leverage: 10

telegram:
  enabled: true
  # bot_token 和 chat_id 通过 .env 配置
```

### 环境变量

```bash
# .env
GATE_API_KEY=xxx
GATE_API_SECRET=xxx
TG_BOT_TOKEN=xxx
TG_CHAT_ID=xxx
```

---

## 运行方式

```bash
# 启动策略
cd KeyLevelGrid
PYTHONPATH=src python scripts/run.py --config configs/config.yaml

# 查看日志
tail -f logs/key_level_grid.log
```

---

*文档版本: v1.0*  
*创建日期: 2026-01-10*
