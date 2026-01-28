#!/bin/bash
# Key Level Grid 部署脚本
# 
# 用法:
#   ./deploy/deploy.sh [server]              # 部署默认实例 (config.yaml)
#   ./deploy/deploy.sh [server] sol          # 部署 SOL 实例
#   ./deploy/deploy.sh [server] sol eth      # 部署多个实例

set -e

# ============================================
# 配置
# ============================================
DEFAULT_SERVER="klg"
REMOTE_DIR="/opt/key-level-grid"

# ============================================
# 解析参数
# ============================================
SERVER=${1:-$DEFAULT_SERVER}
shift || true

# 如果没有指定币种，使用默认配置
if [ $# -eq 0 ]; then
    SYMBOLS="default"
else
    SYMBOLS="$@"
fi

# ============================================
# 辅助函数
# ============================================
get_config_path() {
    local sym="$1"
    if [ "$sym" = "default" ]; then
        echo "configs/config.yaml"
    else
        echo "configs/config_${sym}.yaml"
    fi
}

get_service_name() {
    local sym="$1"
    if [ "$sym" = "default" ]; then
        echo "klg"
    else
        echo "klg-${sym}"
    fi
}

get_log_path() {
    local sym="$1"
    if [ "$sym" = "default" ]; then
        echo "logs/klg.log"
    else
        echo "logs/klg_${sym}.log"
    fi
}

echo "🚀 部署到服务器: $SERVER"
echo "📁 目标目录: $REMOTE_DIR"
echo "📦 实例列表: $SYMBOLS"
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
# 2. 安装依赖
# ============================================
echo ""
echo "📥 安装依赖..."

ssh ${SERVER} "cd ${REMOTE_DIR} && \
    ([ -d venv ] || python3 -m venv venv) && \
    source venv/bin/activate && \
    pip install --upgrade pip -q && \
    pip install -r requirements.txt -q && \
    mkdir -p state/key_level_grid logs && \
    echo '✅ 依赖安装完成'"

# ============================================
# 3. 为每个实例配置并启动服务
# ============================================
for SYM in $SYMBOLS; do
    SYM_LOWER=$(echo "$SYM" | tr '[:upper:]' '[:lower:]')
    
    SERVICE_NAME=$(get_service_name "$SYM_LOWER")
    CONFIG_PATH=$(get_config_path "$SYM_LOWER")
    LOG_PATH=$(get_log_path "$SYM_LOWER")
    
    echo ""
    echo "🔧 配置实例: $SERVICE_NAME (${CONFIG_PATH})"
    
    EXEC_CMD="${REMOTE_DIR}/venv/bin/python ${REMOTE_DIR}/scripts/run/single.py --config ${REMOTE_DIR}/${CONFIG_PATH} --log-file ${REMOTE_DIR}/${LOG_PATH}"
    
    # 检查配置文件
    ssh ${SERVER} "test -f ${REMOTE_DIR}/${CONFIG_PATH}" || {
        echo "❌ 配置文件不存在: ${CONFIG_PATH}"
        echo "   请先创建配置文件后再部署"
        continue
    }
    
    # 创建/更新服务文件
    SERVICE_CONTENT="[Unit]
Description=Key Level Grid - ${SERVICE_NAME}
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${REMOTE_DIR}
Environment=PATH=${REMOTE_DIR}/venv/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=${REMOTE_DIR}/.env
ExecStart=${EXEC_CMD}
Restart=always
RestartSec=5
StandardOutput=append:${REMOTE_DIR}/logs/${SERVICE_NAME}_stdout.log
StandardError=append:${REMOTE_DIR}/logs/${SERVICE_NAME}_stderr.log
MemoryMax=512M
CPUQuota=30%

[Install]
WantedBy=multi-user.target"

    echo "$SERVICE_CONTENT" | ssh ${SERVER} "sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null"
    
    # 启动服务
    ssh ${SERVER} "sudo systemctl daemon-reload && \
        sudo systemctl enable ${SERVICE_NAME} 2>/dev/null || true && \
        sudo systemctl restart ${SERVICE_NAME} && \
        echo '📊 服务状态:' && \
        sudo systemctl status ${SERVICE_NAME} --no-pager -l" || echo "⚠️ 启动失败"
done

# ============================================
# 完成
# ============================================
echo ""
echo "============================================"
echo "✅ 部署完成！"
echo "============================================"
echo ""
echo "📝 常用命令:"
for SYM in $SYMBOLS; do
    SYM_LOWER=$(echo "$SYM" | tr '[:upper:]' '[:lower:]')
    SERVICE_NAME=$(get_service_name "$SYM_LOWER")
    LOG_PATH=$(get_log_path "$SYM_LOWER")
    
    echo ""
    echo "[$SERVICE_NAME]"
    echo "   状态: ssh ${SERVER} 'sudo systemctl status ${SERVICE_NAME}'"
    echo "   日志: ssh ${SERVER} 'tail -f ${REMOTE_DIR}/${LOG_PATH}'"
    echo "   重启: ssh ${SERVER} 'sudo systemctl restart ${SERVICE_NAME}'"
done
