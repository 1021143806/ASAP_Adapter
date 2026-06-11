基于代码分析，第三方开发需要对接的报文接口分为**上行**（第三方→本系统）和**下行**（本系统→第三方）两大类，以下是详细清单：

---

## 一、上行接口 — 第三方主动上报给本系统

### 1.1 门状态主动上报

**Endpoint:** `POST /changeDoorStatus`

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
>
> 必须报文

```Shell
##  健康检查接口

```
GET /actuator/health
```

- **认证**: 无（公开接口）
- **Content-Type**: `text/plain; charset=utf-8`
- **正常响应**: `1000` (HTTP 200)
- **设计说明**: 返回纯文本 `1000`（与华睿 ICS 接口返回码 `code: 1000` 保持一致），避免 JSON 解析开销，适合监控程序快速检测。

> 请求示例
```
GET /actuator/health
```

> 正常响应示例
```
1000
```
```


