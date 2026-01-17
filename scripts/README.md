# 📜 Key Level Grid 脚本目录

> **最后更新**: 2026-01-17

---

## 🗂️ 目录结构

```
scripts/
├── README.md                    # 📌 本文件
│
├── run/                         # 🚀 启动脚本
│   ├── single.py               # 单实例策略启动
│   └── multi.py                # 多实例启动器
│
├── tools/                       # 🔧 工具脚本
│   └── calc_levels.py          # 关键价位计算 CLI
│
├── maintenance/                 # 🛠️ 维护脚本
│   ├── rebuild_grid.py         # 强制重置网格
│   └── reset_counters.py       # 清空配额计数器
│
└── backtest/                    # 🧪 回测脚本
    └── run.py                  # 历史回放回测
```

---

## 🚀 启动脚本 (run/)

### single.py - 单实例策略启动

启动单个交易对的网格策略。

```bash
# 基础启动
python scripts/run/single.py -c configs/config.yaml

# 模拟运行（不实际交易）
python scripts/run/single.py -c configs/config.yaml --dry-run

# 指定日志文件
python scripts/run/single.py -c configs/config.yaml --log-file logs/btc.log
```

### multi.py - 多实例启动器

按 `instances.yaml` 配置同时启动多个策略进程。

```bash
# 默认配置启动
python scripts/run/multi.py

# 指定配置文件
python scripts/run/multi.py -c configs/instances.yaml
```

---

## 🔧 工具脚本 (tools/)

### calc_levels.py - 关键价位计算

支持加密货币和美股的支撑/阻力位计算 CLI 工具。

```bash
# 加密货币示例
python scripts/tools/calc_levels.py BTCUSDT 4h 1d
python scripts/tools/calc_levels.py ETHUSDT 1h 4h 1d

# 美股示例
python scripts/tools/calc_levels.py TSLA 4h 1d
python scripts/tools/calc_levels.py AAPL 1d --count 5

# JSON 格式输出
python scripts/tools/calc_levels.py NVDA 4h --output json
```

**自动检测规则**：
- 包含 `USDT/USD/BTC/ETH` 后缀 → 使用 Gate 期货数据
- 纯字母 1~5 位 → 使用 Polygon 美股数据

---

## 🛠️ 维护脚本 (maintenance/)

### rebuild_grid.py - 强制重置网格

命令行强制重置网格，支持保留或清空计数器。

```bash
# 基础重置（清空所有计数器）
python scripts/maintenance/rebuild_grid.py -c configs/config.yaml

# 保留 fill_counter
python scripts/maintenance/rebuild_grid.py -c configs/config.yaml --preserve-counters

# 保留 active_inventory
python scripts/maintenance/rebuild_grid.py -c configs/config.yaml --preserve-inventory
```

### reset_counters.py - 清空配额计数器

清空 `fill_counter` 与邻位映射。

```bash
# 基础清空
python scripts/maintenance/reset_counters.py -c configs/config.yaml

# 清空后重建邻位映射（推荐）
python scripts/maintenance/reset_counters.py -c configs/config.yaml --rebuild-mapping

# 完全重置（清空映射）
python scripts/maintenance/reset_counters.py -c configs/config.yaml --clear-mapping

# 指定清空原因
python scripts/maintenance/reset_counters.py -c configs/config.yaml --reason "manual_reset"
```

---

## 🧪 回测脚本 (backtest/)

### run.py - 历史回放回测

使用历史 K 线数据进行策略回测。

```bash
# 基础回测
python scripts/backtest/run.py -c configs/config.yaml

# 指定时间范围
python scripts/backtest/run.py -c configs/config.yaml \
    --start "2025-01-01" \
    --end "2025-12-31"

# 指定交易对
python scripts/backtest/run.py -c configs/config.yaml --symbol BTCUSDT
```

---

## 📋 旧文件映射表

| 原路径 | 新路径 |
|--------|--------|
| `scripts/run.py` | `scripts/run/single.py` |
| `scripts/run_instances.py` | `scripts/run/multi.py` |
| `scripts/calc_levels.py` | `scripts/tools/calc_levels.py` |
| `scripts/rebuild_grid.py` | `scripts/maintenance/rebuild_grid.py` |
| `scripts/reset_counters.py` | `scripts/maintenance/reset_counters.py` |
| `scripts/backtest.py` | `scripts/backtest/run.py` |

---

## ⚠️ 注意事项

1. **环境变量**：运行前确保已加载 `.env` 文件中的 API 密钥
2. **配置文件**：默认使用 `configs/config.yaml`，可通过 `-c` 参数指定
3. **日志输出**：默认输出到 `logs/` 目录
4. **实盘风险**：非 `--dry-run` 模式会进行真实交易，请谨慎操作
