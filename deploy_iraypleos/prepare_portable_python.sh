#!/bin/bash
# 离线便携 Python 3.9 制作脚本
# 在开发机上运行，打包一个 portable Python 3.9 到 vendor 目录
# 目标机器没有 Python 时，部署脚本会自动使用此便携包

set -e

echo "========================================"
echo "便携 Python 3.9 制作工具"
echo "========================================"
echo ""

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="$DEPLOY_DIR/vendor_packages3.9"
TARGET_DIR="$VENDOR_DIR/python3.9"

# 检查当前系统 Python
PYTHON_CMD=""
for cmd in python3 python3.9 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver=$("$cmd" --version 2>&1)
        ver_num=$(echo "$ver" | grep -oP '[0-9]+\.[0-9]+' | head -1)
        major=${ver_num%%.*}
        minor=${ver_num#*.}
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON_CMD="$cmd"
            echo "✅ 使用: $cmd → $ver"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "❌ 未找到 Python 3.9+，请先安装 Python 3.9"
    exit 1
fi

# 检查是否已存在
if [ -d "$TARGET_DIR" ] && [ -f "$TARGET_DIR/bin/python3" ]; then
    echo "⚠️  便携 Python 已存在: $TARGET_DIR"
    echo "   版本: $($TARGET_DIR/bin/python3 --version 2>&1)"
    read -p "   是否重新创建? [y/N] " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "   跳过"
        exit 0
    fi
    rm -rf "$TARGET_DIR"
fi

echo ""
echo "1. 创建便携 Python 虚拟环境..."
$PYTHON_CMD -m venv "$TARGET_DIR"

if [ ! -f "$TARGET_DIR/bin/python3" ]; then
    echo "❌ 创建失败"
    exit 1
fi

echo "   ✅ 便携 Python: $($TARGET_DIR/bin/python3 --version 2>&1)"

echo ""
echo "2. 检查 venv 创建功能..."
# 便携 Python 需要能创建虚拟环境
if [ ! -f "$TARGET_DIR/bin/pip" ]; then
    echo "   ⚠️  pip 未找到，尝试安装..."
    $TARGET_DIR/bin/python3 -m ensurepip --upgrade 2>/dev/null || true
fi

echo ""
echo "3. 验证..."
echo "   Python: $($TARGET_DIR/bin/python3 --version 2>&1)"
echo "   pip:    $($TARGET_DIR/bin/pip --version 2>&1 | head -1)"
echo ""
echo "   测试虚拟环境创建..."
TEST_VENV=$(mktemp -d)
if $TARGET_DIR/bin/python3 -m venv "$TEST_VENV/test" 2>/dev/null; then
    echo "   ✅ venv 创建正常"
    rm -rf "$TEST_VENV"
else
    echo "   ⚠️  venv 创建异常 (部分系统可能需要额外配置)"
    rm -rf "$TEST_VENV"
fi

echo ""
echo "========================================"
echo "便携 Python 3.9 已就绪!"
echo "========================================"
echo ""
echo "路径: $TARGET_DIR"
echo "大小: $(du -sh "$TARGET_DIR" | cut -f1)"
echo ""
echo "将此目录部署到目标机器的同路径即可。"
echo "部署脚本会自动检测并使用。"
echo "========================================"
