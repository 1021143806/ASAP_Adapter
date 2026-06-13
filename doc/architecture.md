# ASAP Adapter 架构与技术文档

## 概述

ASAP (Air Shower Access Protocol) Adapter 是一个协议转换网关，核心职责是将**上层调度系统（RCS/WDCS）的业务指令**翻译为**底层风淋门硬件控制 + 区域管控**的 API 调用序列，并反向将设备状态上报给调度系统。

支持两种工作模式：
- **风淋门模式** (doorCode: 1001/1002)：通过 Angel 协议控制物理 AB 自动门
- **区域管控模式** (doorCode: q001/q002)：通过 Zone API 实现虚拟门的区域独占访问

---

## 系统架构

```mermaid
graph TB
    subgraph UP["上层调度"]
        RCS["RCS/WDCS<br/>下发 controlDoor<br/>查询 doorStatus<br/>接收 changeDoorStatus"]
    end

    subgraph ASAP["ASAP Adapter (FastAPI :5012)"]
        ROUTER["HTTP 路由层<br/>分流 controlDoor/doorStatus"]
        SM["状态机编排层<br/>StateMachine(14步) + ZoneStateMachine(6步)"]
        SSE["SSE 事件推送<br/>实时状态 + step_log"]
        WEB["WebUI SPA<br/>5 视图实时监控"]

        subgraph ADAPT["协议适配层"]
            DOOR["DoorClient<br/>Angel ACS 门控制"]
            ZONE["ZoneClient<br/>区域独占管控"]
            RPRT["RcsReporter<br/>状态上报 RCS"]
            SIM["SimController<br/>内置模拟器"]
        end
    end

    subgraph DOWN["下游系统"]
        ANGEL["Angel 风淋门<br/>POST/GET /acs/door/{id}"]
        ZONEAPI["区域管控服务<br/>POST /api/zones/{enter,exit,status}"]
    end

    RCS -->|POST| ROUTER
    ROUTER --> SM
    SM --> DOOR
    SM --> ZONE
    SM --> RPRT
    RPRT -->|上报| RCS
    DOOR -->|控制+轮询| ANGEL
    ZONE -->|进入+退出+查询| ZONEAPI
    SM -.->|SSE| SSE
    SSE -.->|EventSource| WEB
    ROUTER -.->|API| WEB
```

---

## 双模式分流

```mermaid
flowchart TD
    RCS["RCS controlDoor 请求<br/>{doorCode, status, deviceCode}"] --> CHECK{doorCode?}

    CHECK -->|"1001 / 1002"| AIR["🚪 风淋门模式"]
    CHECK -->|"q001 / q002"| ZONE_CTRL["📡 区域管控模式"]

    AIR --> SM["StateMachine.start()<br/>14步全自动流程"]
    SM --> S1["1.请求区域 → 开外门<br/>→ 等开 → AGV进入"]
    S1 --> S2["→ 关外门 → 等关<br/>→ ⏱ 风淋(4s)"]
    S2 --> S3["→ 开内门 → 等开<br/>→ AGV驶离 → 关内门<br/>→ 释放区域"]
    S3 --> REPORT["上报 RCS<br/>POST /changeDoorStatus"]

    ZONE_CTRL --> ZSM["ZoneStateMachine<br/>6步交互流程"]
    ZSM --> Z1["q001开 → 进入区域 → 上报开"]
    Z1 --> Z2["q001关 → 上报关"]
    Z2 --> Z3["q002开 → AGV在出口"]
    Z3 --> Z4["q002关 → 退出区域 → 上报关"]
    Z4 --> REPORT

    style AIR fill:#4a7,stroke:#263
    style ZONE_CTRL fill:#48a,stroke:#246
```

---

## 报文时序图

### 风淋门完整流程

