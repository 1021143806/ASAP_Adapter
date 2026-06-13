"""
风淋流程状态机

核心编排逻辑，管理从 IDLE 到风淋完成并释放区域的完整流程。
包含错误处理、超时重试、状态发布（供 WebUI 和 SSE 消费）。
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional, Callable, Awaitable

from .config import AppConfig
from .models import (
    AirShowerState, AirShowerStatus,
    DoorState, ZoneState,
    AngelDoorStatus,
    StateEvent,
)
from .door_client import DoorClient, DoorClientError
from .zone_client import ZoneClient, ZoneClientError
from .rcs_reporter import RcsReporter

logger = logging.getLogger(__name__)

# 状态机步骤编号映射
STATE_STEP_MAP = {
    AirShowerState.IDLE: 0,
    AirShowerState.REQUEST_ZONE: 1,
    AirShowerState.OPEN_OUTER_DOOR: 2,
    AirShowerState.WAIT_OUTER_DOOR_OPEN: 3,
    AirShowerState.AGV_ENTERING: 4,
    AirShowerState.CLOSE_OUTER_DOOR: 5,
    AirShowerState.WAIT_OUTER_DOOR_CLOSE: 6,
    AirShowerState.SHOWERING: 7,
    AirShowerState.OPEN_INNER_DOOR: 8,
    AirShowerState.WAIT_INNER_DOOR_OPEN: 9,
    AirShowerState.AGV_EXITING: 10,
    AirShowerState.CLOSE_INNER_DOOR: 11,
    AirShowerState.WAIT_INNER_DOOR_CLOSE: 12,
    AirShowerState.RELEASE_ZONE: 13,
    AirShowerState.ERROR: -1,
}


class StateMachine:
    """风淋流程状态机"""

    def __init__(
        self,
        config: AppConfig,
        door_client: DoorClient,
        zone_client: ZoneClient,
        rcs_reporter: RcsReporter,
    ):
        self.config = config
        self.door = door_client
        self.zone = zone_client
        self.rcs = rcs_reporter

        # 状态
        self._state: AirShowerState = AirShowerState.IDLE
        self._status = AirShowerStatus()
        self._task: Optional[asyncio.Task] = None
        self._cancel_event = asyncio.Event()

        # 回调：状态变更通知（用于 SSE 推送）
        self.on_event: Optional[Callable[[StateEvent], Awaitable[None]]] = None

        # 当前AGV编号
        self._current_agv: str = ""

    # ── 属性 ──────────────────────────────────

    @property
    def state(self) -> AirShowerState:
        return self._state

    @property
    def status(self) -> AirShowerStatus:
        """获取当前状态快照"""
        self._status.state = self._state
        self._status.current_step = STATE_STEP_MAP.get(self._state, 0)
        # 填充门编码（来自 RCS door_code_mapping）
        mapping = self.config.rcs.door_code_mapping
        self._status.outer_door.door_code = mapping.get("DOOR_OUTER", "")
        self._status.inner_door.door_code = mapping.get("DOOR_INNER", "")
        return self._status

    @property
    def is_busy(self) -> bool:
        """是否有流程正在执行"""
        return self._state != AirShowerState.IDLE and self._state != AirShowerState.ERROR

    # ── 启动流程 ──────────────────────────────

    async def start(self, agv_id: str = "") -> bool:
        """
        启动风淋进入流程
        返回 True 表示成功启动，False 表示当前忙碌无法启动
        """
        if self.is_busy:
            logger.warning("状态机忙碌中，拒绝启动: state=%s", self._state.value)
            return False

        self._current_agv = agv_id
        self._cancel_event.clear()

        # 重置状态
        self._status = AirShowerStatus()
        self._status.current_agv = agv_id
        self._status.started_at = datetime.now().isoformat()

        # 启动后台任务
        self._task = asyncio.create_task(self._run())
        logger.info("风淋流程启动: agv=%s", agv_id or "unknown")
        return True

    # ── 取消流程 ──────────────────────────────

    async def cancel(self):
        """取消当前流程"""
        if self._task and not self._task.done():
            self._cancel_event.set()
            self._task.cancel()
            logger.info("风淋流程被取消")
        self._set_state(AirShowerState.IDLE)

    # ── 核心流程 ──────────────────────────────

    async def _run(self):
        """流程主循环"""
        try:
            await self._step_request_zone()
            await self._step_open_outer_door()
            await self._step_wait_outer_door_open()
            await self._step_agv_entering()
            await self._step_close_outer_door()
            await self._step_wait_outer_door_close()
            await self._step_showering()
            await self._step_open_inner_door()
            await self._step_wait_inner_door_open()
            await self._step_agv_exiting()
            await self._step_close_inner_door()
            await self._step_wait_inner_door_close()
            await self._step_release_zone()
            await self._finish()

        except asyncio.CancelledError:
            logger.info("风淋流程被取消")
            await self._cleanup()

        except (DoorClientError, ZoneClientError) as e:
            logger.error("风淋流程异常: %s", e)
            self._status.error_message = str(e)
            self._set_state(AirShowerState.ERROR)
            await self._cleanup()

        except Exception as e:
            logger.exception("风淋流程未预期异常:")
            self._status.error_message = f"未预期异常: {e}"
            self._set_state(AirShowerState.ERROR)
            await self._cleanup()

    # ── 各步骤实现 ────────────────────────────

    async def _step_request_zone(self):
        """步骤1: 请求区域"""
        self._set_state(AirShowerState.REQUEST_ZONE)
        logger.info("步骤1/13: 请求区域 [%s]", self.config.zone.zone_id)
        result = await self.zone.enter_with_retry()
        self._status.zone.permission_id = result.permission_id
        self._status.zone.status = "granted"
        self._publish_event("zone_granted", {
            "permission_id": result.permission_id,
        })

    async def _step_open_outer_door(self):
        """步骤2: 开外门"""
        self._set_state(AirShowerState.OPEN_OUTER_DOOR)
        door_id = self.config.angel.outer_door_id
        logger.info("步骤2/13: 开外门 [%s]", door_id)
        resp = await self.door.open_door(door_id, robot_name=self._current_agv)
        self._update_door_state(door_id, resp.doorStatus)
        self._publish_event("door_control", {
            "door_id": door_id, "action": "open",
        })

    async def _step_wait_outer_door_open(self):
        """步骤3: 等外门完全打开"""
        self._set_state(AirShowerState.WAIT_OUTER_DOOR_OPEN)
        door_id = self.config.angel.outer_door_id
        logger.info("步骤3/13: 等待外门全开 [%s]", door_id)
        status = await self.door.wait_for_open(door_id)
        self._update_door_state(door_id, status.doorStatus)
        self._publish_event("door_opened", {
            "door_id": door_id,
            "message": "外门已开，AGV可进入",
        })

    async def _step_agv_entering(self):
        """步骤4: 等待AGV进入"""
        self._set_state(AirShowerState.AGV_ENTERING)
        logger.info("步骤4/13: 等待AGV进入")
        # 上报RCS：外门已开
        await self.rcs.report_door_open(self.config.angel.outer_door_id)
        # 等待 AGV 进入
        await asyncio.sleep(self.config.air_shower.agv_enter_timeout)
        self._publish_event("agv_entered", {
            "agv": self._current_agv,
        })

    async def _step_close_outer_door(self):
        """步骤5: 关外门"""
        self._set_state(AirShowerState.CLOSE_OUTER_DOOR)
        door_id = self.config.angel.outer_door_id
        logger.info("步骤5/13: 关外门 [%s]", door_id)
        resp = await self.door.close_door(door_id)
        self._update_door_state(door_id, resp.doorStatus)
        self._publish_event("door_control", {
            "door_id": door_id, "action": "close",
        })

    async def _step_wait_outer_door_close(self):
        """步骤6: 等外门完全关闭"""
        self._set_state(AirShowerState.WAIT_OUTER_DOOR_CLOSE)
        door_id = self.config.angel.outer_door_id
        logger.info("步骤6/13: 等待外门关闭 [%s]", door_id)
        status = await self.door.wait_for_close(door_id)
        self._update_door_state(door_id, status.doorStatus)
        # 上报 RCS：外门已关
        await self.rcs.report_door_closed(door_id)
        self._publish_event("door_closed", {
            "door_id": door_id,
        })

    async def _step_showering(self):
        """步骤7: 风淋计时"""
        self._set_state(AirShowerState.SHOWERING)
        duration = self.config.air_shower.duration
        logger.info("步骤7/13: 风淋中 (%ds)", duration)
        # 分小段 sleep 以便可被取消
        elapsed = 0.0
        while elapsed < duration:
            self._status.elapsed = elapsed
            if self._cancel_event.is_set():
                raise asyncio.CancelledError()
            await asyncio.sleep(0.5)
            elapsed += 0.5
        self._status.elapsed = duration
        self._publish_event("shower_complete", {
            "duration": duration,
        })

    async def _step_open_inner_door(self):
        """步骤8: 开内门"""
        self._set_state(AirShowerState.OPEN_INNER_DOOR)
        door_id = self.config.angel.inner_door_id
        logger.info("步骤8/13: 开内门 [%s]", door_id)
        resp = await self.door.open_door(door_id, robot_name=self._current_agv)
        self._update_door_state(door_id, resp.doorStatus)

    async def _step_wait_inner_door_open(self):
        """步骤9: 等内门完全打开"""
        self._set_state(AirShowerState.WAIT_INNER_DOOR_OPEN)
        door_id = self.config.angel.inner_door_id
        logger.info("步骤9/13: 等待内门全开 [%s]", door_id)
        status = await self.door.wait_for_open(door_id)
        self._update_door_state(door_id, status.doorStatus)
        self._publish_event("door_opened", {
            "door_id": door_id,
            "message": "内门已开，AGV可驶离",
        })

    async def _step_agv_exiting(self):
        """步骤10: 等待AGV驶离"""
        self._set_state(AirShowerState.AGV_EXITING)
        logger.info("步骤10/13: 等待AGV驶离")
        # 上报 RCS：内门已开
        await self.rcs.report_door_open(self.config.angel.inner_door_id)
        # 等待 AGV 驶离
        await asyncio.sleep(self.config.air_shower.agv_exit_timeout)
        self._publish_event("agv_exited", {
            "agv": self._current_agv,
        })

    async def _step_close_inner_door(self):
        """步骤11: 关内门"""
        self._set_state(AirShowerState.CLOSE_INNER_DOOR)
        door_id = self.config.angel.inner_door_id
        logger.info("步骤11/13: 关内门 [%s]", door_id)
        resp = await self.door.close_door(door_id)
        self._update_door_state(door_id, resp.doorStatus)

    async def _step_wait_inner_door_close(self):
        """步骤12: 等内门关闭"""
        self._set_state(AirShowerState.WAIT_INNER_DOOR_CLOSE)
        door_id = self.config.angel.inner_door_id
        logger.info("步骤12/13: 等待内门关闭 [%s]", door_id)
        status = await self.door.wait_for_close(door_id)
        self._update_door_state(door_id, status.doorStatus)
        # 上报 RCS：内门已关
        await self.rcs.report_door_closed(door_id)

    async def _step_release_zone(self):
        """步骤13: 释放区域"""
        self._set_state(AirShowerState.RELEASE_ZONE)
        logger.info("步骤13/13: 释放区域 [%s]", self.config.zone.zone_id)
        await self.zone.exit_with_retry()
        self._status.zone.status = "released"
        self._publish_event("zone_released", {
            "zone_id": self.config.zone.zone_id,
        })

    async def _finish(self):
        """流程正常结束"""
        self._set_state(AirShowerState.IDLE)
        self._status.elapsed = 0.0
        self._status.current_agv = ""
        logger.info("风淋流程正常完成")
        self._publish_event("flow_complete", {})

    async def _cleanup(self):
        """异常/取消后的清理"""
        logger.info("开始清理...")
        try:
            # 如果持有区域权限，尝试释放
            if self.zone.is_occupied:
                logger.warning("清理: 释放区域")
                await self.zone.exit_with_retry()
        except Exception as e:
            logger.error("清理释放区域失败: %s", e)

        self._set_state(AirShowerState.IDLE)
        self._status.elapsed = 0.0
        self._status.current_agv = ""
        logger.info("清理完成")

    # ── 内部辅助 ──────────────────────────────

    def _set_state(self, new_state: AirShowerState):
        """设置状态并更新时间"""
        old_state = self._state
        self._state = new_state
        self._status.state = new_state
        self._status.current_step = STATE_STEP_MAP.get(new_state, 0)
        self._status.last_event = f"{old_state.value} → {new_state.value}"
        logger.info("状态变更: %s → %s", old_state.value, new_state.value)

    def _update_door_state(self, door_id: str, door_status_str: str):
        """更新门状态缓存"""
        try:
            door_status = AngelDoorStatus(door_status_str)
        except ValueError:
            door_status = AngelDoorStatus.UNKNOWN

        door_state = DoorState(
            door_id=door_id,
            door_status=door_status,
            last_updated=datetime.now().isoformat(),
        )

        if door_id == self.config.angel.outer_door_id:
            self._status.outer_door = door_state
        elif door_id == self.config.angel.inner_door_id:
            self._status.inner_door = door_state

    def _publish_event(self, event_type: str, data: dict):
        """发布事件（触发 SSE 回调）"""
        if self.on_event:
            event = StateEvent(
                timestamp=datetime.now().isoformat(),
                event_type=event_type,
                data=data,
            )
            asyncio.ensure_future(self._safe_publish(event))

    async def _safe_publish(self, event: StateEvent):
        """安全发布事件"""
        try:
            await self.on_event(event)
        except Exception as e:
            logger.warning("事件发布失败: %s", e)

    # ── 手动控制（独立于流程） ─────────────────

    async def manual_open_door(self, door_id: str):
        """手动开门"""
        if self.is_busy:
            raise DoorClientError("流程执行中，不允许手动操作")
        resp = await self.door.open_door(door_id)
        self._update_door_state(door_id, resp.doorStatus)
        await self.rcs.report_door_open(door_id)
        logger.info("手动开门: %s", door_id)

    async def manual_close_door(self, door_id: str):
        """手动关门"""
        if self.is_busy:
            raise DoorClientError("流程执行中，不允许手动操作")
        resp = await self.door.close_door(door_id)
        self._update_door_state(door_id, resp.doorStatus)
        await self.rcs.report_door_closed(door_id)
        logger.info("手动关门: %s", door_id)

    async def query_door_status(self, door_id: str) -> AngelDoorStatus:
        """查询门状态"""
        try:
            resp = await self.door.get_status(door_id)
            self._update_door_state(door_id, resp.doorStatus)
            return AngelDoorStatus(resp.doorStatus)
        except (DoorClientError, ValueError):
            return AngelDoorStatus.UNKNOWN

    async def query_zone_status(self) -> str:
        """查询区域状态"""
        try:
            resp = await self.zone.get_status()
            self._status.zone.status = resp.status
            self._status.zone.occupied_by = resp.occupied_by
            return resp.status
        except ZoneClientError:
            return "unknown"
