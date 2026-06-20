# ASAP Adapter Skill

## 项目概述

风淋门-区域管控协议适配器，Python 3.9+，FastAPI + httpx 异步架构。纯协议翻译层，无风淋状态机。

## 目录约定

- `asap_adapter/` — 核心 Python 包
  - `main.py` — FastAPI 入口 + 生命周期 + 后台区域轮询
  - `door_translator.py` — 协议翻译器 (RCS↔ACS + Direction 先开判定)
  - `zone_state_machine.py` — 区域管控状态机 (q001→进入/退出)
  - `door_client.py` — Angel HTTP 客户端 (httpx, 阻塞轮询)
  - `zone_client.py` — 区域管控 HTTP 客户端 (重试)
  - `config.py` — TOML 多优先级配置加载
  - `models.py` — 全协议 Pydantic 模型
  - `router.py` — 全部 HTTP API
  - `upgrade_service.py` — ZIP 升级/回滚
  - `static/` — WebUI (common.js/jsonHighlight/轮询日志)
- `sim_controller/` — 内置模拟器 (挂载到主端口 /sim 路由)
  - `state.py` — 模拟状态 (门+区域)
  - `router.py` — 模拟 ACS+Zone 协议端点
  - `static/index.html` — 模拟器 WebUI
- `config/env.toml` — 静态配置 (gitignore)
- `config/env.template.toml` — 配置模板
- `data/config.toml` — 统一运行时配置 (WebUI 编辑)
- `deploy_iraypleos/` — CentOS 7 离线部署
- `test/` — 测试脚本 (gitignore)
- `logs/` — 运行时日志 (gitignore)

## 关键命令

```bash
# 启动 (端口 5012, 含内置模拟器 /sim)
cd /main/app/github/ASAP_Adapter
source venv/bin/activate
python3 -m asap_adapter.main

# Supervisor 管理
supervisorctl status asap_adapter
supervisorctl restart asap_adapter

# 健康检查
curl http://localhost:5012/actuator/health

# WebUI
http://localhost:5012/           # 风淋门
http://localhost:5012/sim        # 模拟器
http://localhost:5012/zone       # 区域管控
http://localhost:5012/config     # 配置
http://localhost:5012/upgrade    # 升级

# 启用模拟器 (WebUI 风淋门页右上角按钮)
curl -X POST http://localhost:5012/api/asap/sim/enable

# 统一直看日志
curl http://localhost:5012/api/asap/logs?limit=10
```

## 部署升级

```bash
# 1. 制作升级包 (在项目根目录)
version="3.3.x"
cat > version.json << EOF
{"title":"v${version}: 说明"}
EOF
zip -r d.zip \
  asap_adapter/*.py \
  asap_adapter/static/ \
  sim_controller/*.py \
  sim_controller/static/ \
  version.json
rm version.json

# 2. 上传部署
curl -X POST http://10.68.2.10:5012/api/asap/upgrade/upload \
  -F "file=@d.zip" -F "remark=xxx"
sleep 5 && rm -f d.zip

# 3. 验证
curl http://10.68.2.10:5012/api/asap/upgrade/version
```

## 核心规则

### Door 映射
- RCS doorCode: `1001`/`1002` — 风淋门
- RCS doorCode: `q001` — 区域管控门
- ACS doorId: `DOOR01`/`DOOR02`
- 映射通过 `config.rcs.door_code_mapping` 配置

### Direction 判定
- 两门全关 → 先开哪扇决定 Direction
- `1001` (DOOR01) 先开 → Direction="2" (出)
- `1002` (DOOR02) 先开 → Direction="1" (进)
- Direction 写入后续所有 ACS 请求（含关门）
- 两门再全关 → 重置 Direction

### 区域管控预检
- RCS 发 q001 status=1 → 先查 Zone 占用状态
- 被本 AGV 占用 → 直接开 (已在区域内)
- 被其他 AGV 占用 → 返回 code=2001 拒绝
- 空闲 → 异步进入流程

### 后台区域轮询
- 默认 300s 间隔检查 Zone 状态
- 检测到外部释放 → 自动重置本地门状态为关闭
- `/api/asap/zone-status` 每次调用实时查询

### 模拟器状态值
- 门状态仅 `"0"`/`"1"`/`"2"` (ACS 规范)
- 过渡态 (开关中) 上报为 `"0"` (未开到位)
- 区域状态: `"available"` / `"occupied"`
- 模拟器 zone_id: `zone_001` (与配置一致)

### 配置优先级
```
data/config.toml > runtime.toml > overrides.json > env.toml > dataclass 默认值
```
所有默认值: DOOR01/DOOR02, zone_001, 端口 5012

### excluded files (升级保护)
`config/env.toml`, `venv/`, `logs/`, `backup/`, `test/`, `.git/`, `.gitignore`, `skill.md`, `README.md`, `deploy_iraypleos/`, `__pycache__/`, `*.pyc`

## 依赖管理

生产环境离线安装，依赖在 `deploy_iraypleos/vendor_packages3.9/`。
```bash
cd deploy_iraypleos/vendor_packages3.9
pip download --only-binary=:all: --platform manylinux2014_x86_64 --python-version 3.9 <pkg>
echo "<pkg>==<ver>" >> requirements_asap_fixed.txt
```

## 生产服务器

### 10.68.2.10 — 内网测试机
- 用户: ymsk, 密码: ?shenDA8899
- 项目: /main/app/github/ASAP_Adapter
- Supervisor: `supervisorctl status asap_adapter`
- 端口: 5012

### 172.31.43.181 — 生产备机
- 用户: a1 (同 ymsk 网络)
- 项目: /main/app/github/ASAP_Adapter
- Supervisor: `supervisorctl status asap_adapter`
- 端口: 5012

## ds 说

- 架构: 纯协议翻译层 (AirShowerTranslator) + 区域状态机 (ZoneStateMachine)。无风淋计时/编排
- Direction: 先开门判定，两门全关重置。这是唯一的"状态记忆"
- 日志: POST 轮询 `/api/asap/logs` (500条缓冲区)。删除了 SSE 避免复杂
- 前端: 5 独立页面，common.js 共享主题/JSON高亮/日志渲染/轮询
- 模拟器: 挂载到主端口 /sim，通过 WebUI 启用/禁用切换 Door/Zone URL
- 升级包必须含 `version.json` (非 v.json)，否则升级记录无变更说明
- 升级服务通过 `asap_adapter/__init__.py` 存在即视为合法包
- 配置页 `save_all_config()` 同步 zone_id 到 sim_controller
- 两台服务器部署需要分开执行，升级后手动启用模拟器
- 部署脚本创建 v.json 临时文件，打包后删除。已加 .gitignore