```mermaid
sequenceDiagram
    participant RCS as RCS/WDCS
    participant ASAP as ASAP Adapter
    participant ANGEL as Angel 风淋门
    participant ZONE as 区域管控

    Note over RCS,ZONE: 🚪 风淋门模式 (doorCode=1001)

    RCS->>ASAP: POST /api/rcs/controlDoor<br/>{doorCode:"1001",status:1}
    ASAP-->>RCS: {code:1000,msg:"风淋流程已启动"}

    Note over ASAP: 步骤1: 请求区域
    ASAP->>ZONE: POST /api/zones/enter
    ZONE-->>ASAP: 200 {permission_id,status:"granted"}

    Note over ASAP: 步骤2-3: 开外门+等待
    ASAP->>ANGEL: POST /acs/door/DOOR01 {command:"1"}
    loop 轮询
        ASAP->>ANGEL: GET /acs/door/DOOR01
    end
    ANGEL-->>ASAP: {doorStatus:"1",command:"1",code:"200"}

    Note over ASAP: 步骤4: 上报外门开
    ASAP->>RCS: POST /changeDoorStatus<br/>{doorNum:"1001",doorStatus:"1"}

    Note over ASAP: 步骤5-6: 关外门+等待
    ASAP->>ANGEL: POST /acs/door/DOOR01 {command:"2"}
    loop 轮询
        ASAP->>ANGEL: GET /acs/door/DOOR01
    end
    ANGEL-->>ASAP: {doorStatus:"0",code:"200"}

    Note over ASAP: 步骤7: ⏱ 风淋4秒

    Note over ASAP: 步骤8-9: 开内门+等待
    ASAP->>ANGEL: POST /acs/door/DOOR02 {command:"1",Direction:"2"}
    loop 轮询
        ASAP->>ANGEL: GET /acs/door/DOOR02
    end
    ANGEL-->>ASAP: {doorStatus:"1",command:"1",code:"200"}

    Note over ASAP: 步骤10: 上报内门开
    ASAP->>RCS: POST /changeDoorStatus<br/>{doorNum:"1002",doorStatus:"1"}

    Note over ASAP: 步骤11-12: 关内门+等待
    ASAP->>ANGEL: POST /acs/door/DOOR02 {command:"2"}

    Note over ASAP: 步骤13: 释放区域
    ASAP->>ZONE: POST /api/zones/exit
    ZONE-->>ASAP: 200 {status:"released"}

    ASAP->>RCS: POST /changeDoorStatus<br/>{doorNum:"1002",doorStatus:"2"}
```

### 区域管控完整流程

```mermaid
sequenceDiagram
    participant RCS as RCS/WDCS
    participant ASAP as ASAP Adapter
    participant ZONE as 区域管控

    Note over RCS,ZONE: 📡 区域管控模式 (doorCode=q001/q002)

    RCS->>ASAP: status=1, doorCode="q001"
    ASAP-->>RCS: {code:1000,msg:"进入区域流程已启动"}

    Note over ASAP: 步骤1: 轮询+进入区域
    loop 轮询直到可用
        ASAP->>ZONE: GET /api/zones/status?zone_id=zone_001
    end
    ZONE-->>ASAP: {status:"available"}
    ASAP->>ZONE: POST /api/zones/enter
    ZONE-->>ASAP: 200 {status:"granted"}

    Note over ASAP: 步骤2: 上报 q001 开
    ASAP->>RCS: POST /changeDoorStatus<br/>{doorNum:"q001",doorStatus:"1"}

    Note over ASAP: 步骤3: RCS 关闭 q001
    RCS->>ASAP: status=2, doorCode="q001"
    ASAP-->>RCS: {code:1000,msg:"q001_closed"}
    ASAP->>RCS: POST /changeDoorStatus<br/>{doorNum:"q001",doorStatus:"2"}

    Note over ASAP: 步骤4: RCS 打开 q002
    RCS->>ASAP: status=1, doorCode="q002"
    ASAP-->>RCS: {code:1000,msg:"q002_opened"}

    Note over ASAP: 步骤5: RCS 关闭 q002 → 退出区域
    RCS->>ASAP: status=2, doorCode="q002"
    ASAP-->>RCS: {code:1000,msg:"退出区域流程已启动"}
    ASAP->>ZONE: POST /api/zones/exit
    ZONE-->>ASAP: 200 {status:"released"}
    loop 确认释放
        ASAP->>ZONE: GET /api/zones/status
    end
    ZONE-->>ASAP: {status:"available"}

    ASAP->>RCS: POST /changeDoorStatus<br/>{doorNum:"q002",doorStatus:"2"}
```

---

## 状态机设计

### 风淋门状态机 (`state_machine.py`)

