#!/bin/bash
# ASAP Adapter 离线部署脚本
# 目标系统: IRAYPLEOS (Python 3.9)

set -e

echo "========================================"
echo "ASAP Adapter - 风淋门区域管控协议适配器"
echo "========================================"
echo "系统: IRAYPLEOS"
echo "Python版本: $(python3 --version 2>&1)"
echo "用户: $(whoami)"
echo "时间: $(date)"
echo "========================================"

# 目录定义
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$DEPLOY_DIR")"
cd "$PROJECT_DIR"

if [ ! -f "app.py" ]; then
    echo "错误: 未找到 app.py，请确认脚本在项目根目录的上级运行"
    exit 1
fi

# 配置变量
SERVICE_NAME="asap_adapter"
IRAY_USER="ymsk"
SUPERVISOR_CONF="/main/server/supervisor/${SERVICE_NAME}.conf"
LOG_DIR="/main/app/${SERVICE_NAME}/logs"
VENDOR_DIR="$DEPLOY_DIR/vendor_packages3.9"

echo ""
echo "1. 检查环境..."
echo "   Python版本: $(python3 --version 2>&1)"
echo "   项目目录: $PROJECT_DIR"
echo "   部署目录: $DEPLOY_DIR"
echo "   当前用户: $(whoami)"
echo "   Supervisor用户: $IRAY_USER"

echo ""
echo "2. 检查 vendor 离线包目录..."
if [ ! -d "$VENDOR_DIR" ]; then
    echo "   ❌ vendor_packages3.9 目录不存在"
    exit 1
fi

echo "   快速检查关键包..."
KEY_PACKAGES=("fastapi" "uvicorn" "httpx" "pydantic" "typing_extensions" "anyio" "starlette" "h11" "certifi" "httpcore" "sniffio")
for pkg in "${KEY_PACKAGES[@]}"; do
    file=$(find "$VENDOR_DIR" -type f -iname "*${pkg}*.whl" 2>/dev/null | head -1)
    if [ -f "$file" ]; then
        echo "   ✅ $pkg: $(basename "$file")"
    else
        echo "   ⚠️  未找到: $pkg (可手动补全)"
    fi
done

echo ""
echo "3. 清理并创建虚拟环境..."
rm -rf venv 2>/dev/null || true
python3 -m venv venv

if [ ! -f "venv/bin/python" ]; then
    echo "   ❌ 虚拟环境创建失败"
    exit 1
fi
echo "   ✅ 虚拟环境创建成功"

echo ""
echo "4. 激活虚拟环境..."
source venv/bin/activate
echo "   虚拟环境Python: $(python --version 2>&1)"

echo ""
echo "5. 安装离线依赖包..."
REQ_FILE="$VENDOR_DIR/requirements_asap_fixed.txt"
if [ ! -f "$REQ_FILE" ]; then
    echo "   ⚠️  未找到 requirements_asap_fixed.txt"
    echo "   尝试使用 requirements_py39_fixed.txt（如存在）..."
    REQ_FILE="$VENDOR_DIR/requirements_py39_fixed.txt"
fi

