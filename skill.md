# ASAP Adapter Skill

## 项目概述

风淋门-区域管控协议适配器，Python 3.9+，FastAPI。

## 目录约定

- `asap_adapter/` — 核心 Python 包，模块化设计
  - `main.py` — FastAPI 入口 + 生命周期
  - `state_machine.py` — 14 步风淋流程状态机
  - `upgrade_service.py` — ZIP 包升级管理（备份/回滚/记录）
  - `router.py` — 所有 HTTP API 路由（RCS 对接/管理/升级/SSE）
  - `static/` — WebUI 仪表盘 + 升级管理页面
- `sim_controller/` — 风淋门+区域管控模拟器（端口 5112，调试用）
- `config/env.toml` — 启用配置 (gitignore)
- `deploy_iraypleos/` — IRAYPLEOS 离线部署（含 Python 自动安装）
- `test/` — 测试脚本 (gitignore)
- `logs/` — 运行时日志 (gitignore)

## 关键命令

```bash
# 启动 ASAP (端口 5012)
python app.py

# 启动模拟器 (端口 5112)
python sim_controller/app.py

# 一键启动两者
bash dev/start_all.sh

# 健康检查
curl http://localhost:5012/actuator/health

# WebUI
open http://localhost:5012/

# 模拟器 WebUI
open http://localhost:5112/

# 升级管理 WebUI
open http://localhost:5012/upgrade
```

## 升级管理

支持通过 POST ZIP 包在线升级，自动备份当前代码，可回滚。

### 制作升级包

```bash
# 项目根目录打包需要更新的文件，不含排除项：
# config/env.toml, venv/, logs/, backup/, .git/ 等自动保留
zip -r upgrade_v1.1.0.zip app.py asap_adapter/ requirements.txt

# 可选放入 version.json（含版本说明）:
echo '{"title":"v1.1.0 安全更新","changes":["修复xxx","新增xxx"]}' > version.json
```

### 上传升级

```bash
# WebUI: http://localhost:5012/upgrade（拖拽上传）
# 或 curl:
curl -X POST http://HOST:5012/api/asap/upgrade/upload \
  -F "file=@upgrade_v1.1.0.zip" \
  -F "remark=修复xxx"
```

## 依赖管理

生产环境离线安装，依赖在 `deploy_iraypleos/vendor_packages3.9/` 中。

新增依赖：
```bash
cd deploy_iraypleos/vendor_packages3.9
pip download --only-binary=:all: --platform manylinux2014_x86_64 --python-version 3.9 <pkg>
echo "<pkg>==<ver>" >> requirements_asap_fixed.txt
```

## ds 说

- 架构：FastAPI + httpx 异步架构，状态机驱动流程编排
- 状态机 14 步：IDLE → REQUEST_ZONE → OPEN_OUTER → WAIT_OUTER_OPEN → AGV_ENTERING → CLOSE_OUTER → WAIT_OUTER_CLOSE → SHOWERING → OPEN_INNER → WAIT_INNER_OPEN → AGV_EXITING → CLOSE_INNER → WAIT_INNER_CLOSE → RELEASE_ZONE → IDLE
- 异常处理：任意步骤出错 → ERROR → 释放区域 → IDLE
- WebUI 通过 SSE 实时推送状态，端口 5012
- RCS 配置在 WebUI 底部直接编辑（状态上报 URL），即时生效
- 升级管理：POST ZIP 包升级，自动备份，支持回滚
- 模拟器：完全模拟 Angel + Zone 协议，WebUI 可视化控制，日志可查看请求/响应报文详情
- 部署目标 IRAYPLEOS / CentOS 7，Python 3.9 离线环境，脚本自动检测并安装 Python
