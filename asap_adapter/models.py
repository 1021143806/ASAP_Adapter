"""
数据模型定义

包含所有涉及协议的数据模型：
  - Angel 风淋门协议（请求/响应）
  - 区域管控协议（请求/响应）
  - RCS/WDCS 对接协议（请求/响应）
  - ASAP 内部状态模型
"""

from enum import Enum
from typing import Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════
#  Angel 风淋门协议
# ═══════════════════════════════════════════

class AngelCommand(str, Enum):
    """风淋门控制指令"""
    NO_ACTION = "0"
    OPEN = "1"
    CLOSE = "2"


class AngelDirection(str, Enum):
    """进门方向"""
    ENTER = "1"   # 进
    EXIT = "2"    # 出


class AngelDoorStatus(str, Enum):
    """门状态"""
    CLOSED = "0"           # 门已关闭
    OPENED = "1"           # 门完全打开到位
    FAULT = "2"            # 故障
    UNKNOWN = "-1"         # 未知


class AngelCode(str, Enum):
    """响应码"""
    OK = "200"
    ERROR = "500"


class AngelControlRequest(BaseModel):
    """POST /acs/door/{DOOR_ID} 请求体"""
    doorSerial: str
    command: str                              # 0=无动作, 1=开, 2=关
    Direction: Optional[str] = None           # 1=进, 2=出
    RobotName: Optional[str] = None           # AGV编号


class AngelControlResponse(BaseModel):
    """POST /acs/door/{DOOR_ID} 响应体"""
    doorSerial: str = ""
    doorStatus: str = "0"
    command: str = "0"
    code: str = "200"


class AngelStatusResponse(BaseModel):
    """GET /acs/door/{DOOR_ID} 响应体"""
    doorSerial: str = ""
    doorStatus: str = "0"
    command: str = "0"
    code: str = "200"


# ═══════════════════════════════════════════
#  区域管控协议
# ═══════════════════════════════════════════

class ZoneStatus(str, Enum):
    """区域状态"""
    GRANTED = "granted"
    RELEASED = "released"
    OCCUPIED = "occupied"
    AVAILABLE = "available"


class ZoneEnterRequest(BaseModel):
    """POST /api/zones/enter 请求体"""
    zone_id: str
    client_id: str


class ZoneEnterResponse(BaseModel):
    """POST /api/zones/enter 成功响应"""
    permission_id: str = ""
    zone_id: str = ""
    client_id: str = ""
    status: str = ""


class ZoneEnterConflict(BaseModel):
    """POST /api/zones/enter 冲突响应 (409)"""
    error: str = ""
    occupied_by: str = ""


class ZoneExitRequest(BaseModel):
    """POST /api/zones/exit 请求体"""
    zone_id: str
    client_id: str


class ZoneExitResponse(BaseModel):
    """POST /api/zones/exit 响应"""
    zone_id: str = ""
    client_id: str = ""
    status: str = ""


class ZoneStatusResponse(BaseModel):
    """GET /api/zones/status 响应"""
    zone_id: str = ""
    status: str = ""            # "occupied" 或 "available"
    occupied_by: str = ""


# ═══════════════════════════════════════════
#  RCS/WDCS 对接协议
# ═══════════════════════════════════════════

class RcsDoorControlRequest(BaseModel):
    """RCS → ASAP: 门禁控制请求"""
    doorCode: str = Field(default="", description="门编号")
    status: int = Field(default=0, description="1=开门, 2=关门")
    deviceCode: str = Field(default="", description="AGV编号")
    qrName: str = Field(default="", description="二维码内容")
    orderId: int = Field(default=0, description="任务ID")
    deviceNum: str = Field(default="", description="AGV设备号")
    payLoad: str = Field(default="", description="负载重量")
    thirdOrderId: str = Field(default="", description="第三方订单号")
    entryPoint: str = Field(default="", description="入口节点")


class RcsDoorControlResponse(BaseModel):
    """RCS → ASAP: 门禁控制响应"""
    code: int = 1000
    msg: str = "success"
    data: Any = None


