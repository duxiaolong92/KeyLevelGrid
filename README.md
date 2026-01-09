# Key Level Grid Strategy

基于支撑/阻力位的网格交易策略。

## 功能特点

- 🎯 **智能价位识别**：多维度支撑/阻力位计算（摆动高低点、成交量密集区、斐波那契、心理关口）
- 📊 **网格交易**：自动在关键支撑位布置买单，在阻力位布置止盈单
- 🔄 **自动重建**：价格大幅偏离时自动重建网格
- 💰 **BTC 等量分配**：每个网格分配相同数量的 BTC
- 🛡️ **风险控制**：网格底线止损保护

## 安装

```bash
# 克隆项目
git clone https://github.com/yourusername/key-level-grid.git
cd key-level-grid

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 或使用 pip install -e . 安装为包
pip install -e .
```

## 配置

1. 复制并编辑配置文件：

```bash
cp configs/config.yaml configs/my_config.yaml
```

2. 创建 `.env` 文件配置 API 密钥：

```bash
# .env
GATE_KLG_API_KEY=your_api_key
GATE_KLG_API_SECRET=your_api_secret
```

3. 编辑配置文件 `configs/my_config.yaml`：

```yaml
# 运行模式
dry_run: true  # true=模拟, false=实盘

# 交易配置
trading:
  symbol: "BTCUSDT"
  exchange: "gate"
  leverage: 10

# 仓位配置
position:
  total_capital: 5000
  max_leverage: 10
  max_capital_usage: 0.8

# 网格配置
grid:
  max_grids: 20
  floor_buffer: 0.005
  rebuild_enabled: true
  rebuild_threshold_pct: 0.02
  rebuild_cooldown_sec: 900
```

## 运行

```bash
# 使用默认配置
python scripts/run.py

# 指定配置文件
python scripts/run.py --config configs/my_config.yaml

# 或使用 CLI（需要先 pip install -e .）
klg-run --config configs/my_config.yaml
```

## 项目结构

```
key-level-grid/
├── configs/
│   └── config.yaml          # 配置文件
├── src/
│   └── key_level_grid/
│       ├── models.py        # 数据模型
│       ├── kline_feed.py    # K线数据源
│       ├── indicator.py     # 指标计算
│       ├── resistance.py    # 支撑/阻力计算
│       ├── position.py      # 仓位管理
│       ├── signal.py        # 信号生成
│       ├── strategy.py      # 策略主逻辑
│       ├── executor/        # 订单执行
│       └── utils/           # 工具模块
├── scripts/
│   └── run.py               # 启动脚本
├── state/                   # 状态持久化
├── logs/                    # 日志文件
└── tests/                   # 测试
```

## 策略逻辑

### 1. 支撑/阻力位计算

- **摆动高低点 (SW)**：三尺度 (5/13/34) 识别价格转折点
- **成交量密集区 (VOL)**：Volume Profile 识别交易活跃区域
- **斐波那契 (FIB)**：0.382, 0.5, 0.618, 1.0, 1.618 回撤/扩展位
- **心理关口 (PSY)**：整数关口（如 90000, 85000）

### 2. 网格下单

- 在强支撑位（评分 ≥ 80）布置买单
- 每个网格分配等量 BTC
- 最大仓位 = 总资金 × 杠杆 × 使用率

### 3. 止盈止损

- **止盈**：在阻力位布置 reduce-only 卖单
- **止损**：网格底线（最低支撑位下方 0.5%）触发全仓止损

### 4. 网格重建

- 当价格偏离锚点超过 2% 时自动重建
- 重建时跳过均价保护过滤，允许在更高价位挂单

