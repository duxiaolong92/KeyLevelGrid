# Key Level Grid - 单一规格文档（All-in-One）

整合原有 `strategy_spec.md`、`telegram_notification.md`、`features/F001~F003`、`plan.md`、`ADR005` 等需求到一份文档，便于统一查阅。

---

## 1. 项目概述
- 目标：基于支撑/阻力位的智能网格策略，适配永续合约（Gate 优先）。
- 价值：价位识别 + 网格化下单 + 交易所级止损 + Telegram 可观测/可控 + 多实例扩展。

## 2. 用户故事（精简）
- US-001 关键价位交易：支撑挂买、阻力挂卖，震荡盈利。
- US-002 自动止损保护：持仓自动提交交易所止损，断网仍有效。
- US-003 实时通知：Telegram 提示启动、成交、异常、订单更新。
- US-004 远程控制：Telegram 查看持仓/挂单/关键价位，触发“更新网格”。
- US-005 多实例：可并行跑多交易所/多币种，互不干扰。
- US-006 跨市场价位分析：通过 CLI 或 Telegram 查询任意美股/加密货币的关键价位。🆕

## 3. 功能需求（FR）
### FR-001 关键价位识别
- **多周期 K 线融合**：支持 1~3 个周期灵活配置（禁止硬编码）。
  - 配置项：`trading.timeframe` + `trading.aux_timeframes`
  - 第一个为主周期，后续为辅助周期
  - 多周期共振强度提升 `mtf_boost`（默认 0.30）
- **价位来源**：摆动高低点（SW）、斐波那契（FIB）、心理关口（PSY）、成交量密集区（VOL）。
- **强度评分**：阈值 `min_strength`（默认 60），相近价位按 `merge_tolerance`（默认 0.5%）合并。
- **距离过滤**：`min_distance_pct`（0.5%）~ `max_distance_pct`（30%）。

**多周期融合算法**：
```
1. 分别计算各周期价位（SW/VOL/FIB/PSY）
2. 辅助周期强度 ×1.2
3. ±0.5% 内相同价位视为"多周期共振"，强度 ×1.3
4. 多来源叠加：每多一种来源，强度 +15%
5. 合并 → 过滤 → 按综合分排序
```

**配置示例**：
```yaml
trading:
  timeframe: "4h"
  aux_timeframes: ["1d", "1w"]

resistance:
  mtf_boost: 0.30                 # 多周期共振加成
  min_strength: 60
  merge_tolerance: 0.005
  min_distance_pct: 0.005
  max_distance_pct: 0.30
  volume_bucket_pct: 0.01
  volume_top_pct: 0.20
```

### FR-002 网格构建
- 区间：自动或手动（`manual_upper/lower`）。
- 网格数：按有效支撑/阻力（`by_levels`）或固定数量。
- 底线：最低支撑下方 `floor_buffer` 形成止损线。
- 重建：价格偏离阈值（默认 2%）+ 冷却（默认 900s）；成交驱动重建有独立冷却 `rebuild_cooldown_on_fill_sec`。

### FR-003 止损订单（交易所计划委托）
- 持仓存在即提交/更新止损计划单（reduce_only，IOC）。
- 更新防抖：30s；取消与本地状态分离，避免重复提交。
- 启动同步交易所现有止损单；触发后通知亏损（已完成）。

### FR-004 止盈订单
- 按阻力位等份拆分覆盖持仓；持仓变化触发补挂。
- 使用 reduce_only；成交后可触发成交驱动重建。

### FR-005 Telegram 通知
- 启动/停止、错误、成交、网格重建、挂单汇总、风险预警、每日汇总；统一 USDT 计价，支持 dry_run。

### FR-006 Telegram 菜单/指令
- `/start` 菜单：当前持仓、当前挂单、关键价位、更新网格。
- “更新网格”强制刷新 pending orders。
- `/position` 展示止损计划单（ID、触发价、数量）。

### FR-007 状态持久化
- JSON：`state/key_level_grid/{exchange}/{symbol}_state.json`。重启可恢复锚点、网格、持仓、订单状态。

### FR-008 多实例运行（F002）
- 配置：`configs/instances.yaml` 列出实例 `name/exchange/symbol/config_path/log_path/env_prefix`。
- 启动器：`scripts/run_instances.py` 为每实例创建子进程，日志隔离（`LOG_FILE_PATH` 环境变量）。
- 状态按 `exchange` 隔离；未实现的交易所需显式报错。