if [ -f "$REQ_FILE" ]; then
    echo "   使用: $REQ_FILE"
    echo "   包数量: $(grep -cE '^[a-zA-Z]' "$REQ_FILE")"

    echo "   开始安装..."
    if pip install --no-index --find-links="$VENDOR_DIR" -r "$REQ_FILE" 2>/dev/null; then
        echo "   ✅ 批量依赖安装成功"
    else
        echo "   ⚠️  批量安装失败，尝试逐个安装..."
        while IFS= read -r line; do
            [[ -z "$line" || "$line" =~ ^# ]] && continue
            pkg_name="${line%%==*}"
            pkg_name="${pkg_name%%>*}"
            pkg_name="$(echo "$pkg_name" | xargs)"
            wheel_file=$(find "$VENDOR_DIR" -type f -iname "*${pkg_name}*.whl" 2>/dev/null | head -1)
            if [ -f "$wheel_file" ]; then
                if pip install --no-index --find-links="$VENDOR_DIR" "$wheel_file" 2>/dev/null; then
                    echo "   ✅ $pkg_name 安装成功"
                else
                    echo "   ❌ $pkg_name 安装失败"
                fi
            else
                echo "   ⚠️  未找到 ${pkg_name}.whl，跳过"
            fi
        done < "$REQ_FILE"
    fi
else
    echo "   ⚠️  未找到 requirements 文件，尝试在线安装..."
    pip install fastapi uvicorn[standard] httpx pydantic typing_extensions
fi

echo ""
echo "6. 验证安装..."
echo "   测试关键包导入..."
test_import() {
    if python -c "import $1; print('   ✅ $1 导入成功')" 2>/dev/null; then
        return 0
    else
        echo "   ❌ $1 导入失败"
        return 1
    fi
}

test_import fastapi
test_import uvicorn
test_import httpx
test_import pydantic

echo ""
echo "7. 配置 Supervisor..."
if [ -f "$SUPERVISOR_CONF" ]; then
    echo "   ✅ Supervisor 配置已存在: $SUPERVISOR_CONF"
    echo "   ℹ️  跳过创建，将直接重启"
else
    echo "   创建 Supervisor 配置..."
    mkdir -p "$LOG_DIR" 2>/dev/null || true

    cat > "$SUPERVISOR_CONF" << EOF
[program:${SERVICE_NAME}]
command=$PROJECT_DIR/venv/bin/python3 $PROJECT_DIR/app.py
directory=$PROJECT_DIR
user=$IRAY_USER
autostart=true
autorestart=true
startsecs=10
startretries=3
redirect_stderr=true
stdout_logfile=$LOG_DIR/${SERVICE_NAME}.log
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=0
stderr_logfile=$LOG_DIR/${SERVICE_NAME}_error.log
stderr_logfile_maxbytes=5MB
stderr_logfile_backups=0
environment=PYTHONPATH="$PROJECT_DIR"
EOF

    echo "   ✅ Supervisor 配置创建完成: $SUPERVISOR_CONF"
fi

echo ""
echo "8. 启动服务..."
if command -v supervisorctl >/dev/null 2>&1; then
    supervisorctl reread 2>/dev/null || echo "   ⚠️  重读配置失败"
    supervisorctl update 2>/dev/null || echo "   ⚠️  更新配置失败"

    echo "   重启服务..."
    supervisorctl restart "$SERVICE_NAME" 2>/dev/null || {
        echo "   ⚠️  重启失败，尝试直接启动..."
        supervisorctl start "$SERVICE_NAME" 2>/dev/null
    }

    sleep 3

    if supervisorctl status "$SERVICE_NAME" 2>/dev/null | grep -q "RUNNING"; then
        echo "   ✅ 服务已在 Supervisor 中运行"
    else
        echo "   ⚠️  服务未在 Supervisor 中运行，直接启动..."
        nohup "$PROJECT_DIR/venv/bin/python3" "$PROJECT_DIR/app.py" > "$LOG_DIR/${SERVICE_NAME}_direct.log" 2>&1 &
        sleep 2
        if pgrep -f "python.*app.py" >/dev/null; then
            echo "   ✅ 已直接启动"
        else
            echo "   ❌ 启动失败"
        fi
    fi
else
    echo "   ⚠️  supervisorctl 未找到，直接启动..."
    nohup "$PROJECT_DIR/venv/bin/python3" "$PROJECT_DIR/app.py" > "$LOG_DIR/${SERVICE_NAME}_direct.log" 2>&1 &
    sleep 2
    if pgrep -f "python.*app.py" >/dev/null; then
        echo "   ✅ 已直接启动"
    else
        echo "   ❌ 启动失败"
    fi
fi

echo ""
echo "========================================"
echo "ASAP Adapter 部署完成！"
echo "========================================"
echo ""
echo "服务信息:"
echo "- 服务名: $SERVICE_NAME"
echo "- 端口: 5012"
echo "- 虚拟环境: $PROJECT_DIR/venv/"
echo "- Supervisor 配置: $SUPERVISOR_CONF"
echo "- 日志目录: $LOG_DIR"
echo ""
echo "验证命令:"
echo "- 检查进程: pgrep -f 'python.*app.py'"
echo "- 检查端口: netstat -tlnp | grep 5012"
echo "- 健康检查: curl -s http://localhost:5012/actuator/health"
echo "- WebUI:     http://localhost:5012/"
echo ""
echo "========================================"
