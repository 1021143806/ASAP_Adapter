#!/bin/bash
# ============================================================
# ASAP Adapter · CentOS 7 专属 Python 安装
# 在系统 Python < 3.9 时被 deploy.sh 调用
# ============================================================
#
# 安装策略（按优先级）:
#   1. SCL rh-python39（最推荐，性能最好）
#   2. 预编译包 python39_build.tar.gz
#   3. 源码编译 Python-3.9.x.tar.xz
#
# 成功后设置 PYTHON3 和 USE_SYSTEM_PYTHON=true

# 进入脚本所在目录（保证相对路径可靠）
CENTOS7_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log_info "CentOS 7 平台: 准备 Python 3.9 环境..."

# 通用检测：是否已有 Python 3.9 可用（之前编译安装过）
for _py in /usr/local/bin/python3 /usr/bin/python3 /opt/rh/rh-python39/root/bin/python3; do
    if [ -x "$_py" ]; then
        ver=$("$_py" --version 2>&1)
        ver_num=$(echo "$ver" | grep -oP '[0-9]+\.[0-9]+' | head -1)
        major=${ver_num%%.*}; minor=${ver_num#*.}
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON3="$_py"
            USE_SYSTEM_PYTHON=true
            log_ok "Python 3.9 已存在，跳过安装: $ver"
            return 0
        fi
    fi
done

# ---- 策略 1：SCL rh-python39 ----
SCL_PYTHON="/opt/rh/rh-python39/root/bin/python3"
if [ -x "$SCL_PYTHON" ]; then
    PYTHON3="$SCL_PYTHON"
    USE_SYSTEM_PYTHON=true
    log_ok "使用 SCL Python 3.9: $SCL_PYTHON"
    return 0
fi

# ---- 策略 2：预编译包 ----
PYTHON_TGZ="$CENTOS7_DIR/rpms/python39_build.tar.gz"
PYTHON_PREFIX="/usr/local/python3"

if [ -f "$PYTHON_TGZ" ]; then
    log_info "尝试预编译包安装: $PYTHON_TGZ"

    # 清理旧版本
    rm -rf "$PYTHON_PREFIX" /usr/local/python39_build 2>/dev/null || true

    tar -xzf "$PYTHON_TGZ" -C /usr/local/

    # 兼容解压后目录名可能是 python39_build
    if [ -d /usr/local/python39_build ] && [ ! -d "$PYTHON_PREFIX" ]; then
        mv /usr/local/python39_build "$PYTHON_PREFIX"
    fi

    if [ -x "$PYTHON_PREFIX/bin/python3" ]; then
        # 验证 glibc 兼容性
        if $PYTHON_PREFIX/bin/python3 --version &>/dev/null; then
            PYTHON3="$PYTHON_PREFIX/bin/python3"
            USE_SYSTEM_PYTHON=true
            log_ok "预编译 Python 3.9 安装成功: $($PYTHON3 --version 2>&1)"
            return 0
        else
            log_warn "预编译包 glibc 不兼容 (当前: $(ldd --version 2>&1 | head -1))"
            rm -rf "$PYTHON_PREFIX" /usr/local/python39_build 2>/dev/null
        fi
    else
        log_warn "解压后未找到 python3 可执行文件"
        rm -rf "$PYTHON_PREFIX" /usr/local/python39_build 2>/dev/null
    fi
fi

# ---- 策略 3：源码编译 ----
PYTHON_SRC=$(ls "$CENTOS7_DIR/rpms/Python-3.9"*.tar.xz 2>/dev/null | head -1)

if [ -f "$PYTHON_SRC" ]; then
    # 检查是否已编译安装过（避免反复编译）
    if [ -x /usr/local/bin/python3 ]; then
        ver=$(/usr/local/bin/python3 --version 2>&1)
        ver_num=$(echo "$ver" | grep -oP '[0-9]+\.[0-9]+' | head -1)
        major=${ver_num%%.*}; minor=${ver_num#*.}
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON3="/usr/local/bin/python3"
            USE_SYSTEM_PYTHON=true
            log_ok "Python 3.9 已安装，跳过编译: $ver"
            return 0
        fi
    fi

    log_info "开始源码编译 Python 3.9 (约 5-15 分钟)..."

    # 检查编译依赖
    command -v gcc &>/dev/null || die "缺少 gcc，无法编译 Python。先执行: yum install -y gcc make"
    command -v make &>/dev/null || die "缺少 make，无法编译 Python"

    # 安装编译依赖（仅从 rpms/ 离线安装）
    log_info "安装编译依赖..."
    for rpm_pkg in openssl-devel bzip2-devel libffi-devel zlib-devel readline-devel sqlite-devel; do
        if ! rpm -qa 2>/dev/null | grep -qi "^${rpm_pkg}-"; then
            rpm_file=$(ls "$CENTOS7_DIR/rpms/${rpm_pkg}"*.rpm 2>/dev/null | head -1)
            if [ -f "$rpm_file" ]; then
                log_info "  安装 $rpm_pkg ..."
                rpm -ivh "$rpm_file" --nodeps 2>/dev/null || true
            fi
        fi
    done

    log_info "解压源码..."
    tar -xf "$PYTHON_SRC" -C /tmp/
    SRC_DIR="/tmp/$(basename "$PYTHON_SRC" .tar.xz)"
    cd "$SRC_DIR"

    # 安装到系统路径 /usr/local，使得 python3 直接在 PATH 中可用
    INSTALL_PREFIX="/usr/local"

    log_info "配置中..."
    if ! ./configure --prefix="$INSTALL_PREFIX" --enable-optimizations --with-ensurepip=install > /tmp/python39_configure.log 2>&1; then
        tail -20 /tmp/python39_configure.log
        die "Python 配置失败 (详情: /tmp/python39_configure.log)"
    fi
    log_ok "配置完成 (--prefix=$INSTALL_PREFIX, --enable-optimizations)"

    log_info "编译中 (使用 $(nproc) 核, 约 5-10 分钟)..."
    if ! make -j$(nproc) > /tmp/python39_make.log 2>&1; then
        tail -20 /tmp/python39_make.log
        die "Python 编译失败 (详情: /tmp/python39_make.log)"
    fi
    log_ok "编译完成"

    log_info "安装中..."
    if ! make install > /tmp/python39_install.log 2>&1; then
        tail -20 /tmp/python39_install.log
        die "Python 安装失败 (详情: /tmp/python39_install.log)"
    fi
    log_ok "安装完成"

    cd /tmp
    rm -rf "$SRC_DIR"

    if [ -x "/usr/local/bin/python3" ]; then
        PYTHON3="/usr/local/bin/python3"
        USE_SYSTEM_PYTHON=true
        log_ok "Python 3.9 源码编译安装成功: $($PYTHON3 --version 2>&1)"
        return 0
    else
        die "Python 源码编译失败"
    fi
fi

# ---- 所有策略均失败 ----
die "CentOS 7: 无法安装 Python 3.9\n  \
请选择以下方式之一:\n  \
  1. 安装 SCL: yum install -y centos-release-scl && yum install -y rh-python39\n  \
  2. 放置预编译包: platform/centos7/rpms/python39_build.tar.gz\n  \
  3. 放置源码包:   platform/centos7/rpms/Python-3.9.x.tar.xz + 编译依赖 RPM"
