#!/bin/bash
# 服务器初始化脚本（在服务器上执行一次）
# 用法: bash setup-server.sh

set -e

PROJECT_DIR="/opt/key-level-grid"
SERVICE_NAME="klg"

echo "🔧 Key Level Grid 服务器初始化"
echo ""

# ============================================
# 1. 创建项目目录
# ============================================
echo "📁 创建项目目录..."
sudo mkdir -p ${PROJECT_DIR}
sudo chown -R $(whoami):$(whoami) ${PROJECT_DIR}

# ============================================
# 2. 安装系统依赖
# ============================================
echo "📦 安装系统依赖..."

if command -v apt-get &> /dev/null; then
    # Ubuntu/Debian
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv
elif command -v yum &> /dev/null; then
    # CentOS/RHEL
    sudo yum install -y python3 python3-pip
fi

# ============================================
# 3. 创建 .env 文件模板
# ============================================
echo "📝 创建 .env 模板..."

if [ ! -f "${PROJECT_DIR}/.env" ]; then
    cat > ${PROJECT_DIR}/.env << 'EOF'
# Gate.io API (必填)
GATE_KLG_API_KEY=your_api_key_here
GATE_KLG_API_SECRET=your_api_secret_here

# Telegram 通知 (可选)
TG_BOT_TOKEN=your_telegram_bot_token
TG_CHAT_ID=your_telegram_chat_id
EOF
    echo "⚠️  请编辑 ${PROJECT_DIR}/.env 填入你的 API 密钥！"
fi

# ============================================
# 4. 安装 systemd 服务
# ============================================
echo "🔧 配置 systemd 服务..."

if [ -f "${PROJECT_DIR}/deploy/klg.service" ]; then
    sudo cp ${PROJECT_DIR}/deploy/klg.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable ${SERVICE_NAME}
    echo "✅ systemd 服务已配置"
else
    echo "⚠️  服务文件不存在，请先部署代码"
fi

echo ""
echo "✅ 服务器初始化完成！"
echo ""
echo "📋 下一步操作:"
echo "   1. 编辑 API 密钥: nano ${PROJECT_DIR}/.env"
echo "   2. 从本地部署代码: ./deploy/deploy.sh"
echo "   3. 启动服务: sudo systemctl start ${SERVICE_NAME}"
