# ASAP Adapter

**Air Shower Access Protocol Adapter** · 风淋门-区域管控协议适配器

> 协议转换网关，桥接 **Angel 风淋门协议**、**区域管控协议** 与 **RCS/WDCS 系统**，编排完整的风淋进入/驶离流程。

---

好的，这是一个报文转换模块的设计方案。

这是一个协议转换的模块，主要用于解析双方协议并翻译的功能。

### 模块名称
**ASAP Adapter** (Air Shower Access Protocol Adapter)

---

## 项目结构

```
ASAP_Adapter/
├── app.py                          # Supervisor 入口脚本
├── requirements.txt                # Python 依赖清单
├── readme.md                       # 本文档
├── .gitignore
├── config/
│   ├── env.toml                    # 启用的配置 (gitignore)
│   ├── env.template.toml           # 配置模板
│   └── old/                        # 配置备份
├── asap_adapter/                   # 核心包
│   ├── __init__.py
│   ├── main.py                     # FastAPI 入口 + 生命周期
│   ├── config.py                   # TOML 配置加载
│   ├── models.py                   # Pydantic 数据模型 (所有协议)
│   ├── door_client.py              # Angel 风淋门 HTTP 客户端
│   ├── zone_client.py              # 区域管控 HTTP 客户端
│   ├── rcs_reporter.py             # RCS/WDCS 状态上报客户端
│   ├── state_machine.py            # 风淋流程状态机 (核心编排)
│   ├── router.py                   # HTTP API 路由
│   ├── logger.py                   # 日志配置 (文件轮转)
│   └── static/
│       └── index.html              # WebUI 仪表盘 (单页)
├── doc/
│   ├── angel.md                    # Angel 风淋门协议文档
│   └── rcswdcs.md                  # RCS/WDCS 对接协议文档
├── deploy_iraypleos/               # IRAYPLEOS 离线部署
│   ├── deploy_iraypleos.sh         # 部署脚本 (幂等)
│   ├── README.md
│   └── vendor_packages3.9/         # 离线 wheel 包
│       ├── requirements_asap_fixed.txt
│       └── *.whl
├── test/                           # 测试脚本 (gitignore)
│   └── test_state_machine.py
├── logs/                           # 运行时日志 (gitignore)
├── venv/                           # 虚拟环境 (gitignore)
├── dev/                            # 开发调试 (gitignore)
└── backup/                         # 备份 (gitignore)
```

## 架构图

```
                    ┌─────────────────────────────────────────────┐
                    │            RCS / WDCS (上层调度)             │
                    │  POST /changeDoorStatus ← 状态上报           │
                    │  POST {control_url}        → 门禁控制       │
                    │  POST {status_url}         → 状态查询       │
                    └──────────────────┬──────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ASAP Adapter (:5012)                        │
│                                                                 │
│  ┌─────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│  │  HTTP API   │  │     SSE 推送      │  │     WebUI        │   │
│  │  /api/asap/ │  │  /api/sse/events  │  │  / (index.html)  │   │
│  │  /api/rcs/  │  │                  │  │  玻璃拟态仪表盘   │   │
│  └──────┬──────┘  └──────────────────┘  └──────────────────┘   │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │               State Machine (状态机)                      │   │
│  │  IDLE → REQUEST_ZONE → OPEN_OUTER → WAIT_OUTER_OPEN →   │   │
│  │  AGV_ENTERING → CLOSE_OUTER → WAIT_OUTER_CLOSE →        │   │
│  │  SHOWERING → OPEN_INNER → WAIT_INNER_OPEN →             │   │
│  │  AGV_EXITING → CLOSE_INNER → WAIT_INNER_CLOSE →         │   │
│  │  RELEASE_ZONE → IDLE                                     │   │
│  │  任意异常 → ERROR → 清理 → IDLE                          │   │
│  └──────────────────────────────────────────────────────────┘   │
│         │            │              │                            │
│         ▼            ▼              ▼                            │
│  ┌──────────┐ ┌────────────┐ ┌──────────────┐                  │
│  │ Door     │ │ Zone       │ │ RCS          │                  │
│  │ Client   │ │ Client     │ │ Reporter     │                  │
│  │(Angel    │ │(区域管控    │ │(状态上报)    │                  │
│  │ 协议)    │ │ 协议)      │ │              │                  │
│  └────┬─────┘ └─────┬──────┘ └──────┬───────┘                  │
└───────┼─────────────┼───────────────┼──────────────────────────┘
        │             │               │
        ▼             ▼               ▼
 ┌──────────┐  ┌────────────┐  ┌──────────────┐
 │ Angel    │  │ Zone API   │  │ RCS/WDCS     │
 │ 风淋门    │  │ 区域管控    │  │ System       │
 │ :8080    │  │ /api/zones │  │ /changeDoor  │
 └──────────┘  └────────────┘  └──────────────┘
```