```mermaid
stateDiagram-v2
    [*] --> IDLE

    IDLE --> REQUEST_ZONE : RCS status=1
    REQUEST_ZONE --> OPEN_OUTER_DOOR : 区域授权
    REQUEST_ZONE --> REQUEST_ZONE : 409 被占用,重试

    OPEN_OUTER_DOOR --> WAIT_OUTER_DOOR_OPEN : 指令已发
    WAIT_OUTER_DOOR_OPEN --> AGV_ENTERING : doorStatus=1

    AGV_ENTERING --> CLOSE_OUTER_DOOR : AGV入位/超时
    CLOSE_OUTER_DOOR --> WAIT_OUTER_DOOR_CLOSE
    WAIT_OUTER_DOOR_CLOSE --> SHOWERING : 外门已关

    SHOWERING --> OPEN_INNER_DOOR : 风淋结束
    OPEN_INNER_DOOR --> WAIT_INNER_DOOR_OPEN
    WAIT_INNER_DOOR_OPEN --> AGV_EXITING : doorStatus=1

    AGV_EXITING --> CLOSE_INNER_DOOR : AGV驶离/超时
    CLOSE_INNER_DOOR --> WAIT_INNER_DOOR_CLOSE
    WAIT_INNER_DOOR_CLOSE --> RELEASE_ZONE : 内门已关

    RELEASE_ZONE --> IDLE : 区域释放

    IDLE --> ERROR : 任意异常
    ERROR --> IDLE : 清理完成
```

**14步对应关系：**

| 步骤 | 状态 | 动作 | 下游调用 |
|:---:|------|------|----------|
| 1 | REQUEST_ZONE | 请求区域 | POST /api/zones/enter |
| 2 | OPEN_OUTER_DOOR | 开外门 | POST /acs/door/{outer} command:1 |
| 3 | WAIT_OUTER_DOOR_OPEN | 等开门 | GET /acs/door/{outer} 轮询 |
| 4 | AGV_ENTERING | AGV进入 | 上报RCS 外门开 |
| 5 | CLOSE_OUTER_DOOR | 关外门 | POST /acs/door/{outer} command:2 |
| 6 | WAIT_OUTER_DOOR_CLOSE | 等关门 | GET /acs/door/{outer} 轮询 |
| 7 | SHOWERING | 风淋 | 内部计时 4s |
| 8 | OPEN_INNER_DOOR | 开内门 | POST /acs/door/{inner} command:1 |
| 9 | WAIT_INNER_DOOR_OPEN | 等开门 | GET /acs/door/{inner} 轮询 |
| 10 | AGV_EXITING | AGV驶离 | 上报RCS 内门开 |
| 11 | CLOSE_INNER_DOOR | 关内门 | POST /acs/door/{inner} command:2 |
| 12 | WAIT_INNER_DOOR_CLOSE | 等关门 | GET /acs/door/{inner} 轮询 |
| 13 | RELEASE_ZONE | 释放区域 | POST /api/zones/exit |

### 区域管控状态机 (`zone_state_machine.py`)

```mermaid
stateDiagram-v2
    [*] --> IDLE

    IDLE --> ENTERING : q001 status=1
    ENTERING --> INSIDE : 进入区域成功
    INSIDE --> Q001_CLOSED : q001 status=2
    Q001_CLOSED --> Q002_OPENED : q002 status=1
    Q002_OPENED --> EXITING : q002 status=2
    EXITING --> IDLE : 退出区域完成

    IDLE --> ERROR : 异常
    ERROR --> IDLE : 清理
```

**6步对应关系：**

| 步骤 | 状态 | 触发 | 动作 |
|:---:|------|------|------|
| 1 | ENTERING | q001 status=1 | 轮询zone → POST enter |
| 2 | INSIDE | 进入成功 | 上报RCS q001开 |
| 3 | Q001_CLOSED | q001 status=2 | 上报RCS q001关 |
| 4 | Q002_OPENED | q002 status=1 | AGV在出口等待 |
| 5 | EXITING | q002 status=2 | POST exit → 轮询释放 |
| 6 | IDLE | 释放确认 | 上报RCS q002关 |

---

## 配置系统

```mermaid
flowchart LR
    subgraph 加载流程
        DEFAULT["代码默认值"] --> ENV["env.toml<br/>(server/log/静态)"]
        ENV --> OVERRIDE["overrides.json<br/>(兼容旧版)"]
        OVERRIDE --> RUNTIME["runtime.toml<br/>(热更新)"]
        RUNTIME --> UNIFIED["data/config.toml<br/>(统一配置 ⭐)"]
    end

    UNIFIED --> MEMORY["内存 AppConfig<br/>(热更新即时生效)"]
    MEMORY --> DOOR_CLIENT
    MEMORY --> ZONE_CLIENT
    MEMORY --> RCS_REPORTER
    MEMORY --> SIM_CTRL
```

