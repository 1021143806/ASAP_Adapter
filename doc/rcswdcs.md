基于代码分析，第三方开发需要对接的报文接口分为**上行**（第三方→本系统）和**下行**（本系统→第三方）两大类，以下是详细清单：

---

## 一、上行接口 — 第三方主动上报给本系统

### 1.1 门状态主动上报

**Endpoint:** `POST ip:7110/changeDoorStatus`

由第三方系统在门状态发生变化时主动调用。

```json
// 请求
{
  "doorNum": "1001",      // 门编号
  "doorStatus": "1"       // 1=开门, 2=关门
}

// 响应
{
  "code": 1000,
  "msg": "success",
  "data": null
}
```

> 注意：该接口直接将状态写入 Redis `DOOR_STATUS:{doorNum}`，本系统的定时任务会读取该值并上报到 RTPS/BMS。

---

## 二、下行接口 — 本系统调用第三方系统

### 2.1 门禁控制 — 开门/关门

**第三方系统需要提供 HTTP 接口**，URL 配置在数据库 `access_config.door_control_relation_id` 字段。

**调用方式：** `HTTP POST`

```json
// 请求体
{
  "doorCode": "1001",         // 门编号
  "status": 1,                // 1=开门, 2=关门  (注意: DoorStateEnum.OPEN.code=1, CLOSE.code=2)
  "deviceCode": "AGV001",     // 请求开门的AGV编号
  "qrName": "QR-CODE-001",    // 当前二维码内容
  "orderId": 10086,           // 任务ID
  "deviceNum": "AGV-001",     // AGV设备号
  "payLoad": "100",           // 负载重量
  "thirdOrderId": "ORDER001", // 第三方订单号
  "entryPoint": "NODE001"     // 入口节点
}

// 期望响应
{
  "code": 1000,               // 1000=成功, 其他=失败
  "msg": "success",
  "data": null
}
```

**配置方式：** 在数据库 `access_config` 表中：
- `control_way = 4`（THIRDPARTYSYSTEM）
- `door_control_relation_id` 填第三方的 HTTP URL

---

### 2.2 门状态查询

**第三方系统需要提供 HTTP 接口**，URL 配置在数据库 `access_config.door_status_relation_id` 字段。

**调用方式：** `HTTP POST`

```json
// 请求体
{
  "doorCode": "1001"
}

// 成功响应
{
  "code": 1000,
  "msg": "success",
  "data": {
    "status": 1      // 0=离线, 1=开门, 2=关门
  }
}

// 失败响应（本系统会将该门标记为 OFFLINE）
{
  "code": 9999,
  "msg": "error",
  "data": null
}
```

**配置方式：** 在数据库 `access_config` 表中：
- `control_way = 4`（THIRDPARTYSYSTEM）
- `door_status_relation_id` 填第三方的 HTTP URL
- `door_has_status = true/false` — 是否有状态反馈能力
- `reverse = 0/1` — 是否翻转 OPEN/CLOSE 状态值

> 如果 `reverse = 1`，状态映射规则：
> - 第三方返回 2（OPENREVERSE）→ 系统转成 1（OPEN）
> - 第三方返回 1（CLOSEREVERSE）→ 系统转成 2（CLOSE）

**调用频率：** 每 500ms 查询一次（可通过配置 `get.door.status.interval` 调整）。

---

## 三、第三方门禁平台对接 — 魔点 MoreDian 示例

系统内置了魔点云平台对接实现 `MoreDianDoorControl`，可作为第三方对接参考。

### 3.1 获取 Access Token

```http
GET https://oapi.moredian.com/org/getOrgAccessToken?orgId={orgId}&orgAuthKey={orgAuthKey}
```

```json
// 响应
{
  "data": {
    "accessToken": "xxx"
  }
}
```

### 3.2 根据 SN 获取 DeviceId

```http
GET https://oapi.moredian.com/device/deviceId?accessToken={token}&deviceSn={sn}
```

```json
// 响应
{
  "data": 123456789
}
```

### 3.3 开门指令

```http
POST https://oapi.moredian.com/device/notify/openDoor?accessToken={token}

{
  "deviceId": 123456789,
  "memberId": 1781175628343541765,
  "memberName": "仪表华睿AGV"
}

// 响应
{
  "result": "0"      // 0=成功
}
```

---

## 四、本系统上报给上层系统的报文

如果第三方开发者需要**消费**本系统上报的状态数据（如对接 RTPS 或 BMS），以下是上报格式：

### 4.1 门状态上报到 RTPS

由 `getRequestRTPSParam()` 方法组装，通过 ServiceConfig 配置的 URL 上报。

```json
{
  "doorNum": "1001",
  "areaId": 1,
  "status": 0,                  // 0=开门, 1=关门（注意：已做 -1 转换）
  "taskStatus": 1,              // 0=initializing, 1=idle, 2=running, 3=finish
  "increaseId": 1718000000000,  // 时间戳
  "deviceCode": "AGV001",       // 当前正在过门的AGV（如有）
  "deviceNum": "AGV-001",
  "taskId": "TASK001",
  "thirdOrderId": "ORDER001",    // 仅 returnOtherParam=true 时
  "entryPoint": "NODE001"
}
```

### 4.2 门状态上报到 BMS

使用 Feign 客户端调用 `bmsFeign.updateDeviceDoorStatus()`。

```json
{
  "doorNum": "1001",
  "areaId": 1,
  "status": 1,                // 1=开门, 2=关门, 0=离线
  "deviceCode": "AGV001",     // 当前正在过门的AGV
  "taskId": "TASK001",
  "deviceNum": "AGV-001",
  "thirdOrderId": "ORDER001",  // 仅 returnOtherParam=true 时
  "entryPoint": "NODE001"
}
```

