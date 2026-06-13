"""
区域管控状态机

独立于风淋流程，专用于 q001/q002 虚拟门与区域进入/退出 API 的映射。
RCS 门控制映射：
  - q001 status=1 → 进入区域 (POST /api/zones/enter)，上报 q001 开
  - q001 status=2 → AGV 已进入，标记 q001 关，上报 RCS
  - q002 status=1 → AGV 到达出口，q002 开
  - q002 status=2 → 退出区域 (POST /api/zones/exit)，上报 RCS
"""

import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Awaitable

from .config import AppConfig
from .zone_client import ZoneClient, ZoneClientError
from .rcs_reporter import RcsReporter

logger = logging.getLogger(__name__)


class ZoneFlowState(str, Enum):
    IDLE = "idle"
    ENTERING = "entering"          # 正在请求进入区域（轮询+进入）
    INSIDE = "inside"              # 区域内，q001 已上报开
    Q001_CLOSED = "q001_closed"    # q001 已关，等待 q002 开
    Q002_OPENED = "q002_opened"    # q002 已开，AGV 在出口等待
    EXITING = "exiting"            # 正在退出区域
    ERROR = "error"


class ZoneFlowStatus:
    """区域管控流程状态快照（供 WebUI 使用）"""

    def __init__(self):
        self.state: ZoneFlowState = ZoneFlowState.IDLE
        self.entry_door_status: str = "2"     # q001 RCS 状态: 1=开, 2=关
        self.exit_door_status: str = "2"      # q002 RCS 状态: 1=开, 2=关
        self.entry_door_code: str = "q001"
        self.exit_door_code: str = "q002"
        self.current_agv: str = ""
        self.zone_id: str = ""
        self.zone_status: str = "unknown"     # available / occupied / granted / released
        self.zone_occupied_by: str = ""
        self.current_step: int = 0            # 1=进入中, 2=区域内, 3=退出中
        self.started_at: str = ""
        self.last_check: str = ""
        self.error_message: str = ""
        self.step_log: list = []

    def dump(self) -> dict:
        return {
            "state": self.state.value,
            "entry_door_status": self.entry_door_status,
            "exit_door_status": self.exit_door_status,
            "entry_door_code": self.entry_door_code,
            "exit_door_code": self.exit_door_code,
            "current_agv": self.current_agv,
            "zone_id": self.zone_id,
            "zone_status": self.zone_status,
            "zone_occupied_by": self.zone_occupied_by,
            "current_step": self.current_step,
            "started_at": self.started_at,
            "last_check": self.last_check,
            "error_message": self.error_message,
            "step_log": self.step_log,
        }