**统一配置文件 `data/config.toml`：**

```toml
# ASAP Adapter 统一配置
# 版本: 5 | 更新: 2026-06-13 20:11:xx

[angel]                          # AB 自动门对接
base_url = "http://10.68.2.10:5012/sim"
outer_door_id = "DOOR01"
inner_door_id = "DOOR02"
poll_interval = 1.0
poll_timeout = 30.0

[zone]                           # 区域管控对接
enter_url = "http://127.0.0.1:5012/sim/api/zones/enter"
exit_url = "http://127.0.0.1:5012/sim/api/zones/exit"
status_url = "http://127.0.0.1:5012/sim/api/zones/status"
zone_id = "zone_001"
entry_door_code = "q001"
exit_door_code = "q002"
zone_poll_interval = 300.0

[rcs]                            # RCS/WDCS 对接
change_status_url = "http://10.68.2.10:7110/changeDoorStatus"
report_interval = 0.5

[rcs.door_code_mapping]          # 门编码映射
DOOR01 = "1001"
DOOR02 = "1002"

[air_shower]                     # 风淋参数
duration = 4.0

[sim]                            # 内置模拟器
auto_open_delay = 2.0
auto_close_delay = 2.0
zone_always_busy = false
zone_id = "zone_001"

[meta]
version = 5
```

**特性：**
- 热更新：保存即生效，无需重启
- 版本号：每次保存自动 +1，备份旧文件
- 合并保存：只更新修改的字段，其他保留不变
- 可视化编辑：WebUI 配置管理页统一管理所有配置

---

## 门编码映射

```mermaid
flowchart LR
    subgraph RCS["RCS 门编号"]
        R1001["1001"]
        R1002["1002"]
        Q001["q001"]
        Q002["q002"]
    end

    subgraph ASAP["ASAP 门 ID"]
        DOOR01["DOOR01<br/>(外门/进入)"]
        DOOR02["DOOR02<br/>(内门/退出)"]
    end

    subgraph PROTO["下游协议"]
        ANGEL_POST["POST /acs/door/DOOR01"]
        ANGEL_GET["GET /acs/door/DOOR01"]
        ZONE_ENTER["POST /api/zones/enter"]
        ZONE_EXIT["POST /api/zones/exit"]
    end

    R1001 -->|door_code_mapping| DOOR01
    R1002 -->|door_code_mapping| DOOR02
    Q001 -->|entry_door_code| ZONE_ENTER
    Q002 -->|exit_door_code| ZONE_EXIT
    DOOR01 --> ANGEL_POST
    DOOR01 --> ANGEL_GET
```

---

## 异常处理流程

```mermaid
flowchart TD
    ANY["任意步骤出错"] --> CHECK{错误类型}

    CHECK -->|门故障 doorStatus=2| FAULT["DOOR_FAULT"]
    CHECK -->|轮询超时| TIMEOUT["TIMEOUT"]
    CHECK -->|区域冲突超限| CONFLICT["ZONE_CONFLICT"]
    CHECK -->|网络异常| NET["NETWORK_ERROR"]
    CHECK -->|流程取消| CANCEL["CANCELLED"]

    FAULT --> SET_ERROR["状态 → ERROR<br/>记录错误信息"]
    TIMEOUT --> SET_ERROR
    CONFLICT --> SET_ERROR
    NET --> SET_ERROR
    CANCEL --> CANCEL_CLEAN["通过 _cancel_event<br/>安全终止所有步骤"]

    SET_ERROR --> CLEANUP["清理阶段"]
    CANCEL_CLEAN --> CLEANUP

    CLEANUP --> CHECK_ZONE{是否持有区域?}
    CHECK_ZONE -->|是| FORCE["exit_with_retry()<br/>强制释放区域"]
    CHECK_ZONE -->|否| SKIP["跳过释放"]
    FORCE --> DONE["状态 → IDLE"]
    SKIP --> DONE
```

---

## 模块依赖

