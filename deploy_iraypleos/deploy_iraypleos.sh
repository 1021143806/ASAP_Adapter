#!/bin/bash
# ============================================================
# ASAP Adapter · 多平台离线部署
# 自动检测 OS + Python，按需走平台专属安装策略
# 目标系统: IRAYPLEOS / CentOS 7 / openEuler / Debian
# ============================================================
set -euo pipefail

# ---- 颜色（仅在终端可用时开启） ----
if [ -t 1 ]; then
    C_R='\033[31m' C_G='\033[32m' C_Y='\033[33m'
    C_B='\033[34m' C_C='\033[36m' C_N='\033[0m' C_BOLD='\033[1m'
else
    C_R='' C_G='' C_Y='' C_B='' C_C='' C_N='' C_BOLD=''
fi
log_info()  { echo -e "${C_B}[INFO]${C_N}  $*"; }
log_ok()    { echo -e "${C_G}[ OK ]${C_N}  $*"; }
log_warn()  { echo -e "${C_Y}[WARN]${C_N}  $*"; }
log_error() { echo -e "${C_R}[ERROR]${C_N} $*"; }
die()       { log_error "$*"; exit 1; }
step()      { echo ""; echo -e "${C_BOLD}[$1]${C_N} $2"; }

# ---- 目录定义 ----
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$DEPLOY_DIR")"
VENDOR_DIR="$DEPLOY_DIR/vendor_packages3.9"
cd "$PROJECT_DIR"

[ -f "app.py" ] || die "未找到 app.py，请确认在项目根目录运行部署脚本"

SERVICE_NAME="asap_adapter"
APP_PORT=5012
SUPERVISOR_USER="${USER:-ymsk}"
SUPERVISOR_CONF="/main/server/supervisor/${SERVICE_NAME}.conf"
LOG_DIR="/main/app/${SERVICE_NAME}/logs"

# ---- 1. 操作系统检测 ----
detect_os() {
    step "1" "检测操作系统"

    if [ -f /etc/os-release ]; then
        OS_ID=$(grep -oP '^ID="?\K[^"]+' /etc/os-release | head -1)
        OS_VERSION=$(grep -oP '^VERSION_ID="?\K[^"]+' /etc/os-release | head -1 | cut -d. -f1)
        OS_PRETTY=$(grep -oP '^PRETTY_NAME="?\K[^"]+' /etc/os-release | head -1)
    elif [ -f /etc/redhat-release ]; then
        OS_ID="centos"
        OS_VERSION=$(grep -oP '[0-9]+' /etc/redhat-release | head -1)
        OS_PRETTY=$(cat /etc/redhat-release)
    elif [ -f /etc/debian_version ]; then
        OS_ID="debian"
        OS_VERSION=$(cat /etc/debian_version | cut -d. -f1)
        OS_PRETTY="Debian $OS_VERSION"
    else
        OS_ID="unknown"; OS_VERSION="0"; OS_PRETTY="Unknown"
    fi

    ARCH=$(uname -m)
    log_info "系统:    ${OS_PRETTY:-$OS_ID}"
    log_info "架构:    $ARCH"
    log_info "OS ID:   $OS_ID (v$OS_VERSION)"

    # 规范化
    OS_ID_LOWER=$(echo "$OS_ID" | tr '[:upper:]' '[:lower:]')
    case "$OS_ID_LOWER" in
        openeuler|iraypleos) OS_ID="openEuler" ;;
        centos|rhel|fedora)  OS_ID="centos" ;;
        ubuntu|debian)       OS_ID="debian" ;;
        *) die "不支持的操作系统: $OS_ID ($OS_PRETTY)" ;;
    esac
}

