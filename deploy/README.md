# 服务器部署指南

## 快速部署

### 1. 配置 SSH 别名（本地）

编辑 `~/.ssh/config`，添加服务器配置：

```
Host klg
    HostName your_server_ip
    User root
    IdentityFile ~/.ssh/your_key
```

### 2. 服务器初始化（只需一次）

```bash
# SSH 到服务器
ssh klg

# 创建项目目录
mkdir -p /opt/key-level-grid

# 退出服务器
exit
```

### 3. 部署代码（本地执行）

```bash
# 赋予执行权限
chmod +x deploy/deploy.sh

# 部署到服务器
./deploy/deploy.sh
```

### 4. 配置 API 密钥（服务器）

```bash
ssh klg

# 编辑 .env 文件
nano /opt/key-level-grid/.env

# 填入以下内容：
# GATE_KLG_API_KEY=你的API_KEY
# GATE_KLG_API_SECRET=你的API_SECRET
# TG_BOT_TOKEN=你的TG机器人Token
# TG_CHAT_ID=你的TG聊天ID
```

### 5. 安装 systemd 服务（服务器）

```bash
# 复制服务文件
sudo cp /opt/key-level-grid/deploy/klg.service /etc/systemd/system/

# 重新加载
sudo systemctl daemon-reload

# 设置开机自启
sudo systemctl enable klg

# 启动服务
sudo systemctl start klg
```

## 常用命令

```bash
# 查看服务状态
sudo systemctl status klg

# 查看实时日志
sudo journalctl -u klg -f

# 重启服务
sudo systemctl restart klg

# 停止服务
sudo systemctl stop klg

# 查看最近 100 行日志
sudo journalctl -u klg -n 100
```

## 更新部署

修改代码后，只需执行：

```bash
./deploy/deploy.sh
```

脚本会自动：
1. 同步代码到服务器
2. 安装新依赖（如有）
3. 重启服务

## 文件说明

```
deploy/
├── deploy.sh        # 部署脚本（本地执行）
├── klg.service      # systemd 服务配置
├── setup-server.sh  # 服务器初始化脚本
├── env.example      # 环境变量模板
└── README.md        # 本文件
```

## 注意事项

1. **API 密钥安全**：`.env` 文件不会被同步，需要在服务器上手动配置
2. **状态持久化**：`state/` 目录不会被同步，网格状态保存在服务器
3. **日志管理**：使用 `journalctl` 查看日志，systemd 自动管理日志轮转