```mermaid
classDiagram
    class FastAPIApp {
        +DoorClient door
        +ZoneClient zone
        +RcsReporter rcs
        +StateMachine sm
        +ZoneStateMachine zone_sm
        +SimController sim_controller
        +AppConfig config
    }

    class StateMachine {
        +start(agv_id) bool
        +cancel()
        +query_door_status(door_id)
        -_step_request_zone()
        -_step_open_outer_door()
        -_step_wait_outer_door_open()
        -_step_agv_entering()
        -_step_close_outer_door()
        -_step_showering()
        -_step_open_inner_door()
        -_step_release_zone()
    }

    class ZoneStateMachine {
        +handle_open(doorCode, agvId)
        +handle_close(doorCode)
        +door_status_by_code(doorCode) int
        -_enter_flow()
        -_exit_flow()
        -_wait_zone_available()
    }

    class DoorClient {
        +open_door(door_id, direction, robot)
        +close_door(door_id)
        +get_status(door_id)
        +wait_for_open(door_id)
        +wait_for_close(door_id)
    }

    class ZoneClient {
        +enter() ZoneEnterResponse
        +enter_with_retry()
        +exit() ZoneExitResponse
        +exit_with_retry()
        +get_status() ZoneStatusResponse
    }

    class RcsReporter {
        +report_door_open(door_id)
        +report_door_closed(door_id)
        +report_door_status(door_num, status)
    }

    class SimController {
        +control_door(door_id, command)
        +get_door_status(door_id)
        +snapshot() SimSnapshot
        +reset_all()
    }

    class HTTPRouter {
        +POST /api/rcs/controlDoor
        +POST /api/rcs/doorStatus
        +GET /api/asap/status
        +GET /api/asap/zone-status
        +POST /api/asap/start
        +POST /api/asap/cancel
        +GET /api/sse/events
        +GET /api/asap/config/all
        +POST /api/asap/config/all
        +POST /api/asap/sim/enable
        +POST /api/asap/sim/disable
    }

    FastAPIApp --> DoorClient
    FastAPIApp --> ZoneClient
    FastAPIApp --> RcsReporter
    FastAPIApp --> StateMachine
    FastAPIApp --> ZoneStateMachine
    StateMachine --> DoorClient
    StateMachine --> ZoneClient
    StateMachine --> RcsReporter
    ZoneStateMachine --> ZoneClient
    ZoneStateMachine --> RcsReporter
    HTTPRouter --> StateMachine : 路由调用
    HTTPRouter --> ZoneStateMachine : 分流调用
```

---

## WebUI 视图路由

```mermaid
flowchart TD
    SP["index.html<br/>单页应用 (SPA)"] --> NAV["顶部导航栏"]

    NAV --> VIEW_DOOR["🚪 风淋门<br/>view-door"]
    NAV --> VIEW_ZONE["📡 区域管控<br/>view-zone"]
    NAV --> VIEW_CFG["⚙ 配置管理<br/>view-config"]
    NAV --> VIEW_SIM["🎮 模拟器<br/>view-simulator"]
    NAV --> VIEW_UPG["⬆ 升级<br/>view-upgrade"]

    VIEW_DOOR --> D1["流程步骤节点<br/>(13步+风淋)"]
    VIEW_DOOR --> D2["门状态卡片<br/>(外门+内门)"]
    VIEW_DOOR --> D3["SSE 实时日志"]
    VIEW_DOOR --> D4["步骤报文详情<br/>(点击展开JSON)"]

    VIEW_ZONE --> Z1["区域门卡片<br/>(q001+q002)"]
    VIEW_ZONE --> Z2["区域状态<br/>(占用/释放/AGV)"]
    VIEW_ZONE --> Z3["模拟器指引"]

    VIEW_CFG --> C1["统一配置编辑<br/>(读取+保存+版本)"]
    VIEW_CFG --> C2["运行时配置编辑器<br/>(runtime.toml)"]
    VIEW_CFG --> C3["静态配置编辑器<br/>(env.toml)"]

    VIEW_SIM --> S1["门控制面板<br/>(开门/关门/故障)"]
    VIEW_SIM --> S2["区域控制<br/>(强制占用/释放)"]
    VIEW_SIM --> S3["⏱ 风淋倒计时"]

    style VIEW_CFG fill:#48a,stroke:#246
```

---

## 风淋时序甘特图

```mermaid
gantt
    title 风淋门流程时间线 (风淋4秒)
    dateFormat  X
    axisFormat %s秒

    section 外门
    请求区域           :a1, 0, 1
    开外门             :a2, after a1, 1
    等外门开(轮询)     :a3, after a2, 2
    AGV进入            :a4, after a3, 3
    关外门             :a5, after a4, 1
    等外门关(轮询)     :a6, after a5, 2

    section 风淋
    风淋计时           :b1, after a6, 4

    section 内门
    开内门             :c1, after b1, 1
    等内门开(轮询)     :c2, after c1, 2
    AGV驶离            :c3, after c2, 3
    关内门             :c4, after c3, 1
    等内门关(轮询)     :c5, after c4, 2
    释放区域           :c6, after c5, 1
```