# ---- 2. Python 检测 ----
detect_python() {
    step "2" "检测 Python 环境"

    PYTHON3=""

    # 按优先级查找 python3
    for candidate in \
        "${PYTHON3_PATH:-}" \
        "$(command -v python3 2>/dev/null)" \
        "/opt/rh/rh-python39/root/bin/python3" \
        "/usr/local/python3/bin/python3" \
        "/usr/local/bin/python3" \
        "$(command -v python 2>/dev/null)" \
    ; do
        [ -z "$candidate" ] && continue
        [ ! -x "$candidate" ] && continue

        ver=$("$candidate" --version 2>&1)
        ver_num=$(echo "$ver" | grep -oP '[0-9]+\.[0-9]+' | head -1)
        major=${ver_num%%.*}; minor=${ver_num#*.}

        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON3="$candidate"
            break
        fi
    done

    if [ -z "$PYTHON3" ]; then
        USE_SYSTEM_PYTHON=false
        log_warn "未找到 Python 3.9+"
        return
    fi

    PYTHON_VERSION=$($PYTHON3 --version 2>&1 | grep -oP '[0-9]+\.[0-9]+\.[0-9]+')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
    PYTHON_ABI="cp$PYTHON_MAJOR$PYTHON_MINOR"
    USE_SYSTEM_PYTHON=true

    log_info "路径:    $PYTHON3"
    log_info "版本:    $PYTHON_VERSION"
    log_info "ABI:     $PYTHON_ABI"
    log_ok "系统 Python 满足要求 (>= 3.9)"
}

# ---- 3. 确保 Python 可用 ----
ensure_python() {
    step "3" "准备 Python 环境"

    if [ "${USE_SYSTEM_PYTHON:-false}" = "true" ]; then
        log_ok "使用系统 Python: $PYTHON3 ($($PYTHON3 --version 2>&1))"
        return 0
    fi

    log_info "系统 Python 不满足要求，尝试平台专属安装..."

    case "$OS_ID" in
        centos)
            if [ "$OS_VERSION" = "7" ]; then
                CENTOS7_SETUP="$DEPLOY_DIR/platform/centos7/setup.sh"
                [ -f "$CENTOS7_SETUP" ] || die "CentOS 7 安装脚本缺失: $CENTOS7_SETUP"
                source "$CENTOS7_SETUP"
            else
                die "CentOS $OS_VERSION: 暂不支持，请手动安装 Python 3.9+"
            fi
            ;;
        openEuler)
            die "openEuler: 请安装 python3: dnf install -y python3 python3-pip python3-venv"
            ;;
        debian)
            die "Debian/Ubuntu: 请安装 python3: apt install -y python3 python3-venv python3-pip"
            ;;
        *)
            die "未知系统，请手动安装 Python 3.9+"
            ;;
    esac

    # 重新检测
    detect_python
    if [ "${USE_SYSTEM_PYTHON:-false}" != "true" ]; then
        die "Python 环境准备失败"
    fi
}

# ---- 4. 创建虚拟环境 ----
create_venv() {
    step "4" "创建虚拟环境"

    VENV_PATH="$PROJECT_DIR/venv"

    if [ -d "$VENV_PATH" ]; then
        log_info "清理旧虚拟环境..."
        rm -rf "$VENV_PATH"
    fi

    # 检查 venv 模块
    if ! $PYTHON3 -m venv --help &>/dev/null; then
        die "Python 3.9 缺少 venv 模块，请确认编译时使用了 --with-ensurepip=install"
    fi

    $PYTHON3 -m venv "$VENV_PATH" || die "虚拟环境创建失败"
    [ -f "$VENV_PATH/bin/python" ] || die "venv/bin/python 不存在"
    log_ok "虚拟环境创建成功: $VENV_PATH"
    log_info "Python: $($VENV_PATH/bin/python --version 2>&1)"
}

