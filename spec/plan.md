# Key Level Grid - 技术方案

## 技术栈

| 组件 | 选型 | 版本 | 说明 |
|------|------|------|------|
| 语言 | Python | 3.12+ | 异步支持好 |
| 数据源 | Gate WebSocket | - | Gate 期货 K线 |
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
│  │   (Gate)     │    │  Calculator  │    │   Manager    │  │
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
kline_feed.py (Gate WebSocket)
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

### ADR-001: 数据源与执行统一为 Gate

**状态**: 已采纳

**背景**: 需要统一行情数据与执行交易的交易所，减少跨所价差偏移。

**决策**: Gate.io 获取 K 线数据，Gate.io 执行交易。

**理由**:
- 行情与执行一致，避免跨所价格差异导致的误判
- Gate.io API 支持 Trigger Order（计划委托），功能更灵活
- 统一数据源，降低运维复杂度

**后果**:
- 行情依赖 Gate 稳定性，需要提升重试与容错

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
  exchange: "gate"
  timeframe: "4h"
  aux_timeframes: ["1d", "1w"]
  leverage: 10
  default_contract_size: 0.0001

kline_feed:
  history_bars: 500
  use_websocket: true
  request_timeout_sec: 10.0

grid:
  range_mode: "manual"
  manual_upper: 98000
  manual_lower: 80000
  sell_quota_ratio: 0.7
  min_profit_pct: 0.005
  buy_price_buffer_pct: 0.002
  sell_price_buffer_pct: 0.002
  base_amount_per_grid: 1.0
  base_position_locked: 0.0
  recon_interval_sec: 30
  order_action_timeout_sec: 10

position:
  # total_capital: 自动从交易所读取
  max_leverage: 10
  max_capital_usage: 0.8

resistance:
  min_strength: 60
  merge_tolerance: 0.005
  min_distance_pct: 0.0001
  max_distance_pct: 0.30
  volume_bucket_pct: 0.01
  volume_top_pct: 0.20
  mtf_boost: 0.30

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