### FR-009 固定每格金额（F003，可按强度加权）
- 每格金额 = `(total_capital * max_leverage * max_capital_usage) / 网格数`。
- 分配模式：`equal` 或 `weighted`（按强度 Σstrength_i 比例）。
- 挂单数量由金额/价格转合约张数；余额不足跳过该档，不再二次分配。

### FR-010 美股 K 线数据源（Polygon）🆕
**背景**：扩展关键价位识别能力，支持美股市场分析。

**数据源**：
- 使用 [Polygon.io](https://polygon.io/) API 获取美股 K 线数据
- 支持的周期：`1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w`
- 需要 API Key（通过环境变量 `POLYGON_API_KEY` 配置）

**支持标的**：
- 美股个股：AAPL, TSLA, NVDA, GOOGL, MSFT 等
- 美股 ETF：SPY, QQQ, IWM 等
- 后续可扩展：指数、期权（视 Polygon 套餐）

**技术要点**：
- 新建 `polygon_kline_feed.py` 模块，复用 `KlineFeed` 接口
- K 线数据结构与币圈保持一致（OHLCV + timestamp）
- 美股交易时段外返回最近有效数据

**配置示例**：
```yaml
polygon:
  api_key_env: "POLYGON_API_KEY"
  rate_limit: 5  # 请求/秒（免费套餐限制）
```

### FR-011 CLI 关键价位计算工具 🆕
**背景**：提供独立的命令行工具，快速计算任意标的的支撑/阻力位，不依赖策略运行。

**命令格式**：
```bash
python scripts/calc_levels.py <symbol> <timeframes> [options]

# 示例
python scripts/calc_levels.py TSLA 4h 1d          # 美股 TSLA，4h + 1d 融合
python scripts/calc_levels.py BTCUSDT 4h 1d       # 币圈 BTC，4h + 1d 融合
python scripts/calc_levels.py AAPL 1d             # 美股 AAPL，仅日线
python scripts/calc_levels.py ETHUSDT 1h 4h 1d    # 币圈 ETH，多周期
```

**参数说明**：
| 参数 | 说明 | 示例 |
|------|------|------|
| `symbol` | 标的代码（自动识别币圈/美股） | `TSLA`, `BTCUSDT`, `AAPL` |
| `timeframes` | 一个或多个周期（空格分隔） | `4h 1d`, `1h 4h 1d` |
| `--min-strength` | 最低强度阈值（默认 60） | `--min-strength 70` |
| `--count` | 返回数量（默认 10） | `--count 5` |
| `--output` | 输出格式：`table`/`json` | `--output json` |

**输出示例**：
```
📍 TSLA 关键价位分析（4h + 1d）

当前价: $248.50

阻力位 (10):
├ R1:  $252.30 (+1.5%) [SW] 💪85
├ R2:  $258.00 (+3.8%) [FIB] 💪78
├ R3:  $265.00 (+6.6%) [PSY] 💪92
...

支撑位 (10):
├ S1:  $245.00 (-1.4%) [SW] 💪80
├ S2:  $240.00 (-3.4%) [VOL] 💪75
├ S3:  $235.00 (-5.4%) [FIB] 💪88
...
```

**标的识别规则**：
- 包含 `USDT`/`USD`/`BTC` → 币圈（使用 Gate 期货数据源）
- 纯字母 2~5 位 → 美股（使用 Polygon 数据源）
- 可通过 `--source gate|polygon` 强制指定

**技术要点**：
- 复用现有 `resistance.py` 中的价位计算逻辑
- 新建 `scripts/calc_levels.py` 独立脚本
- 支持多周期融合（与策略逻辑一致）

### FR-012 Telegram 关键价位查询扩展 🆕
**背景**：在 Telegram Bot 中支持查询任意标的的关键价位，不限于当前策略运行的币种。

**新增命令**：
```
/levels <symbol> [timeframes]

# 示例
/levels TSLA 4h 1d       # 查询 TSLA 的关键价位
/levels AAPL 1d          # 查询 AAPL 日线价位
/levels ETHUSDT 4h       # 查询 ETH 4h 价位
/levels                  # 无参数 = 当前策略标的（保持原有功能）
```

**交互流程**：
1. 用户发送 `/levels TSLA 4h 1d`
2. Bot 回复 "⏳ 正在计算 TSLA 关键价位..."
3. 后台拉取 Polygon K 线 → 计算支撑/阻力
4. 返回格式化结果（同 CLI 输出）

**限制**：
- 单次查询超时 30 秒

**技术要点**：
- 扩展 `bot.py` 的 `/levels` 命令，支持参数解析
- 异步调用 CLI 计算逻辑（避免阻塞 Bot）
- 结果缓存 5 分钟（相同查询直接返回）

## 4. 架构与模块
- 模块：`strategy`（主循环/重建）、`position`（网格生成）、`executor`（交易所适配，Gate）、`telegram.bot`、`state`、`utils`（logger/config）。
- 数据流：价位 → 网格 → 挂单（买/卖/止损/止盈）→ 事件（成交/异常）→ 通知 & 状态。
- 日志：默认 `logs/key_level_grid.log`，CLI 可 `--log-file`；多实例可独立路径。

## 5. 配置要点（映射 `configs/config.yaml`）
- 交易：`exchange=gate`，`symbol=BTCUSDT`，`leverage` 与 `position.max_leverage` 对齐。
- 网格：`range_mode`，`manual_upper/lower`，`rebuild_threshold_pct`，`rebuild_cooldown_sec`，`floor_buffer`，`rebuild_cooldown_on_fill_sec`。
- 仓位：`total_capital=800`，`max_capital_usage=0.8`，`allocation_mode=equal|weighted`。
- 止损：`mode=total`，`trigger=grid_floor` 或 `fixed_pct`。
- 止盈：`mode=by_resistance` 或 `fixed_pct`。
- 阻力/支撑：`min_strength=60`，`merge_tolerance=0.005`，`min_distance_pct`/`max_distance_pct`，`volume_bucket_pct`/`volume_top_pct`，`mtf_boost`。
- 日志：`logging.level/file/console`，可被 CLI 覆盖。

## 6. 非功能性需求
- 安全：密钥用环境变量；reduce_only；支持 dry_run。
- 可靠：交易所止损；异常捕获 + Telegram；Bot 自检重启。
- 可维护：禁止硬编码（见 `DEV_GUIDE.md`）；配置优先；分层清晰。
- 可观测：Rich 面板、日志文件、Telegram 通知。

## 7. 接口与命令
- 单实例：`python scripts/run.py [--force-rebuild] [--log-file PATH]`。
- 多实例：`python scripts/run_instances.py --config configs/instances.yaml`。
- 价位计算：`python scripts/calc_levels.py <symbol> <timeframes> [--min-strength N] [--count N]`。🆕
- Telegram：菜单按钮 + `/position` `/orders` `/levels [symbol timeframes]` `/update_grid` 等。

## 8. 验收要点（提炼）
- 价位过滤遵守 `min_strength`，相近价位可合并。
- 重建遵守偏移阈值与冷却；成交驱动重建有独立冷却。
- 持仓存在则有交易所止损计划单；更新防抖；触发后需通知（待补）。
- 止盈单覆盖持仓张数，reduce_only。
- Telegram 菜单长期可用；10 分钟无指令自动重启 polling。
- 多实例日志/state 隔离；未支持交易所需显式报错。
- 固定每格金额；余额不足跳过该档，不再重分配。

## 9. 未完事项 / 风险
- ~~T004 止损单状态同步（启动查询、避免重复、状态落地）~~  ✅ 已完成
- ~~T005 止损触发通知（含亏损金额/百分比）~~ ✅ 已完成
- T006 单元测试（止损提交/取消/状态持久化）。
- ~~T007 美股数据源 Polygon 集成（FR-010）~~ ✅ 已完成
- ~~T008 CLI 关键价位计算工具（FR-011）~~ ✅ 已完成
- ~~T009 Telegram 跨标的价位查询（FR-012）~~ ✅ 已完成
- ~~T010 多周期灵活配置（FR-001 增强）~~ ✅ 已完成
  - 移除硬编码的 "1d" 辅助周期
  - 支持 1~3 个周期灵活配置
  - 新增 `klines_by_timeframe` 参数，保持向后兼容
- 多实例全局风控未做。

## 10. 版本
- 文档版本：v1.5  
- 更新日期：2026-01-14  
- 维护人：KeyLevelGrid 团队
- 变更记录：
  - v1.5：同步 Gate 数据源、Telegram 时间戳、距离过滤与配置项
  - v1.4：FR-001 增强多周期灵活配置，移除硬编码
  - v1.3：新增 FR-010/011/012 美股数据源和跨市场价位查询