# ---- 5. 安装离线依赖 ----
install_deps() {
    step "5" "安装离线依赖"

    source "$VENV_PATH/bin/activate" || die "虚拟环境激活失败"
    VENV_PIP="$VENV_PATH/bin/pip"

    # 确保 pip 可用
    if [ ! -f "$VENV_PIP" ]; then
        $PYTHON3 -m ensurepip --upgrade 2>/dev/null || true
    fi

    # 查找 requirements 文件
    REQ_FILE="$VENDOR_DIR/requirements_asap_fixed.txt"
    [ ! -f "$REQ_FILE" ] && REQ_FILE="$VENDOR_DIR/requirements_py39_fixed.txt"

    if [ ! -f "$REQ_FILE" ]; then
        die "未找到 requirements 文件: $VENDOR_DIR/requirements_asap_fixed.txt"
    fi

    log_info "离线包目录: $VENDOR_DIR"
    log_info "依赖清单:   $(basename "$REQ_FILE")"
    PKG_COUNT=$(grep -cE '^[a-zA-Z]' "$REQ_FILE")
    log_info "包数量:     $PKG_COUNT"

    # 批量安装
    if "$VENV_PIP" install --no-index --find-links="$VENDOR_DIR" -r "$REQ_FILE" 2>/dev/null; then
        log_ok "依赖安装成功"
    else
        log_warn "批量安装失败，尝试逐个安装..."
        while IFS= read -r line; do
            [[ -z "$line" || "$line" =~ ^# ]] && continue
            pkg_name="${line%%=*}"
            pkg_name="${pkg_name%%>*}"
            pkg_name="$(echo "$pkg_name" | xargs)"
            whl=$(find "$VENDOR_DIR" -type f -iname "*${pkg_name}*.whl" 2>/dev/null | head -1)
            if [ -f "$whl" ]; then
                "$VENV_PIP" install --no-index --find-links="$VENDOR_DIR" "$whl" 2>/dev/null \
                    && log_ok "$pkg_name" || log_error "$pkg_name 安装失败"
            else
                log_warn "跳过: $pkg_name (未找到 wheel)"
            fi
        done < "$REQ_FILE"
    fi
}

# ---- 6. 验证安装 ----
verify_deps() {
    step "6" "验证安装"

    source "$VENV_PATH/bin/activate" 2>/dev/null || true
    for pkg in fastapi uvicorn httpx pydantic; do
        if python -c "import $pkg; print(f'   ✅ $pkg 导入成功')" 2>/dev/null; then
            :
        else
            log_error "$pkg 导入失败"
        fi
    done
}

# ---- 7. Supervisor 配置 ----
configure_supervisor() {
    step "7" "配置 Supervisor"

    VENV_PYTHON="$VENV_PATH/bin/python"

    if [ -f "$SUPERVISOR_CONF" ]; then
        log_ok "Supervisor 配置已存在: $SUPERVISOR_CONF"
        return
    fi

    log_info "创建 Supervisor 配置..."
    mkdir -p "$LOG_DIR" 2>/dev/null || true

    cat > "$SUPERVISOR_CONF" << EOF
[program:${SERVICE_NAME}]
command=$VENV_PYTHON $PROJECT_DIR/app.py
directory=$PROJECT_DIR
user=$SUPERVISOR_USER
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

    log_ok "Supervisor 配置已创建: $SUPERVISOR_CONF"
}

# ---- 8. 启动服务 ----
start_service() {
    step "8" "启动服务"

    if command -v supervisorctl &>/dev/null && [ -f "$SUPERVISOR_CONF" ]; then
        supervisorctl reread 2>/dev/null || true
        supervisorctl update 2>/dev/null || true
        supervisorctl restart "$SERVICE_NAME" 2>/dev/null || supervisorctl start "$SERVICE_NAME" 2>/dev/null || true

        sleep 3
        if supervisorctl status "$SERVICE_NAME" 2>/dev/null | grep -q "RUNNING"; then
            log_ok "服务已在 Supervisor 中运行"
            return
        fi
        log_warn "Supervisor 未运行，尝试直接启动..."
    fi

    # 直接启动
    nohup "$VENV_PATH/bin/python" "$PROJECT_DIR/app.py" \
        > "$LOG_DIR/${SERVICE_NAME}_direct.log" 2>&1 &
    sleep 2

    if pgrep -f "python.*app.py" >/dev/null; then
        log_ok "已通过直接启动方式运行"
    else
        die "服务启动失败，请查看日志: $LOG_DIR/${SERVICE_NAME}_direct.log"
    fi
}

# ── ── ── ── ── ── ── ── ── ── ── ── ──
#  主流程
# ── ── ── ── ── ── ── ── ── ── ── ── ──

echo ""
echo -e "${C_C}========================================${C_N}"
echo -e "${C_BOLD}  ASAP Adapter · 离线部署${C_N}"
echo -e "${C_C}========================================${C_N}"
echo "项目: $SERVICE_NAME"
echo "端口: $APP_PORT"
echo "用户: $(whoami)"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo -e "${C_C}----------------------------------------${C_N}"

detect_os
detect_python
ensure_python
create_venv
install_deps
verify_deps
configure_supervisor
start_service

echo ""
echo -e "${C_C}========================================${C_N}"
echo -e "${C_G}  ASAP Adapter 部署完成！${C_N}"
echo -e "${C_C}========================================${C_N}"
echo ""
echo "服务信息:"
echo "  操作系统:   ${OS_PRETTY:-$OS_ID} ${ARCH}"
echo "  Python:     $($PYTHON3 --version 2>&1)"
echo "  项目目录:   $PROJECT_DIR"
echo "  虚拟环境:   $VENV_PATH/"
echo "  端口:       $APP_PORT"
echo "  Supervisor: ${SUPERVISOR_CONF:-N/A}"
echo "  日志:       $LOG_DIR/"
echo ""
echo "验证命令:"
echo "  健康检查:   curl -s http://localhost:${APP_PORT}/actuator/health"
echo "  WebUI:      http://localhost:${APP_PORT}/"
echo "  状态:       supervisorctl status $SERVICE_NAME"
echo "  日志:       tail -f $LOG_DIR/${SERVICE_NAME}.log"
echo ""
echo -e "${C_C}========================================${C_N}"
