# Key Level Grid 服务器部署指南

## 服务器信息

| 项目 | 值 |
|------|-----|
| IP | 43.167.237.240 |
| 系统 | OpenCloudOS (腾讯云) |
| 用户 | root |
| 项目目录 | /opt/key-level-grid |
| 服务名 | klg |

---

## 🚀 常用命令速查

### 服务管理（在服务器上执行）

```bash
# 查看状态
sudo systemctl status klg

# 重启服务
sudo systemctl restart klg

# 停止服务
sudo systemctl stop klg

# 启动服务
sudo systemctl start klg

# 查看实时日志
sudo journalctl -u klg -f

# 查看最近 100 行日志
sudo journalctl -u klg -n 100

# 查看今天的日志
sudo journalctl -u klg --since today
```

### 快速连接服务器

```bash
# 从本地连接
ssh klg

# 连接后直接查看日志
ssh klg 'journalctl -u klg -f'
```

### 更新部署（本地执行）

```bash
cd /Users/duxiaolong/Desktop/CurWorkSpace/KeyLevelGrid

# 部署默认配置
./deploy/deploy.sh klg

# 部署到指定服务器
./deploy/deploy.sh dxl

# 部署多个币种
./deploy/deploy.sh dxl sol eth
```

---

## 📋 首次部署步骤

### 1. 配置 SSH（本地，只需一次）

```bash
# 创建 SSH 配置
cat >> ~/.ssh/config << 'EOF'
Host klg
    HostName 43.167.237.240
    User root
    IdentityFile /Users/duxiaolong/Desktop/mac_m5.pem
EOF

# 设置权限
chmod 600 ~/.ssh/config
chmod 600 /Users/duxiaolong/Desktop/mac_m5.pem

# 测试连接
ssh klg
```

### 2. 部署代码（本地执行）

```bash
cd /Users/duxiaolong/Desktop/CurWorkSpace/KeyLevelGrid
./deploy/deploy.sh
```

### 3. 配置 API 密钥（服务器）

```bash
ssh klg
nano /opt/key-level-grid/.env
```

填入：
```
GATE_KLG_API_KEY=你的Gate API Key
GATE_KLG_API_SECRET=你的Gate API Secret
TG_BOT_TOKEN=你的Telegram Bot Token
TG_CHAT_ID=你的Telegram Chat ID
```

### 4. 安装 systemd 服务（服务器，只需一次）

```bash
sudo cp /opt/key-level-grid/deploy/klg.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable klg
sudo systemctl start klg
```

---

## 🔄 日常更新流程

1. **本地修改代码**
2. **提交到 Git**（可选）
   ```bash
   git add -A && git commit -m "更新说明"
   ```
3. **部署到服务器**
   ```bash
   ./deploy/deploy.sh
   ```
   > 脚本会自动同步代码、安装依赖、重启服务

---

## 🔀 多实例部署

支持同时运行多个交易对（如 BTC、SOL、ETH），每个实例使用独立的账户和 Telegram Bot。

### 部署命令

```bash
# 部署默认配置 (config.yaml → klg 服务)
./deploy/deploy.sh dxl

# 部署指定币种 (config_sol.yaml → klg-sol 服务)
./deploy/deploy.sh dxl sol

# 部署多个币种
./deploy/deploy.sh dxl sol eth xag
```

### 实例映射规则

| 参数 | 配置文件 | 服务名 |
|------|----------|--------|
| (空) | `configs/config.yaml` | `klg` |
| sol | `configs/config_sol.yaml` | `klg-sol` |
| eth | `configs/config_eth.yaml` | `klg-eth` |
| xag | `configs/config_xag.yaml` | `klg-xag` |

> 币种名称自动转小写，如 `SOL` → `sol`

### 多实例环境变量配置

在服务器 `.env` 文件中添加每个实例的 API 和 TG 配置：

```bash
# 主实例
GATE_KLG_API_KEY=xxx
GATE_KLG_API_SECRET=xxx
TG_BOT_TOKEN=xxx
TG_CHAT_ID=xxx

# SOL 实例
GATE_SOL_API_KEY=xxx
GATE_SOL_API_SECRET=xxx
TG_SOL_BOT_TOKEN=xxx
TG_SOL_CHAT_ID=xxx

# ETH 实例
GATE_ETH_API_KEY=xxx
GATE_ETH_API_SECRET=xxx
TG_ETH_BOT_TOKEN=xxx
TG_ETH_CHAT_ID=xxx
```

### 多实例管理命令

```bash
# 查看所有实例状态
ssh dxl 'sudo systemctl status klg klg-sol klg-eth'

# 重启所有实例
ssh dxl 'sudo systemctl restart klg klg-sol klg-eth'

# 查看某个实例日志
ssh dxl 'sudo journalctl -u klg-sol -f'
```

---

## 📁 文件说明

```
deploy/
├── deploy.sh        # 部署脚本（支持单/多实例）
├── klg.service      # 主实例 systemd 服务模板
├── klg-sol.service  # SOL 实例 systemd 服务模板
├── klg-eth.service  # ETH 实例 systemd 服务模板
├── setup-server.sh  # 服务器初始化脚本
├── env.example      # 环境变量模板
└── README.md        # 本文件
```

> 如果服务文件不存在，部署脚本会自动基于配置创建

---

## ⚠️ 注意事项

1. **API 密钥安全**：`.env` 文件不会被同步到服务器，需要手动配置
2. **状态持久化**：`state/` 目录保存在服务器，不会被覆盖
3. **日志管理**：systemd 自动管理日志轮转，无需手动清理
4. **配置修改**：修改 `configs/config.yaml` 后需要重新部署

---

## 🔧 故障排查

### 服务启动失败

```bash
# 查看详细错误
sudo journalctl -u klg -n 50 --no-pager

# 手动运行测试
cd /opt/key-level-grid
source venv/bin/activate
python scripts/run.py --config configs/config.yaml
```

### 检查 .env 是否正确

```bash
cat /opt/key-level-grid/.env
```

### 检查 Python 环境

```bash
cd /opt/key-level-grid
source venv/bin/activate
pip list
```