---

## 五、第三方开发对接配置总结

### 数据库 `access_config` 表关键配置

```sql
-- 第三方系统对接需要配置的字段
control_way              = 4   -- 第三方系统
door_control_relation_id = "http://third-party.com/api/controlDoor"  -- 控制接口URL
door_status_relation_id  = "http://third-party.com/api/status"      -- 状态查询接口URL
door_has_status          = 1   -- true: 第三方有实时状态, false: 系统需自己管理状态
reverse                  = 0   -- 0: 正常(1=开,2=关), 1: 翻转(2=开,1=关)
pre_open_door_status     =     -- 预开门状态
door_allow_follow        = 0   -- 0: 不允许跟车, 1: 允许跟车
door_close_continue_time =     -- 门保持开启时间(ms)

-- 风淋门额外配置
door_type                = 2   -- 风淋门
air_shower_time          = 5000  -- 风淋等待时间(ms)
house_door_area          = "[100,200,300,400]"  -- 风淋区域矩形坐标[x1,y1,x2,y2]
another_door_num         = "1002"  -- 配对门编号(双门互锁)
need_close_another_door  = 1    -- 1: 需配对门关闭才可开门
```

### 对接协议汇总表

| 方向 | 接口 | 调用方 | 协议 | 频率 |
|---|---|---|---|---|
| 上行 | `POST /changeDoorStatus` | 第三方→本系统 | HTTP JSON | 状态变化时 |
| 下行 | 第三方控制URL | 本系统→第三方 | HTTP POST | AGV过门时 |
| 下行 | 第三方状态URL | 本系统→第三方 | HTTP POST | ~500ms/次 |
| 控制 | `/third/door/controlDoor` | 第三方/测试→本系统 | HTTP JSON | 按需 |
| 查询 | `/no/door/getStatus` | 第三方/测试→本系统 | HTTP JSON | 按需 |

> **注意：** 第三方系统如果无法提供 HTTP 状态查询接口，可以配置 `door_has_status = false`，此时系统通过 `UpdateDoorState` 线程在开门后延迟一段时间自动将状态设置为 OPEN，关门时再设置为 CLOSE，不依赖第三方的实时状态。

---

## 六、AB 自动门接口对接配置（ASAP → Angel AB门）

ASAP Adapter 通过 HTTP 协议控制 Angel AB 自动门，第三方的 AB 门系统需提供以下两个接口。

### 6.1 门控制

**Endpoint:** `POST /acs/door/{doorId}`

由 ASAP Adapter 在风淋流程中调用，控制门的开关。

```json
// 请求体
{
  "doorSerial": "DOOR_OUTER",
  "command": "1",               // 1=开门, 2=关门
  "Direction": "1",
  "RobotName": "AGV001"
}

// 期望响应
{
  "doorSerial": "DOOR_OUTER",
  "doorStatus": "1",            // 0=关, 1=开, 2=故障
  "command": "1",
  "code": "200"
}
```

**调用时机：**
| 步骤 | 动作 | 门 |
|------|------|-----|
| 步骤3 | 开外门 | DOOR_OUTER |
| 步骤6 | 关外门 | DOOR_OUTER |
| 步骤9 | 开内门 | DOOR_INNER |
| 步骤12 | 关内门 | DOOR_INNER |

### 6.2 门状态查询

**Endpoint:** `GET /acs/door/{doorId}`

开门/关门后轮询等待门到位。

```json
// 期望响应
{
  "doorSerial": "DOOR_OUTER",
  "doorStatus": "1",
  "command": "1",
  "code": "200"
}
```

轮询间隔 1 秒，超时 10 秒（可配置）。doorStatus=2（故障）立即终止。

### 6.3 配置方式

```toml
[angel]
base_url = "http://172.31.43.181:8080"   # AB 门地址
outer_door_id = "DOOR_OUTER"
inner_door_id = "DOOR_INNER"
poll_interval = 1.0
poll_timeout = 10.0
```

---

## 七、区域管控接口对接配置（ASAP → Zone Controller）

ASAP Adapter 通过 HTTP 请求区域管控系统，实现风淋区域的独占访问。

### 7.1 请求进入区域

**Endpoint:** `POST /api/zones/enter`

```json
// 请求体
{ "zone_id": "air_shower_room", "client_id": "asap_adapter" }

// 成功 200
{ "permission_id": "perm_abc123", "zone_id": "air_shower_room", "client_id": "asap_adapter", "status": "granted" }

// 被占用 409
{ "error": "Zone is currently occupied", "occupied_by": "AGV001" }
```

被占用时等待 5 秒重试，最多 60 次（可配置）。

### 7.2 退出区域

**Endpoint:** `POST /api/zones/exit`

```json
// 请求体
{ "zone_id": "air_shower_room", "client_id": "asap_adapter" }

// 响应
{ "zone_id": "air_shower_room", "client_id": "asap_adapter", "status": "released" }
```

退出失败时重试，间隔 5 秒，最多 10 次。

### 7.3 查询区域状态

**Endpoint:** `GET /api/zones/status?zone_id=air_shower_room`

```json
{ "zone_id": "air_shower_room", "status": "occupied", "occupied_by": "asap_adapter" }
```

### 7.4 配置方式

```toml
[zone]
enter_url = "http://zone-controller:8080/api/zones/enter"
exit_url  = "http://zone-controller:8080/api/zones/exit"
status_url = "http://zone-controller:8080/api/zones/status"
zone_id = "air_shower_room"
client_id = "asap_adapter"
retry_interval = 5.0
max_retries = 60
exit_retry_interval = 5.0
exit_max_retries = 10
```
