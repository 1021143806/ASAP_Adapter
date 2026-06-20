"""
模拟器核心状态管理

管理门状态、区域状态的模拟逻辑，包括：
  - 开门/关门过渡动画（延时模拟）
  - 故障注入
  - 区域占用/释放
  - 请求日志
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from .models import (
    DoorSimState, ZoneSimState, SimDoor, SimZone,
    SimConfig, SimSnapshot,
)

logger = logging.getLogger(__name__)


class SimController:
    """模拟控制器核心"""

    def __init__(self, outer_door_id: str = "DOOR_OUTER", inner_door_id: str = "DOOR_INNER"):
        self.outer_door = SimDoor(door_id=outer_door_id)
        self.inner_door = SimDoor(door_id=inner_door_id)
        self.zone = SimZone(zone_id="zone_001")
        self.config = SimConfig()
        self.request_log: list[dict] = []

        # 后台过渡任务
        self._transition_task: Optional[asyncio.Task] = None

    # ── 状态快照 ──────────────────────────────

    def snapshot(self) -> SimSnapshot:
        """获取当前状态快照"""
        return SimSnapshot(
            outer_door=self.outer_door,
            inner_door=self.inner_door,
            zone=self.zone,
            config=self.config,
            request_log=self.request_log[-50:],  # 最近50条
            timestamp=datetime.now().isoformat(),
        )

    # ── 门控制 ────────────────────────────────

    def control_door(self, door_id: str, command: str,
                     direction: str = "", robot_name: str = "") -> dict:
        """
        控制门
        返回 Angel 协议格式的响应
        """
        door = self._get_door(door_id)
        if door is None:
            return {"doorSerial": door_id, "doorStatus": "-1",
                    "command": command, "code": "500"}

        # 故障注入
        if self.config.inject_fault and self.config.fault_door_id == door_id:
            self._log("control", door_id, f"故障模式拒绝指令: command={command}",
                      method="POST", path=f"/acs/door/{door_id}")
            return {"doorSerial": door_id, "doorStatus": "2",
                    "command": command, "code": self.config.fault_code}

        door.last_command_time = datetime.now().isoformat()
        door.direction = direction
        door.robot_name = robot_name

        if command == "1":  # 开门
            if door.state == DoorSimState.OPENED:
                self._log("control", door_id, "开门指令: 已处于打开状态",
                          method="POST", path=f"/acs/door/{door_id}")
            else:
                door.state = DoorSimState.OPENING
                door.command = "1"
                self._log("control", door_id, f"开始开门 (delay={door.open_delay}s)",
                          method="POST", path=f"/acs/door/{door_id}")
                self._schedule_transition(door_id, DoorSimState.OPENED, door.open_delay)

        elif command == "2":  # 关门
            if door.state == DoorSimState.CLOSED:
                self._log("control", door_id, "关门指令: 已处于关闭状态",
                          method="POST", path=f"/acs/door/{door_id}")
            else:
                door.state = DoorSimState.CLOSING
                door.command = "2"
                self._log("control", door_id, f"开始关门 (delay={door.close_delay}s)",
                          method="POST", path=f"/acs/door/{door_id}")
                self._schedule_transition(door_id, DoorSimState.CLOSED, door.close_delay)

        else:
            self._log("control", door_id, f"无动作指令: command={command}",
                      method="POST", path=f"/acs/door/{door_id}")

        return self._build_angel_response(door, command)

    def query_door(self, door_id: str) -> dict:
        """查询门状态"""
        door = self._get_door(door_id)
        if door is None:
            return {"doorSerial": door_id, "doorStatus": "-1",
                    "command": "0", "code": "500"}

        self._log("query", door_id, f"状态查询: {door.state.value}",
                  method="GET", path=f"/acs/door/{door_id}")
        return self._build_angel_response(door, door.command)

    def _get_door(self, door_id: str) -> Optional[SimDoor]:
        if door_id == self.outer_door.door_id:
            return self.outer_door
        elif door_id == self.inner_door.door_id:
            return self.inner_door
        return None

    def _build_angel_response(self, door: SimDoor, command: str) -> dict:
        """构建 Angel 协议响应"""
        return {
            "doorSerial": door.door_id,
            "doorStatus": door.state.value if door.state in
                          (DoorSimState.OPENED, DoorSimState.CLOSED, DoorSimState.FAULT)
                          else door.state.value,
            "command": command,
            "code": door.code,
        }

    # ── 过渡模拟 ──────────────────────────────

    def _schedule_transition(self, door_id: str, target: DoorSimState, delay: float):
        """安排门状态过渡（延迟后自动切换到目标状态）"""
        if self._transition_task and not self._transition_task.done():
            self._transition_task.cancel()
        self._transition_task = asyncio.ensure_future(
            self._do_transition(door_id, target, delay)
        )

    async def _do_transition(self, door_id: str, target: DoorSimState, delay: float):
        """执行门状态过渡"""
        try:
            await asyncio.sleep(delay)
            door = self._get_door(door_id)
            if door is None:
                return

            if target == DoorSimState.OPENED:
                door.state = DoorSimState.OPENED
                door.code = "200"
                self._log("transition", door_id, "门已完全打开 ✓")
            elif target == DoorSimState.CLOSED:
                door.state = DoorSimState.CLOSED
                door.code = "200"
                door.command = "0"
                self._log("transition", door_id, "门已完全关闭 ✓")

        except asyncio.CancelledError:
            pass

    # ── 区域控制 ──────────────────────────────

    def zone_enter(self, zone_id: str, client_id: str) -> tuple[dict, int]:
        """
        请求进入区域
        返回 (响应体, HTTP状态码)
        """
        if self.config.zone_always_busy:
            self._log("zone", zone_id, f"始终占用模式: 拒绝 {client_id}")
            return ({"error": "Zone is currently occupied", "occupied_by": "sim_force"}, 409)

        if self.zone.state == ZoneSimState.OCCUPIED:
            self._log("zone", zone_id, f"被占用: {client_id} 被拒绝 (占用者: {self.zone.occupied_by})")
            return ({"error": "Zone is currently occupied",
                     "occupied_by": self.zone.occupied_by}, 409)

        # 进入
        perm_id = f"perm_{uuid.uuid4().hex[:12]}"
        self.zone.state = ZoneSimState.OCCUPIED
        self.zone.occupied_by = client_id
        self.zone.permission_id = perm_id

        self._log("zone", zone_id, f"授权进入: {client_id} (perm={perm_id})")
        return ({
            "permission_id": perm_id,
            "zone_id": zone_id,
            "client_id": client_id,
            "status": "granted",
        }, 200)

    def zone_exit(self, zone_id: str, client_id: str) -> tuple[dict, int]:
        """退出区域"""
        if self.zone.state == ZoneSimState.OCCUPIED and self.zone.occupied_by == client_id:
            self.zone.state = ZoneSimState.AVAILABLE
            self.zone.occupied_by = ""
            self.zone.permission_id = ""
            self._log("zone", zone_id, f"释放: {client_id}")
            return ({"zone_id": zone_id, "client_id": client_id, "status": "released"}, 200)
        else:
            self._log("zone", zone_id, f"释放失败: {client_id} 未占用此区域")
            return ({"zone_id": zone_id, "client_id": client_id, "status": "not_occupied"}, 200)

    def zone_status(self, zone_id: str) -> dict:
        """查询区域状态"""
        occupied = self.zone.state == ZoneSimState.OCCUPIED
        return {
            "zone_id": zone_id,
            "status": "occupied" if occupied else "available",
            "occupied_by": self.zone.occupied_by if occupied else "",
        }

    # ── 手动控制 (WebUI) ─────────────────────

    def manual_set_door_state(self, door_id: str, state: str):
        """手动强制设置门状态"""
        door = self._get_door(door_id)
        if door is None:
            return False
        try:
            door.state = DoorSimState(state)
            door.code = "200"
            self._log("manual", door_id, f"强制设为: {state}")
            return True
        except ValueError:
            return False

    def manual_inject_fault(self, door_id: str, enable: bool):
        """注入/清除门故障"""
        self.config.inject_fault = enable
        self.config.fault_door_id = door_id if enable else ""
        if enable:
            door = self._get_door(door_id)
            if door:
                door.state = DoorSimState.FAULT
                door.code = "500"
            self._log("fault", door_id, "⚠ 注入故障")
        else:
            door = self._get_door(door_id)
            if door:
                door.state = DoorSimState.CLOSED
                door.code = "200"
            self._log("fault", door_id, "清除故障")

    def manual_set_zone_busy(self, busy: bool, client: str = ""):
        """强制设置区域占用"""
        self.config.zone_always_busy = busy
        if busy:
            self.zone.state = ZoneSimState.OCCUPIED
            self.zone.occupied_by = client or "force_occupy"
        else:
            self.zone.state = ZoneSimState.AVAILABLE
            self.zone.occupied_by = ""
        self._log("zone", self.zone.zone_id, f"强制{'占用' if busy else '释放'}")

    def manual_set_delays(self, open_delay: float, close_delay: float):
        """设置门过渡延时"""
        self.outer_door.open_delay = open_delay
        self.outer_door.close_delay = close_delay
        self.inner_door.open_delay = open_delay
        self.inner_door.close_delay = close_delay
        self._log("config", "", f"延迟设置: 开={open_delay}s 关={close_delay}s")

    def set_door_ids(self, outer_id: str, inner_id: str):
        """更新门ID（支持热更新）"""
        changed = False
        if outer_id and outer_id != self.outer_door.door_id:
            self.outer_door = SimDoor(door_id=outer_id)
            changed = True
        if inner_id and inner_id != self.inner_door.door_id:
            self.inner_door = SimDoor(door_id=inner_id)
            changed = True
        if changed:
            self._log("config", "", f"门ID更新: 外={outer_id} 内={inner_id}")

    def reset_all(self):
        """重置所有状态"""
        outer_id = self.outer_door.door_id
        inner_id = self.inner_door.door_id
        self.outer_door = SimDoor(door_id=outer_id)
        self.inner_door = SimDoor(door_id=inner_id)
        self.zone = SimZone(zone_id="air_shower_room")
        self.config = SimConfig()
        self.request_log.clear()
        self._log("system", "", "全部重置")

    # ── 日志 ──────────────────────────────────

    _log_counter = 0

    def _log(self, category: str, target: str, message: str,
             req_body: dict = None, resp_body: dict = None,
             method: str = "", path: str = ""):
        """记录请求日志，含完整报文"""
        self._log_counter += 1
        entry = {
            "id": self._log_counter,
            "time": datetime.now().strftime("%H:%M:%S.%f")[:12],
            "category": category,
            "target": target,
            "message": message,
            "request": req_body,
            "response": resp_body,
            "method": method,
            "path": path,
        }
        self.request_log.append(entry)
        logger.debug("[%s] %s %s: %s", category, target, message)