class RcsStatusQueryRequest(BaseModel):
    """RCS → ASAP: 门状态查询请求"""
    doorCode: str = ""


class RcsStatusData(BaseModel):
    """门状态数据"""
    status: int = 0    # 0=离线, 1=开门, 2=关门


class RcsStatusQueryResponse(BaseModel):
    """RCS → ASAP: 门状态查询响应"""
    code: int = 1000
    msg: str = "success"
    data: Optional[RcsStatusData] = None


class RcsChangeStatusRequest(BaseModel):
    """ASAP → RCS: 门状态主动上报"""
    doorNum: str = ""
    doorStatus: str = ""    # "1"=开门, "2"=关门


class RcsChangeStatusResponse(BaseModel):
    """ASAP → RCS: 门状态主动上报响应"""
    code: int = 1000
    msg: str = "success"
    data: Any = None


# ═══════════════════════════════════════════
#  ASAP 内部状态模型
# ═══════════════════════════════════════════

class AirShowerState(str, Enum):
    """风淋状态机状态"""
    IDLE = "IDLE"
    REQUEST_ZONE = "REQUEST_ZONE"
    OPEN_OUTER_DOOR = "OPEN_OUTER_DOOR"
    WAIT_OUTER_DOOR_OPEN = "WAIT_OUTER_DOOR_OPEN"
    AGV_ENTERING = "AGV_ENTERING"
    CLOSE_OUTER_DOOR = "CLOSE_OUTER_DOOR"
    WAIT_OUTER_DOOR_CLOSE = "WAIT_OUTER_DOOR_CLOSE"
    SHOWERING = "SHOWERING"
    OPEN_INNER_DOOR = "OPEN_INNER_DOOR"
    WAIT_INNER_DOOR_OPEN = "WAIT_INNER_DOOR_OPEN"
    AGV_EXITING = "AGV_EXITING"
    CLOSE_INNER_DOOR = "CLOSE_INNER_DOOR"
    WAIT_INNER_DOOR_CLOSE = "WAIT_INNER_DOOR_CLOSE"
    RELEASE_ZONE = "RELEASE_ZONE"
    ERROR = "ERROR"


class DoorState(BaseModel):
    """门实时状态"""
    door_id: str = ""
    door_code: str = ""
    door_status: AngelDoorStatus = AngelDoorStatus.UNKNOWN
    command: str = "0"
    last_updated: str = ""
    error: str = ""


class ZoneState(BaseModel):
    """区域实时状态"""
    zone_id: str = ""
    status: str = "unknown"
    occupied_by: str = ""
    permission_id: str = ""
    last_check: str = ""


class StepLog(BaseModel):
    """单步执行日志（含请求/响应报文）"""
    step: int = 0
    step_name: str = ""
    action: str = ""          # e.g. "open_outer_door", "close_inner_door"
    direction: str = ""       # "send"=请求, "recv"=响应
    url: str = ""             # 请求 URL
    payload: dict = Field(default_factory=dict)  # 请求体或响应体
    timestamp: str = ""
    success: bool = True


class AirShowerStatus(BaseModel):
    """风淋整体状态（WebUI 使用）"""
    state: AirShowerState = AirShowerState.IDLE
    outer_door: DoorState = Field(default_factory=DoorState)
    inner_door: DoorState = Field(default_factory=DoorState)
    zone: ZoneState = Field(default_factory=ZoneState)
    current_agv: str = ""
    elapsed: float = 0.0
    total_steps: int = 14
    current_step: int = 0
    shower_duration: float = 4.0
    started_at: Optional[str] = None
    error_message: str = ""
    last_event: str = ""
    rcs_query_count: int = 0
    rcs_last_query: str = ""
    step_log: list = Field(default_factory=list)  # 每步报文日志


class StateEvent(BaseModel):
    """状态变更事件（用于 SSE 推送）"""
    timestamp: str
    event_type: str  # "state_change" | "door_change" | "log"
    data: dict
