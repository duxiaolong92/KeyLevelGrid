#!/bin/bash
# Key Level Grid 部署脚本
# 用法: ./deploy/deploy.sh [server_alias]

set -e

# ============================================
# 配置（根据你的服务器修改）
# ============================================
DEFAULT_SERVER="klg"                    # 默认 SSH 别名（在 ~/.ssh/config 中配置）
REMOTE_DIR="/opt/key-level-grid"        # 服务器上的项目目录
SERVICE_NAME="klg"                      # systemd 服务名

# ============================================
# 解析参数
# ============================================
SERVER=${1:-$DEFAULT_SERVER}

echo "🚀 部署到服务器: $SERVER"
echo "📁 目标目录: $REMOTE_DIR"
echo ""

# ============================================
# 1. 同步代码
# ============================================
echo "📦 同步代码..."

rsync -avz --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude 'venv' \
    --exclude '.venv' \
    --exclude 'state/' \
    --exclude 'logs/' \
    --exclude '.DS_Store' \
    --exclude '*.log' \
    ./ ${SERVER}:${REMOTE_DIR}/

echo "✅ 代码同步完成"

# ============================================
# 2. 在服务器上执行安装
# ============================================
echo ""
echo "📥 安装依赖..."

ssh ${SERVER} << 'ENDSSH'
cd /opt/key-level-grid

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境并安装依赖
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 创建必要目录
mkdir -p state/key_level_grid
mkdir -p logs

echo "✅ 依赖安装完成"
ENDSSH

# ============================================
# 3. 重启服务
# ============================================
echo ""
echo "🔄 重启服务..."

ssh ${SERVER} << ENDSSH
sudo systemctl daemon-reload
sudo systemctl restart ${SERVICE_NAME} || echo "服务未配置，跳过重启"
sudo systemctl status ${SERVICE_NAME} --no-pager || true
ENDSSH

echo ""
echo "✅ 部署完成！"
echo ""
echo "📝 常用命令:"
echo "   查看状态: ssh ${SERVER} 'sudo systemctl status ${SERVICE_NAME}'"
echo "   查看日志: ssh ${SERVER} 'sudo journalctl -u ${SERVICE_NAME} -f'"
echo "   停止服务: ssh ${SERVER} 'sudo systemctl stop ${SERVICE_NAME}'"
echo "   启动服务: ssh ${SERVER} 'sudo systemctl start ${SERVICE_NAME}'"
