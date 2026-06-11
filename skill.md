# ASAP Adapter Skill

## 项目概述

风淋门-区域管控协议适配器，Python 3.9+，FastAPI。

## 目录约定

- `asap_adapter/` — 核心 Python 包，模块化设计
- `config/env.toml` — 启用配置 (gitignore)
- `deploy_iraypleos/` — IRAYPLEOS 离线部署
- `test/` — 测试脚本 (gitignore)
- `logs/` — 运行时日志 (gitignore)

## 关键命令

```bash
# 启动 (开发)
python app.py

# 健康检查
curl http://localhost:5012/actuator/health

# 查看状态
curl http://localhost:5012/api/asap/status

# WebUI
open http://localhost:5012/
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
- 状态机14步：IDLE → ... → 风淋完成 → IDLE，异常进入 ERROR 并清理释放区域
- WebUI 通过 SSE 实时推送状态，端口 5012
- 部署目标 IRAYPLEOS，Python 3.9 离线环境