---

## 设计思路

这个模块的核心任务是将高级的"风淋+区域管控"业务逻辑，转换为底层两个独立的API调用序列。

1.  **风淋门控制**：需要遵循"请求打开 -> 等待完全打开 -> 通过 -> 关闭"的状态机。
2.  **区域管控**：需要遵循"请求进入 -> 获得授权 -> (进行业务) -> 释放区域"的互斥逻辑。
3.  **关键约束**：从风淋门协议可见，只有当响应中的 `command` 和 `doorStatus` 同时为 `"1"` 时，AGV才能驶离。

## 模块职责

接收上层的单一指令（如：`ENTER_AIR_SHOWER`），编排和执行对下层的API调用序列，管理等待和重试，并最终向上层返回业务结果。

### 接口定义与报文转换流程

以下定义模块对外提供的指令，以及内部如何将指令转换为对风淋门和区域管控的HTTP调用。

#### 1. 进入风淋室流程

最核心的复杂流程，对应"设备在风淋门中进入门中时等待风淋完成"。

**上层调用模块指令：** `ENTER_AIR_SHOWER`

**模块内部执行序列：**

| 步骤 | 内部动作 | 调用的下层API | 报文转换/参数说明 |
| :--- | :--- | :--- | :--- |
| **1. 请求区域** | 尝试占用整个风淋室区域 | `POST /api/zones/enter` | **请求体：** `{"zone_id": "<目标区域ID>", "client_id": "<AGV编号>"}` <br> **成功处理 (200)**：获得 `permission_id`，进入下一步。 <br> **冲突处理 (409)**：等待并重试（重试次数及间隔可配置）。 |
| **2. 开外门** | 发送开门指令 | `POST /acs/door/DOOR_OUTER` | **请求体：** `{"doorSerial": "DOOR_OUTER", "command": "1", "Direction": "1", "RobotName": "<AGV编号>"}` |
| **3. 等门开** | 轮询外门状态，直至完全打开 | `GET /acs/door/DOOR_OUTER` | **成功条件：** 响应中 `command == "1"` 且 `doorStatus == "1"`。 <br> **故障处理：** 若 `code == "500"` 或 `doorStatus == "2"`，触发异常。 |
| **4. 通知AGV** | (模块边界)上报状态 | SSE 推送到RCS | `{"status": "DOOR_OPENED", "door": "outer"}` <br> 表示AGV可驶入风淋室。 |
| **5. 关外门** | AGV进入后，关门 | `POST /acs/door/DOOR_OUTER` | **请求体：** `{"doorSerial": "DOOR_OUTER", "command": "2"}` |
| **6. 等门关** | 轮询外门状态，确认关闭 | `GET /acs/door/DOOR_OUTER` | **成功条件：** `doorStatus == "0"`。 |
| **7. 启动风淋** | 内部计时等待 | (无API调用) | **内部计时：** 等待 `X` 秒（风淋时长，可配置）。 |
| **8. 开内门** | 风淋完成后，打开内门 | `POST /acs/door/DOOR_INNER` | **请求体：** `{"doorSerial": "DOOR_INNER", "command": "1", "Direction": "1", "RobotName": "<AGV编号>"}` |
| **9. 等门开** | 轮询内门状态 | `GET /acs/door/DOOR_INNER` | **成功条件：** `command == "1"` 且 `doorStatus == "1"`。 |
| **10. 通知AGV** | 上报状态 | SSE 推送到RCS | `{"status": "AIR_SHOWER_COMPLETE", "door": "inner"}` <br> AGV可驶离风淋室。 |
| **11. 关内门** | AGV驶离后，关门 | `POST /acs/door/DOOR_INNER` | **请求体：** `{"doorSerial": "DOOR_INNER", "command": "2"}` |
| **12. 等门关** | 轮询内门状态 | `GET /acs/door/DOOR_INNER` | **成功条件：** `doorStatus == "0"`。 |
| **13. 释放区域** | 整个流程结束，释放区域 | `POST /api/zones/exit` | **请求体：** `{"zone_id": "<目标区域ID>", "client_id": "<AGV编号>"}` <br> **失败处理：** 调用失败后重试，直到成功释放。 |

