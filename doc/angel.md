好的，已为您将上述所有内容合并整理为一份完整的 **《风淋门对接 API 文档》**，便于直接查阅和开发对接。

---

# 风淋门对接 API 文档

## 1. AB自动门接口 (Angel 协议)

**基础信息**
*   **角色**：ACS 作为 Server
*   **协议**：HTTP
*   **内容类型**：`application/json`
*   **AB门互斥规则**：同一时刻仅允许一辆车通行；当两个方向同时有车辆时，只能一方申请。

---

### 1.1 自动门控制 (POST)

- **功能**：控制自动门开关
- **URL**：`http://{ip}:{port}/acs/door/{DOOR01}`
- **方法**：`POST`

**请求参数 (Body)**

| 参数名 | 类型 | 必填 | 说明 | 取值 |
|:---|:---|:---|:---|:---|
| `doorSerial` | String | 是 | 自动门序列号 | 与 URL 中 `{DOOR01}` 一致 |
| `command` | String | 是 | 控制命令 | `"0"`：无动作<br>`"1"`：打开<br>`"2"`：关闭 |
| `Direction` | String | 否 | 进入方向 | `"1"`：进<br>`"2"`：出 |
| `RobotName` | String | 否 | 机器人编号 | |

**请求示例**
```json
{
  "doorSerial": "DOOR01",
  "command": "1",
  "Direction": "1",
  "RobotName": "AGV-001"
}
```

**响应参数**

| 参数名 | 类型 | 说明 | 取值 |
|:---|:---|:---|:---|
| `doorSerial` | String | 自动门序列号 | 与请求一致 |
| `doorStatus` | String | 门系统状态 | `"0"`：已关闭<br>`"1"`：完全打开到位<br>`"2"`：故障 |
| `command` | String | 命令回显 | 与请求 `command` 一致 |
| `code` | String | 状态码 | `"200"`：正常<br>`"500"`：异常 |

**响应示例（成功）**
```json
{
  "doorSerial": "DOOR01",
  "doorStatus": "1",
  "command": "1",
  "code": "200"
}
```

**响应示例（故障）**
```json
{
  "doorSerial": "DOOR01",
  "doorStatus": "2",
  "command": "1",
  "code": "500"
}
```

> **关键规则**：**只有当 `command` 和 `doorStatus` 同时为 `"1"` 时，AGV 才能驶离自动门。**

---

### 1.2 获取自动门状态 (GET)

- **功能**：查询自动门当前状态
- **URL**：`http://{ip}:{port}/acs/door/{DOOR01}`
- **方法**：`GET`

**响应参数**

| 参数名 | 类型 | 说明 | 取值 |
|:---|:---|:---|:---|
| `doorSerial` | String | 自动门序列号 | |
| `doorStatus` | String | 门系统状态 | `"0"`：未开到位<br>`"1"`：完全打开到位<br>`"2"`：故障 |
| `command` | String | 最近一次控制命令 | `"0"`：无动作<br>`"1"`：打开<br>`"2"`：关闭 |
| `code` | String | 状态码 | `"200"`：正常<br>`"500"`：异常 |

**响应示例（门已打开）**
```json
{
  "doorSerial": "DOOR01",
  "doorStatus": "1",
  "command": "1",
  "code": "200"
}
```

**响应示例（门未打开）**
```json
{
  "doorSerial": "DOOR01",
  "doorStatus": "0",
  "command": "0",
  "code": "200"
}
```

---

## 2. 区域管控接口

- **功能**：区域独占式管控，某方占用后其他方不可进入。许可方所有车辆均驶出后，再调用退出接口。

---

### 2.1 请求进入区域 (POST)

- **功能**：申请独占区域
- **URL**：`http://{ip}:{port}/api/zones/enter`
- **方法**：`POST`

**请求参数 (Body)**

| 参数名 | 类型 | 说明 |
|:---|:---|:---|
| `zone_id` | String | 区域 ID |
| `client_id` | String | 客户端/车辆标识 |

**请求示例**
```json
{
  "zone_id": "zone_001",
  "client_id": "client_a"
}
```

**响应参数（成功 200）**

| 参数名 | 类型 | 说明 |
|:---|:---|:---|
| `permission_id` | String | 本次占用许可 ID |
| `zone_id` | String | 区域 ID |
| `client_id` | String | 客户端标识 |
| `status` | String | 固定为 `"granted"` |

**响应示例（成功）**
```json
{
  "permission_id": "perm_123456",
  "zone_id": "zone_001",
  "client_id": "client_a",
  "status": "granted"
}
```

**响应参数（冲突 409）**

| 参数名 | 类型 | 说明 |
|:---|:---|:---|
| `error` | String | `"Zone is currently occupied"` |
| `occupied_by` | String | 当前占用者 `client_id` |

**响应示例（被占用）**
```json
{
  "error": "Zone is currently occupied",
  "occupied_by": "client_b"
}
```

---

### 2.2 退出区域 (POST)

- **功能**：释放已占用区域
- **URL**：`http://{ip}:{port}/api/zones/exit`
- **方法**：`POST`

**请求参数 (Body)**

| 参数名 | 类型 | 说明 |
|:---|:---|:---|
| `zone_id` | String | 区域 ID |
| `client_id` | String | 客户端/车辆标识 |

**请求示例**
```json
{
  "zone_id": "zone_001",
  "client_id": "client_a"
}
```

**响应参数（成功 200）**

| 参数名 | 类型 | 说明 |
|:---|:---|:---|
| `zone_id` | String | 区域 ID |
| `client_id` | String | 客户端标识 |
| `status` | String | 固定为 `"released"` |

**响应示例（成功）**
```json
{
  "zone_id": "zone_001",
  "client_id": "client_a",
  "status": "released"
}
```

---

### 2.3 查询区域状态 (GET)

- **功能**：查询区域占用状态
- **URL**：`http://{ip}:{port}/api/zones/status`
- **方法**：`GET`

**请求参数 (Query)**

| 参数名 | 类型 | 说明 |
|:---|:---|:---|
| `zone_id` | String | 区域 ID |

**请求示例**
```
GET /api/zones/status?zone_id=zone_001
```

**响应参数（成功 200）**

| 参数名 | 类型 | 说明 |
|:---|:---|:---|
| `zone_id` | String | 区域 ID |
| `status` | String | `"occupied"` 或 `"available"` |
| `occupied_by` | String | 占用者 `client_id`（仅占用时返回） |

**响应示例（已占用）**
```json
{
  "zone_id": "zone_001",
  "status": "occupied",
  "occupied_by": "client_b"
}
```

**响应示例（可用）**
```json
{
  "zone_id": "zone_001",
  "status": "available"
}
```