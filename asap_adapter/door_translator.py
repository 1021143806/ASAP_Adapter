"""
风淋门协议翻译器

实现 RCS ↔ Angel ACS 的纯协议转换层，无状态机、无区域管理、无风淋计时。
唯一额外逻辑：Direction 状态管理（先开门判定，持续到两门全关才重置）。

参考文档: doc/风淋门逻辑梳理.md
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .config import AppConfig
from .door_client import DoorClient, DoorClientError
from .models import AngelDoorStatus

logger = logging.getLogger(__name__)

# ── 状态翻译 ──────────────────────────────────

def _acs_to_rcs_status(door_status: str) -> int:
    """ACS doorStatus → RCS status
    ACS: "0"=已关闭, "1"=完全打开, "2"=故障
    RCS: 0=离线, 1=开门, 2=关门
    """
    if door_status == AngelDoorStatus.OPENED.value:
        return 1   # 开门
    elif door_status == AngelDoorStatus.CLOSED.value:
        return 2   # 关门
    else:
        return 0   # 故障/未知 → 离线


@dataclass
class DoorSnapshot:
    """单扇门状态快照（供 WebUI/SSE 使用）"""
    door_id: str = ""           # ACS 门编号 (如 DOOR01)
    door_code: str = ""         # RCS 门编号 (如 1001)
    acs_status: str = "0"       # ACS doorStatus: "0"/"1"/"2"
    rcs_status: int = 2         # 翻译后的 RCS status: 0/1/2
    last_updated: str = ""
    error: str = ""


@dataclass
class AirShowerStatus:
    """风淋门翻译层整体状态（替换旧 AirShowerStatus）"""
    door1: DoorSnapshot = field(default_factory=DoorSnapshot)  # 门1 (1001↔DOOR01)
    door2: DoorSnapshot = field(default_factory=DoorSnapshot)  # 门2 (1002↔DOOR02)
    direction: str = ""          # 当前 Direction: "1"=进, "2"=出, ""=未判定
    rcs_query_count: int = 0
    rcs_last_query: str = ""
    step_log: list = field(default_factory=list)
    started_at: str = ""

    def dump(self) -> dict:
        return {
            "door1": {
                "door_id": self.door1.door_id,
                "door_code": self.door1.door_code,
                "acs_status": self.door1.acs_status,
                "rcs_status": self.door1.rcs_status,
                "last_updated": self.door1.last_updated,
                "error": self.door1.error,
            },
            "door2": {
                "door_id": self.door2.door_id,
                "door_code": self.door2.door_code,
                "acs_status": self.door2.acs_status,
                "rcs_status": self.door2.rcs_status,
                "last_updated": self.door2.last_updated,
                "error": self.door2.error,
            },
            "direction": self.direction,
            "direction_label": {"": "未判定", "1": "进", "2": "出"}.get(self.direction, self.direction),
            "rcs_query_count": self.rcs_query_count,
            "rcs_last_query": self.rcs_last_query,
            "started_at": self.started_at,
            "step_log": self.step_log,
        }


class AirShowerTranslator:
    """
    风淋门协议翻译器

    核心原则: RCS 发什么 → 翻译 → 发到 ACS → 收到响应 → 翻译 → 回给 RCS
    """

    def __init__(self, config: AppConfig, door_client: DoorClient):
        self.config = config
        self.door = door_client

        # Direction 状态：两门全关时重置为 None，先开哪扇门决定 Direction
        self._current_direction: Optional[str] = None

        # 状态快照
        self._status = AirShowerStatus()
        self._status.started_at = datetime.now().isoformat()

        # 门 ↔ 编码映射
        self._refresh_mapping()

    # ── 门编码映射 ──────────────────────────────

    def _refresh_mapping(self):
        """从配置刷新门编码映射"""
        mapping = self.config.rcs.door_code_mapping
        # mapping: {"DOOR01": "1001", "DOOR02": "1002"}
        for door_id, door_code in mapping.items():
            if door_code == list(mapping.values())[0] if len(mapping) > 0 else "1001":
                self._status.door1.door_id = door_id
                self._status.door1.door_code = door_code
            else:
                self._status.door2.door_id = door_id
                self._status.door2.door_code = door_code

    def _door_id_by_code(self, door_code: str) -> Optional[str]:
        """RCS doorCode → ACS doorId"""
        mapping = self.config.rcs.door_code_mapping
        for door_id, code in mapping.items():
            if code == door_code:
                return door_id
        logger.warning("未找到 doorCode[%s] 的映射", door_code)
        return None

    def _door_code_by_id(self, door_id: str) -> str:
        """ACS doorId → RCS doorCode"""
        return self.config.rcs.door_code_mapping.get(door_id, "")

    def _snapshot_by_door_id(self, door_id: str) -> Optional[DoorSnapshot]:
        """根据 door_id 获取对应的状态快照"""
        if door_id == self._status.door1.door_id:
            return self._status.door1
        if door_id == self._status.door2.door_id:
            return self._status.door2
        return None

    # ── Direction 判定逻辑 ──────────────────────

    def _determine_direction(self, door_code: str) -> str:
        """
        根据"先开门判定"规则确定 Direction
        规则: 1001先开 → "2"(出), 1002先开 → "1"(进)
        Direction 持续到两门都关后才重置
        """
        if self._current_direction is not None:
            return self._current_direction

        # 检查两门是否都关
        both_closed = (
            self._status.door1.acs_status in ("0", "")
            and self._status.door2.acs_status in ("0", "")
        )
        if not both_closed:
            # 有门开着但 Direction 未设置（异常恢复场景），使用当前状态推测
            if self._status.door1.acs_status == "1":
                # 门1开着 → 是出
                self._current_direction = "2"
                return "2"
            if self._status.door2.acs_status == "1":
                self._current_direction = "1"
                return "1"

        # 根据文档规则：先开哪扇门决定 Direction
        door1_code = self._status.door1.door_code
        door2_code = self._status.door2.door_code

        if door_code == door1_code:
            direction = "2"  # 1001 → 出
        elif door_code == door2_code:
            direction = "1"  # 1002 → 进
        else:
            direction = "1"  # 未知，默认进

        self._current_direction = direction
        logger.info("Direction 判定: doorCode=%s → Direction=%s (%s)",
                     door_code, direction, "进" if direction == "1" else "出")
        return direction

    def _reset_direction_if_both_closed(self):
        """如果两门都关，重置 Direction"""
        if (self._status.door1.acs_status in ("0", "")
                and self._status.door2.acs_status in ("0", "")):
            if self._current_direction is not None:
                logger.info("两门已全关，重置 Direction (原值: %s)", self._current_direction)
                self._current_direction = None

    # ── RCS 控制接口 ────────────────────────────

    async def handle_control(self, door_code: str, status: int,
                              device_code: str = "") -> tuple:
        """
        处理 RCS 控制请求（阻塞式）

        Args:
            door_code: RCS 门编号 (1001/1002)
            status: 1=开门, 2=关门
            device_code: AGV 编号

        Returns:
            (code: int, msg: str)  RCS 响应码和消息
        """
        door_id = self._door_id_by_code(door_code)
        if door_id is None:
            return 2001, f"未知门编号: {door_code}"

        self._status.rcs_query_count += 1
        action_label = "开门" if status == 1 else "关门"
        self._status.rcs_last_query = (
            f"{datetime.now().strftime('%H:%M:%S')} "
            f"控制 doorCode={door_code} {action_label}"
        )
        logger.info("RCS控制: door=%s(%s) status=%d agv=%s",
                     door_code, door_id, status, device_code)

        # 更新本地快照
        snap = self._snapshot_by_door_id(door_id)

        if status == 1:
            return await self._handle_open(door_id, door_code, device_code, snap)
        elif status == 2:
            return await self._handle_close(door_id, door_code, snap)
        else:
            return 2003, f"未知状态: {status}"

    async def _handle_open(self, door_id: str, door_code: str,
                            device_code: str, snap: Optional[DoorSnapshot]) -> tuple:
        """处理开门：POST open + 轮询直到 doorStatus='1'"""
        direction = self._determine_direction(door_code)

        try:
            # 1. 发送开门指令
            self._log_step("open", "send",
                           f"POST /acs/door/{door_id}",
                           {"doorSerial": door_id, "command": "1",
                            "Direction": direction, "RobotName": device_code})
            resp = await self.door.open_door(door_id, direction=direction,
                                              robot_name=device_code)
            self._log_step("open", "recv",
                           f"POST /acs/door/{door_id}",
                           resp.model_dump())

            # 更新状态
            if snap:
                snap.acs_status = resp.doorStatus
                snap.rcs_status = _acs_to_rcs_status(resp.doorStatus)
                snap.last_updated = datetime.now().isoformat()
                snap.error = ""

            # 2. 如果未完全打开，轮询等待
            if resp.doorStatus != AngelDoorStatus.OPENED.value:
                if resp.doorStatus == AngelDoorStatus.FAULT.value:
                    self._publish()
                    return 9999, "door fault"
                if resp.code == "500":
                    self._publish()
                    return 9999, "door error"

                logger.info("等待门[%s]打开...", door_id)
                try:
                    status_resp = await self.door.wait_for_open(door_id)
                    if snap:
                        snap.acs_status = status_resp.doorStatus
                        snap.rcs_status = _acs_to_rcs_status(status_resp.doorStatus)
                        snap.last_updated = datetime.now().isoformat()
                except DoorClientError as e:
                    self._publish()
                    return 9999, f"timeout"

            self._publish()
            return 1000, "success"

        except DoorClientError as e:
            logger.error("开门失败[%s]: %s", door_id, e)
            if snap:
                snap.error = str(e)
            self._publish()
            return 9999, f"door error: {e}"

    async def _handle_close(self, door_id: str, door_code: str,
                             snap: Optional[DoorSnapshot]) -> tuple:
        """处理关门：POST close + 轮询直到 doorStatus='0'"""
        direction = self._current_direction or "1"

        try:
            # 1. 发送关门指令
            self._log_step("close", "send",
                           f"POST /acs/door/{door_id}",
                           {"doorSerial": door_id, "command": "2",
                            "Direction": direction})
            resp = await self.door.close_door(door_id)
            self._log_step("close", "recv",
                           f"POST /acs/door/{door_id}",
                           resp.model_dump())

            # 更新状态
            if snap:
                snap.acs_status = resp.doorStatus
                snap.rcs_status = _acs_to_rcs_status(resp.doorStatus)
                snap.last_updated = datetime.now().isoformat()
                snap.error = ""

            # 2. 如果未完全关闭，轮询等待
            if resp.doorStatus != AngelDoorStatus.CLOSED.value:
                if resp.doorStatus == AngelDoorStatus.FAULT.value:
                    self._publish()
                    return 9999, "door fault"
                if resp.code == "500":
                    self._publish()
                    return 9999, "door error"

                logger.info("等待门[%s]关闭...", door_id)
                try:
                    status_resp = await self.door.wait_for_close(door_id)
                    if snap:
                        snap.acs_status = status_resp.doorStatus
                        snap.rcs_status = _acs_to_rcs_status(status_resp.doorStatus)
                        snap.last_updated = datetime.now().isoformat()
                except DoorClientError as e:
                    self._publish()
                    return 9999, f"timeout"

            # 3. 重置 Direction（如果两门都关了）
            self._reset_direction_if_both_closed()

            self._publish()
            return 1000, "success"

        except DoorClientError as e:
            logger.error("关门失败[%s]: %s", door_id, e)
            if snap:
                snap.error = str(e)
            self._publish()
            return 9999, f"door error: {e}"

    # ── RCS 状态查询 ────────────────────────────

    async def handle_query(self, door_code: str) -> tuple:
        """
        处理 RCS 状态查询（非阻塞）

        Returns:
            (code: int, msg: str, data: dict or None)
        """
        door_id = self._door_id_by_code(door_code)
        if door_id is None:
            return 9999, f"未知门编号: {door_code}", None

        self._status.rcs_query_count += 1
        self._status.rcs_last_query = (
            f"{datetime.now().strftime('%H:%M:%S')} "
            f"doorCode={door_code}"
        )

        try:
            status_resp = await self.door.get_status(door_id)
            rcs_status = _acs_to_rcs_status(status_resp.doorStatus)

            # 更新快照
            snap = self._snapshot_by_door_id(door_id)
            if snap:
                snap.acs_status = status_resp.doorStatus
                snap.rcs_status = rcs_status
                snap.last_updated = datetime.now().isoformat()
                snap.error = ""

            self._publish()
            return 1000, "success", {"status": rcs_status}

        except DoorClientError as e:
            logger.error("查询失败[%s]: %s", door_id, e)
            # 查询失败返回离线
            snap = self._snapshot_by_door_id(door_id)
            if snap:
                snap.error = str(e)
            self._publish()
            return 9999, str(e), None

    # ── 手动控制 ────────────────────────────────

    async def manual_open(self, door_id: str):
        """手动开门（独立于 Direction 判定）"""
        resp = await self.door.open_door(door_id, direction="1")
        snap = self._snapshot_by_door_id(door_id)
        if snap:
            snap.acs_status = resp.doorStatus
            snap.rcs_status = _acs_to_rcs_status(resp.doorStatus)
            snap.last_updated = datetime.now().isoformat()
        self._publish()

    async def manual_close(self, door_id: str):
        """手动关门"""
        resp = await self.door.close_door(door_id)
        snap = self._snapshot_by_door_id(door_id)
        if snap:
            snap.acs_status = resp.doorStatus
            snap.rcs_status = _acs_to_rcs_status(resp.doorStatus)
            snap.last_updated = datetime.now().isoformat()
        self._reset_direction_if_both_closed()
        self._publish()

    # ── 状态快照 ────────────────────────────────

    def get_status(self) -> AirShowerStatus:
        """获取整体状态快照（供 WebUI/SSE）"""
        return self._status

    def get_door_states(self) -> dict:
        """获取双门状态"""
        return {
            "door1": {
                "door_id": self._status.door1.door_id,
                "door_code": self._status.door1.door_code,
                "acs_status": self._status.door1.acs_status,
                "rcs_status": self._status.door1.rcs_status,
            },
            "door2": {
                "door_id": self._status.door2.door_id,
                "door_code": self._status.door2.door_code,
                "acs_status": self._status.door2.acs_status,
                "rcs_status": self._status.door2.rcs_status,
            },
            "direction": self._current_direction,
            "direction_label": {"": "未判定", "1": "进", "2": "出"}.get(
                self._current_direction or "", self._current_direction or ""),
        }

    # ── 取消 ────────────────────────────────────

    async def cancel(self):
        """取消操作（无任务时为空操作）"""
        pass

    # ── 内部辅助 ────────────────────────────────

    def _log_step(self, action: str, direction: str, url: str, payload: dict):
        """记录报文日志"""
        entry = {
            "action": action,
            "direction": direction,
            "url": url,
            "payload": payload,
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:12],
        }
        self._status.step_log.append(entry)
        if len(self._status.step_log) > 30:
            self._status.step_log = self._status.step_log[-30:]

    def _publish(self):
        """(已废弃) SSE 事件发布 — v3.3 改用轮询 /api/asap/logs"""
        pass