#### 2. 查询区域/门状态

**能力：** `GET_STATUS`

可通过 API `/api/asap/status` 获取整体状态，或通过 RCS 协议 `/api/rcs/doorStatus` 查询单个门状态。

#### 3. 紧急/手动控制

**能力：** `MANUAL_OPEN`， `MANUAL_CLOSE`

通过 API `/api/asap/manual/open` 和 `/api/asap/manual/close` 直接开门/关门，用于调试或紧急情况。

---

## API 端点

| 端点 | 方法 | 用途 | 来源 |
|------|------|------|------|
| `/` | GET | WebUI 仪表盘 | ASAP |
| `/actuator/health` | GET | 健康检查 (返回 `1000`) | RCS 协议 |
| `/api/asap/status` | GET | 获取风淋系统整体状态 | ASAP |
| `/api/asap/start` | POST | 启动风淋流程 | ASAP |
| `/api/asap/cancel` | POST | 取消风淋流程 | ASAP |
| `/api/asap/manual/open` | POST | 手动开门 | ASAP |
| `/api/asap/manual/close` | POST | 手动关门 | ASAP |
| `/api/asap/manual/close` | POST | 手动关门 | ASAP |
| `/api/sse/events` | GET | SSE 实时事件推送 | WebUI |
| `/api/rcs/controlDoor` | POST | RCS 门禁控制入口 | RCS 协议 |
| `/api/rcs/doorStatus` | POST | RCS 门状态查询 | RCS 协议 |
| `/static/*` | GET | 静态资源 | WebUI |

## 关键技术点

### AGV 交管

根据风淋门协议的约束：

> 1. 当前AB安全门有合力AGV通过，所以，需要KIVA 跟合力交管部分需要做交互区，同一时刻只能有一个辆车通行。
> 2. 当两个方向同时有车辆进入的时候，只能一车辆申请。

"区域管控"协议的独占式设计（`POST /api/zones/enter`）完全满足了这一需求。将整个风淋室定义为一个独占区域（例如 `zone_id: "air_shower_room"`），任何一方（KIVA或合力）的AGV在尝试进入前，都必须先成功调用此接口，从协议层面保证了同一时刻只有一辆车能进入该区域。

### 状态机

14个状态的严格状态机，任意步骤出现异常（门故障、区域冲突超限、网络超时等）均进入 `ERROR` 状态并执行清理（释放区域）。

### 离线部署

目标服务器 IRAYPLEOS 为离线环境，依赖以 `.whl` 形式存储在 `deploy_iraypleos/vendor_packages3.9/` 中，部署脚本幂等可反复执行。

---

## 配置说明 (`config/env.toml`)

