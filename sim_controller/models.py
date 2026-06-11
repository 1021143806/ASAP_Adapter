"""
模拟器数据模型
"""

from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class DoorSimState(str, Enum):
    """门模拟状态"""
    CLOSED = "0"           # 门已关闭
    OPENING = "opening"    # 正在打开（过渡）
    OPENED = "1"           # 门完全打开
    CLOSING = "closing"    # 正在关闭（过渡）
    FAULT = "2"            # 故障


class ZoneSimState(str, Enum):
    """区域模拟状态"""
    AVAILABLE = "available"
    OCCUPIED = "occupied"


class SimDoor(BaseModel):
    """模拟的门状态"""
    door_id: str
    state: DoorSimState = DoorSimState.CLOSED
    command: str = "0"         # 最后一次接收的 command
    direction: str = ""        # 方向
    robot_name: str = ""       # AGV 编号
    code: str = "200"          # 响应码
    last_command_time: str = ""
    open_delay: float = 2.0    # 开门过渡时间 (秒)
    close_delay: float = 2.0   # 关门过渡时间 (秒)


class SimZone(BaseModel):
    """模拟的区域状态"""
    zone_id: str
    state: ZoneSimState = ZoneSimState.AVAILABLE
    occupied_by: str = ""
    permission_id: str = ""
    enter_delay: float = 0.0   # 进入延迟
    exit_delay: float = 0.0    # 退出延迟


class SimConfig(BaseModel):
    """模拟配置"""
    auto_open_delay: float = 2.0    # 门自动完成打开过渡的时间
    auto_close_delay: float = 2.0   # 门自动完成关闭过渡的时间
    zone_always_busy: bool = False  # 区域始终返回被占用
    inject_fault: bool = False      # 注入故障
    fault_door_id: str = ""         # 故障门 ID
    fault_code: str = "500"         # 故障响应码


class SimSnapshot(BaseModel):
    """模拟器状态快照 (WebUI)"""
    outer_door: SimDoor
    inner_door: SimDoor
    zone: SimZone
    config: SimConfig
    request_log: list = []
    timestamp: str = ""


# ── Angel Protocol Models ──────────────────

class AngelControlRequest(BaseModel):
    doorSerial: str
    command: str
    Direction: Optional[str] = None
    RobotName: Optional[str] = None


class AngelControlResponse(BaseModel):
    doorSerial: str = ""
    doorStatus: str = "0"
    command: str = "0"
    code: str = "200"


class AngelStatusResponse(BaseModel):
    doorSerial: str = ""
    doorStatus: str = "0"
    command: str = "0"
    code: str = "200"


# ── Zone Protocol Models ───────────────────

class ZoneEnterRequest(BaseModel):
    zone_id: str
    client_id: str


class ZoneEnterResponse(BaseModel):
    permission_id: str = ""
    zone_id: str = ""
    client_id: str = ""
    status: str = ""


class ZoneEnterConflict(BaseModel):
    error: str = ""
    occupied_by: str = ""


class ZoneExitRequest(BaseModel):
    zone_id: str
    client_id: str


class ZoneExitResponse(BaseModel):
    zone_id: str = ""
    client_id: str = ""
    status: str = ""  # "released"


class ZoneStatusResponse(BaseModel):
    zone_id: str = ""
    status: str = ""  # "occupied" | "available"
    occupied_by: str = ""