---

## 报文映射总表

| 步骤 | RCS 输入 | ASAP 处理 | 下游调用 | 响应/RCS上报 |
|:---:|----------|-----------|----------|------------|
| 1 | `controlDoor {doorCode:"1001",status:1}` | 检测开门请求 | `POST /api/zones/enter` | 获得 permission_id |
| 2 | — | 区域授权通过 | `POST /acs/door/DOOR01 {command:"1",Direction:"1"}` | doorStatus: opening |
| 3 | — | 轮询等门全开 | `GET /acs/door/DOOR01` | doorStatus:"1",code:"200" |
| 4 | — | 门开→通知RCS | — | `POST /changeDoorStatus {doorNum:"1001",doorStatus:"1"}` |
| 5 | — | 超时后关外门 | `POST /acs/door/DOOR01 {command:"2"}` | |
| 6 | — | 轮询等门关 | `GET /acs/door/DOOR01` | doorStatus:"0" |
| 7 | — | ⏱ 风淋计时 4s | — | — |
| 8 | — | 开内门 | `POST /acs/door/DOOR02 {command:"1",Direction:"2"}` | |
| 9 | — | 轮询等门开 | `GET /acs/door/DOOR02` | doorStatus:"1" |
| 10 | — | 门开→通知RCS | — | `POST /changeDoorStatus {doorNum:"1002",doorStatus:"1"}` |
| 11 | — | 超时后关内门 | `POST /acs/door/DOOR02 {command:"2"}` | |
| 12 | — | 轮询等门关 | `GET /acs/door/DOOR02` | doorStatus:"0" |
| 13 | — | 释放区域 | `POST /api/zones/exit` | status:"released" |
| — | `controlDoor {status:2}` | 手动关门 | `POST /acs/door/{id} {command:"2"}` | |

---

## 实时更新机制

```mermaid
sequenceDiagram
    participant SM as 状态机
    participant SSE as SSE 事件总线
    participant WEB as WebUI

    Note over SM,WEB: 正常运行 (SSE)
    SM->>SSE: 状态变更事件 (snapshot)
    SSE-->>WEB: data: {state, step, doors, ...}
    WEB->>WEB: updateDashboard() + renderStepNodes()

    SM->>SSE: zone_snapshot 事件
    SSE-->>WEB: data: {state, entry_door, exit_door, ...}
    WEB->>WEB: updateZoneDashboard()

    Note over SM,WEB: 异常降级 (轮询)
    SSE--xWEB: 连接失败
    WEB->>WEB: sseConnected = false
    loop 每3秒
        WEB->>SM: GET /api/asap/status
        SM-->>WEB: status JSON
        WEB->>WEB: updateDashboard()
    end
```

---

## 并发安全

- **单流程限制**：状态机一次只允许一个流程运行。`start()` 检查 `is_busy`。
- **异步非阻塞**：状态机在 `asyncio.Task` 中运行，不阻塞 HTTP 请求。
- **取消安全**：`_cancel_event` + `CancelledError` 安全终止所有步骤。
- **区域互斥**：通过 Zone API 独占式进入实现，同一时刻只有一辆车占用区域。
- **后台轮询**：区域状态 5 分钟定时轮询，进入/退出前后加速到 5 秒。

---

## 部署与升级

- **启动**：supervisor 管理，`python main.py` 或 `uvicorn`
- **端口**：5012 (API + WebUI + SSE + 模拟器)
- **配置**：`data/config.toml` 持久化，热更新即时生效
- **升级**：WebUI ZIP 上传 → 自动备份 → 解压覆盖 → 重启
- **日志**：`logs/asap.log`，5MB 轮转，保留 3 份
- **模拟器**：可选模块，`sim_controller/` 不存在时自动降级

---

## 版本历史

| 版本 | 主要变更 |
|------|----------|
| v1.x | 基础风淋门流程、区域管控对接 |
| v1.5.0 | 步骤流程节点可视化 + 报文详情 |
| v1.6.0 | 区域管控后台定时轮询 |
| v1.7.0 | ZoneStateMachine 独立状态机 (q001/q002) |
| v1.10.0 | 风淋倒计时 (默认4s) |
| v1.11.0 | q002 先开后关流程 + 报文明晰度 |
| v2.0.0 | 统一配置 data/config.toml + 版本管理 |