| 配置段 | 键 | 默认值 | 说明 |
|--------|-----|--------|------|
| `server` | `port` | `5012` | 服务端口 |
| `angel` | `base_url` | `http://localhost:8080` | 风淋门 HTTP 基础地址 |
| `angel` | `outer_door_id` | `DOOR_OUTER` | 外门 ID |
| `angel` | `inner_door_id` | `DOOR_INNER` | 内门 ID |
| `angel` | `poll_interval` | `1.0` | 轮询间隔 (秒) |
| `angel` | `poll_timeout` | `30.0` | 轮询超时 (秒) |
| `zone` | `enter_url` | - | 区域进入 API |
| `zone` | `exit_url` | - | 区域退出 API |
| `zone` | `zone_id` | `air_shower_room` | 风淋室区域 ID |
| `zone` | `max_retries` | `10` | 区域冲突最大重试 |
| `air_shower` | `duration` | `15.0` | 风淋时长 (秒) |
| `rcs` | `change_status_url` | - | RCS 状态上报地址 |
| `rcs` | `door_code_mapping` | - | 门 ID → RCS doorCode 映射 |

---

## 模拟器 (SimController)

`sim_controller/` 提供了一个完全模拟 **Angel 风淋门协议 + 区域管控协议** 的调试工具，无需真实硬件即可测试 ASAP Adapter 的完整流程。

### 启动方式

```bash
# 方式一：分别启动
python3 sim_controller/app.py            # 模拟器 → 端口 5112
python3 app.py                           # ASAP    → 端口 5012

# 方式二：一键启动（推荐）
bash dev/start_all.sh                    # 同时启动模拟器 + ASAP
```

### 模拟器 WebUI

打开 `http://localhost:5112/`，可：

| 功能 | 说明 |
|------|------|
| **门状态面板** | 实时显示外门/内门状态，支持手动开/关/故障注入 |
| **区域管控面板** | 查看区域占用状态，支持强制占用/释放 |
| **模拟配置** | 调整开门/关门过渡延时，设置始终繁忙模式 |
| **快速测试** | 一键模拟风淋流程、区域冲突、门故障 |
| **请求日志** | 记录所有 API 请求和状态变化 |

### 模拟器 API

| 端点 | 方法 | 用途 |
|------|------|------|
| `POST /acs/door/{door_id}` | POST | 控制门（Angel 协议） |
| `GET /acs/door/{door_id}` | GET | 查询门状态 |
| `POST /api/zones/enter` | POST | 请求进入区域 |
| `POST /api/zones/exit` | POST | 退出区域 |
| `GET /api/zones/status` | GET | 查询区域状态 |
| `GET /api/sim/status` | GET | 获取模拟器完整状态 |
| `POST /api/sim/door/set` | POST | 手动设置门状态 |
| `POST /api/sim/door/fault` | POST | 注入/清除门故障 |
| `POST /api/sim/zone/busy` | POST | 强制设置区域占用 |
| `POST /api/sim/config/delays` | POST | 设置过渡延时 |
| `POST /api/sim/reset` | POST | 重置模拟器 |

### 配置对接

在 `config/env.toml` 中将地址指向模拟器即可：

```toml
[angel]
base_url = "http://localhost:5112"

[zone]
enter_url = "http://localhost:5112/api/zones/enter"
exit_url  = "http://localhost:5112/api/zones/exit"
status_url = "http://localhost:5112/api/zones/status"
```

---

## RCS 第三方对接配置

ASAP Adapter 启动后，RCS/WDCS 系统需在 `access_config` 表中进行以下配置：

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `control_way` | `4` | 第三方系统 |
| `door_control_relation_id` | `http://<ASAP_HOST>:5012/api/rcs/controlDoor` | 门禁控制 URL |
| `door_status_relation_id` | `http://<ASAP_HOST>:5012/api/rcs/doorStatus` | 状态查询 URL |
| `door_type` | `2` | 风淋门 |
| `door_has_status` | `true` | 启用实时状态 |
| 健康检查 | `GET /actuator/health` → `1000` | |

ASAP WebUI 首页底部实时显示上述配置地址（自动填充当前主机和端口）。