class ZoneStateMachine:
    """区域管控状态机"""

    def __init__(
        self,
        config: AppConfig,
        zone_client: ZoneClient,
        rcs_reporter: RcsReporter,
    ):
        self.config = config
        self.zone = zone_client
        self.rcs = rcs_reporter

        self._state = ZoneFlowState.IDLE
        self._status = ZoneFlowStatus()
        self._task: Optional[asyncio.Task] = None
        self._cancel_event = asyncio.Event()

        # 回调：状态变更通知（SSE 推送）
        self.on_event: Optional[Callable[[], Awaitable[None]]] = None

        # 同步门编码
        self._status.entry_door_code = config.zone.entry_door_code
        self._status.exit_door_code = config.zone.exit_door_code
        self._status.zone_id = config.zone.zone_id

    # ── 属性 ──────────────────────────────────

    @property
    def state(self) -> ZoneFlowState:
        return self._state

    @property
    def status(self) -> ZoneFlowStatus:
        return self._status

    @property
    def is_busy(self) -> bool:
        return self._state not in (ZoneFlowState.IDLE, ZoneFlowState.ERROR)

    @property
    def entry_door_status(self) -> str:
        """q001 门状态 (1=开, 2=关)，供 RCS 查询"""
        return self._status.entry_door_status

    @property
    def exit_door_status(self) -> str:
        """q002 门状态 (1=开, 2=关)，供 RCS 查询"""
        return self._status.exit_door_status

    def door_status_by_code(self, door_code: str) -> int:
        """根据 RCS doorCode 返回门状态 (0=离线, 1=开门, 2=关门)"""
        entry = self.config.zone.entry_door_code
        exit_ = self.config.zone.exit_door_code
        if door_code == entry:
            return 1 if self._status.entry_door_status == "1" else 2
        elif door_code == exit_:
            return 1 if self._status.exit_door_status == "1" else 2
        return 0

    # ── RCS 控制接口 ──────────────────────────

    async def handle_open(self, door_code: str, agv_id: str = "") -> (bool, str):
        """
        RCS doorStatus=1 — 开门
        返回 (success, message)
        """
        entry = self.config.zone.entry_door_code
        exit_ = self.config.zone.exit_door_code

        if door_code == entry:
            if self.is_busy:
                return False, f"区域流程忙碌中: {self._state.value}"
            self._current_agv = agv_id
            self._cancel_event.clear()
            self._status = ZoneFlowStatus()
            self._status.entry_door_code = entry
            self._status.exit_door_code = exit_
            self._status.zone_id = self.config.zone.zone_id
            self._status.current_agv = agv_id
            self._status.started_at = datetime.now().isoformat()
            self._task = asyncio.create_task(self._enter_flow())
            self._publish()
            return True, "进入区域流程已启动"

        elif door_code == exit_:
            # q002 status=1: AGV 到达出口
            if self._state == ZoneFlowState.Q001_CLOSED:
                self._set_state(ZoneFlowState.Q002_OPENED)
                self._status.exit_door_status = "1"
                self._status.current_step = 4
                await self.rcs.report_door_open(exit_)
                self._log_step(4, "q002 已开 (AGV在出口)", "q002_open", "info", "",
                               {"door": exit_, "status": "opened"})
                self._publish()
                logger.info("Zone: q002 已开，等待关闭触发退出")
                return True, "q002_opened"
            else:
                return False, f"当前状态不可开 q002: {self._state.value}"

        return False, f"未知门编号: {door_code}"

    async def handle_close(self, door_code: str) -> (bool, str):
        """
        RCS doorStatus=2 — 关门
        返回 (success, message)
        """
        entry = self.config.zone.entry_door_code
        exit_ = self.config.zone.exit_door_code

        if door_code == entry:
            if self._state == ZoneFlowState.INSIDE:
                self._set_state(ZoneFlowState.Q001_CLOSED)
                self._status.entry_door_status = "2"
                self._status.current_step = 3
                # 上报 RCS：q001 已关
                self._log_step(3, "上报 q001 已关", "report_closed", "send",
                               f"POST {self.config.rcs.change_status_url}",
                               {"doorNum": self._status.entry_door_code, "doorStatus": "2"})
                await self.rcs.report_door_closed(entry)
                self._publish()
                logger.info("Zone: q001 已关，等待 q002 开")
                return True, "q001_closed"
            else:
                return False, f"当前状态不可关 q001: {self._state.value}"

        elif door_code == exit_:
            if self._state == ZoneFlowState.Q002_OPENED:
                self._cancel_event.clear()
                self._task = asyncio.create_task(self._exit_flow())
                self._publish()
                return True, "退出区域流程已启动"
            else:
                return False, f"当前状态不可关 q002: {self._state.value}"

        return False, f"未知门编号: {door_code}"

    # ── 进入区域流程 ──────────────────────────

    async def _enter_flow(self):
        """进入区域：轮询 zone 状态 → POST enter → 上报 q001 开"""
        try:
            # 1) 等待区域可用（轮询）
            self._set_state(ZoneFlowState.ENTERING)
            self._status.current_step = 1
            self._log_step(1, "等待区域可用", "poll_zone", "send",
                           f"GET {self.config.zone.status_url}",
                           {"zone_id": self.config.zone.zone_id})
            available = await self._wait_zone_available()
            if not available:
                raise ZoneClientError("等待区域可用超时")

            # 2) 请求进入区域
            self._log_step(1, "请求进入区域", "enter_zone", "send",
                           f"POST {self.config.zone.enter_url}",
                           {"zone_id": self.config.zone.zone_id,
                            "client_id": self.config.zone.client_id})
            result = await self.zone.enter_with_retry()
            self._status.zone_status = "granted"
            self._log_step(1, "进入区域成功", "enter_zone", "recv",
                           f"POST {self.config.zone.enter_url}",
                           {"permission_id": result.permission_id, "status": "granted"})

            # 3) 上报 RCS：q001 已开
            self._set_state(ZoneFlowState.INSIDE)
            self._status.entry_door_status = "1"
            self._status.current_step = 2
            self._log_step(2, "上报 q001 已开", "report_open", "send",
                           f"POST {self.config.rcs.change_status_url}",
                           {"doorNum": self._status.entry_door_code, "doorStatus": "1"})
            await self.rcs.report_door_open(self._status.entry_door_code)
            self._publish()
            logger.info("Zone: 进入区域完成，q001 已开")

        except asyncio.CancelledError:
            logger.info("Zone 进入流程被取消")
            await self._cleanup()
        except ZoneClientError as e:
            logger.error("Zone 进入流程失败: %s", e)
            self._status.error_message = str(e)
            self._set_state(ZoneFlowState.ERROR)
            await self._cleanup()

    async def _wait_zone_available(self, timeout: float = 120.0) -> bool:
        """轮询直到区域可用，超时返回 False"""
        deadline = asyncio.get_event_loop().time() + timeout
        interval = self.config.zone.zone_poll_interval
        if interval <= 0 or interval > 300:
            interval = 5  # 进入流程中加快轮询到 5 秒

        while asyncio.get_event_loop().time() < deadline:
            if self._cancel_event.is_set():
                return False
            try:
                status = await self.zone.get_status()
                self._status.zone_status = status.status
                self._status.zone_occupied_by = status.occupied_by
                self._status.last_check = datetime.now().isoformat()
                self._publish()
                if status.status == "available":
                    return True
                logger.info("Zone 被占用 (%s)，等待 %ds 后重试...",
                            status.occupied_by, interval)
            except ZoneClientError as e:
                logger.warning("Zone 查询失败: %s，%ds 后重试", e, interval)
            await asyncio.sleep(interval)

        logger.error("等待区域可用超时 (%ds)", timeout)
        return False

    # ── 退出区域流程 ──────────────────────────

    async def _exit_flow(self):
        """退出区域：POST exit → 轮询确认 → 上报 q002 关"""
        try:
            self._set_state(ZoneFlowState.EXITING)
            self._status.exit_door_status = "2"
            self._status.current_step = 5

            # 1) 退出区域
            self._log_step(5, "退出区域", "exit_zone", "send",
                           f"POST {self.config.zone.exit_url}",
                           {"zone_id": self.config.zone.zone_id,
                            "client_id": self.config.zone.client_id})
            await self.zone.exit_with_retry()
            self._log_step(5, "退出区域成功", "exit_zone", "recv",
                           f"POST {self.config.zone.exit_url}",
                           {"zone_id": self.config.zone.zone_id, "status": "released"})

            # 2) 轮询确认区域已释放
            self._status.current_step = 6
            self._log_step(6, "确认区域释放", "poll_released", "send",
                           f"GET {self.config.zone.status_url}",
                           {"zone_id": self.config.zone.zone_id})
            confirmed = await self._wait_zone_released()
            if confirmed:
                self._log_step(6, "区域已释放", "poll_released", "recv",
                               f"GET {self.config.zone.status_url}",
                               {"zone_id": self.config.zone.zone_id, "status": "available"})

            # 3) 上报 RCS：q002 已关
            self._log_step(6, "上报 q002 已关", "report_closed", "send",
                           f"POST {self.config.rcs.change_status_url}",
                           {"doorNum": self._status.exit_door_code, "doorStatus": "2"})
            await self.rcs.report_door_closed(self._status.exit_door_code)

            # 4) 重置
            self._set_state(ZoneFlowState.IDLE)
            self._status.current_step = 0
            self._status.entry_door_status = "2"
            self._status.exit_door_status = "2"
            self._status.zone_status = "available"
            self._status.zone_occupied_by = ""
            self._publish()
            logger.info("Zone: 退出区域完成")

        except asyncio.CancelledError:
            logger.info("Zone 退出流程被取消")
            await self._cleanup()
        except ZoneClientError as e:
            logger.error("Zone 退出流程失败: %s", e)
            self._status.error_message = str(e)
            self._set_state(ZoneFlowState.ERROR)
            await self._cleanup()

    async def _wait_zone_released(self, timeout: float = 60.0) -> bool:
        """轮询直到区域状态变为 available"""
        deadline = asyncio.get_event_loop().time() + timeout
        interval = 3  # 退出后快速轮询

        while asyncio.get_event_loop().time() < deadline:
            try:
                status = await self.zone.get_status()
                self._status.zone_status = status.status
                self._status.zone_occupied_by = status.occupied_by
                self._status.last_check = datetime.now().isoformat()
                self._publish()
                if status.status in ("available", "released"):
                    return True
            except ZoneClientError:
                pass
            await asyncio.sleep(interval)

        logger.warning("确认区域释放超时，假定已释放")
        return False

    # ── 取消 / 清理 ──────────────────────────

    async def cancel(self):
        """取消当前流程"""
        if self._task and not self._task.done():
            self._cancel_event.set()
            self._task.cancel()
            logger.info("Zone 流程被取消")
        self._set_state(ZoneFlowState.IDLE)
        self._publish()

    async def _cleanup(self):
        """异常/取消后的清理"""
        try:
            if self.zone.is_occupied:
                logger.warning("Zone 清理: 尝试释放区域")
                await self.zone.exit_with_retry()
        except Exception as e:
            logger.error("Zone 清理释放区域失败: %s", e)
        self._set_state(ZoneFlowState.IDLE)

    # ── 内部辅助 ──────────────────────────────

    def _set_state(self, new_state: ZoneFlowState):
        old = self._state
        self._state = new_state
        self._status.state = new_state
        logger.info("Zone 状态变更: %s → %s", old.value, new_state.value)

    def _log_step(self, step: int, step_name: str, action: str,
                  direction: str, url: str, payload: dict):
        """记录步骤报文"""
        entry = {
            "step": step,
            "step_name": step_name,
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
        """发布事件（SSE 推送）"""
        if self.on_event:
            asyncio.ensure_future(self._safe_publish())

    async def _safe_publish(self):
        try:
            await self.on_event()
        except Exception as e:
            logger.warning("Zone 事件发布失败: %s", e)
