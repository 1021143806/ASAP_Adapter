# 离线部署模块 — IRAYPLEOS / CentOS 7

## 概述

本目录包含 ASAP Adapter 的离线部署脚本和依赖包，目标服务器为 **IRAYPLEOS**（离线环境，Python 3.9）。

## 目录结构

```
deploy_iraypleos/
├── deploy_iraypleos.sh            # 主部署脚本（幂等）
├── prepare_portable_python.sh     # 便携 Python 制作工具（在开发机运行）
├── README.md                      # 本文档
└── vendor_packages3.9/            # 离线包仓库（Python 3.9 兼容）
    ├── requirements_asap_fixed.txt     # 版本锁定的依赖清单
    ├── *.whl                          # 离线 wheel 包
    └── python3.9/                     # [可选] 便携 Python 3.9 (由 prepare_portable_python.sh 生成)
```

## 架构设计

### 网络约束

目标服务器 **IRAYPLEOS** 为离线环境，无法连接 PyPI。部署模块采用「**在线打包 — 离线安装**」的双阶段模式：

```
开发机（在线）                         目标服务器（离线）
┌─────────────────┐                 ┌──────────────────────┐
│ pip download     │── scp/rsync ──▶│ vendor_packages3.9   │
│ → *.whl          │                 │ deploy_iraypleos.sh  │
│ → requirements   │                 │ venv/ (自动创建)      │
└─────────────────┘                 │ Supervisor 启动       │
                                    │ 端口 5012            │
                                    └──────────────────────┘
```

### 核心设计原则

| 原则 | 实现 |
|------|------|
| **零依赖部署** | 目标机器无需预装 Python，脚本自动检测并安装（便携包/包管理器） |
| **完全离线** | 所有依赖以 `.whl` 文件存储在 `vendor_packages3.9/` 中，安装时使用 `--no-index --find-links` |
| **幂等部署** | 脚本可反复执行，先清理旧 venv 再重建，不依赖环境状态 |
| **版本锁定** | `requirements_asap_fixed.txt` 精确锁死版本号 |
| **Supervisor 集成** | 配置 `/main/server/supervisor/asap_adapter.conf`，进程保活 |

## 部署流程

| 步骤 | 操作 | 说明 |
|------|------|------|
| 0 | Python 检测 | 自动检测 `python3`/`python3.9`/`python`，版本需 ≥ 3.9 |
| 0b | Python 安装 | 未找到时自动尝试：便携包 → yum → dnf → apt-get |
| 1 | vendor 检查 | 扫描关键 wheel 包是否存在 |
| 2 | 创建 venv | 使用检测到的 Python 创建虚拟环境 |
| 3 | 安装依赖 | 批量 `--no-index` 安装 → 失败则逐个安装 |
| 4 | 导入验证 | 验证 `fastapi`、`uvicorn`、`httpx` 等可导入 |
| 5 | Supervisor 配置 | 不存在时自动创建 |
| 6 | 启动服务 | `supervisorctl restart` → 直接 `nohup` |

## 便携 Python 3.9

如果目标机器完全 **没有 Python 3.9** 且无包管理器，可以使用便携 Python 包：

**1. 在开发机上制作便携包：**

```bash
bash deploy_iraypleos/prepare_portable_python.sh
```

脚本会自动在 `vendor_packages3.9/python3.9/` 下创建一个完整的便携 Python 3.9 环境。

**2. 将整个项目 rsync/scp 到目标机器：**

```bash
rsync -avz ASAP_Adapter/ user@target:/path/to/ASAP_Adapter
```

**3. 在目标机器运行部署脚本：**

```bash
bash deploy_iraypleos/deploy_iraypleos.sh
```

脚本会自动检测并使用 `vendor_packages3.9/python3.9/bin/python3`。

## 依赖清单

### Web 框架

| 包 | 说明 |
|----|------|
| fastapi | Web 框架 |
| uvicorn[standard] | ASGI 服务器 |
| starlette | ASGI 框架底层 |
| pydantic | 数据校验 |
| typing_extensions | 类型扩展 |

### HTTP 客户端

| 包 | 说明 |
|----|------|
| httpx | 异步 HTTP 客户端 |
| httpcore | HTTP 核心 |
| h11 | HTTP/1.1 协议 |
| certifi | SSL 证书 |
| sniffio | 异步库检测 |
| anyio | 异步运行时 |

## 维护指南

### 新增依赖包

```bash
cd deploy_iraypleos/vendor_packages3.9

# 下载兼容 Python 3.9 的 wheel
pip download --only-binary=:all: \
  --platform manylinux2014_x86_64 \
  --python-version 3.9 \
  <package_name>

# 添加到 requirements_asap_fixed.txt
echo "<package_name>==<version>" >> requirements_asap_fixed.txt

# 提交到 git
git add deploy_iraypleos/vendor_packages3.9/
```

### 验证命令

```bash
# 健康检查
curl -s http://localhost:5012/actuator/health

# WebUI
curl -s http://localhost:5012/

# 查看日志
tail -f /main/app/asap_adapter/logs/asap_adapter.log
```
