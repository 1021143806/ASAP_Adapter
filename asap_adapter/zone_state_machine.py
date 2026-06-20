"""
区域管控状态机

仅管理一个虚拟门 q001，与区域进入/退出 API 映射：
  - q001 status=1 → 进入区域 (POST /api/zones/enter, 间隔1s重试直到granted)
  - q001 status=2 → 退出区域 (POST /api/zones/exit, 最多20次间隔5s, 直到released)
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
    ENTERING = "entering"      # 正在请求进入区域（轮询+进入）
    INSIDE = "inside"          # 区域内，q001 已开
    EXITING = "exiting"        # 正在退出区域
    ERROR = "error"


class ZoneFlowStatus:
    """区域管控流程状态快照（供 WebUI 使用）"""

    def __init__(self):
        self.state: ZoneFlowState = ZoneFlowState.IDLE
        self.door_status: str = "2"             # q001: 1=开, 2=关
        self.door_code: str = "q001"
        self.current_agv: str = ""
        self.zone_id: str = ""
        self.zone_status: str = "unknown"
        self.zone_occupied_by: str = ""
        self.current_step: int = 0              # 1=进入中, 2=区域内, 3=退出中
        self.started_at: str = ""
        self.last_check: str = ""
        self.error_message: str = ""
        self.exit_retry_count: int = 0          # 退出重试计数
        self.step_log: list = []

    def dump(self) -> dict:
        return {
            "state": self.state.value,
            "door_status": self.door_status,
            "door_code": self.door_code,
            "current_agv": self.current_agv,
            "zone_id": self.zone_id,
            "zone_status": self.zone_status,
            "zone_occupied_by": self.zone_occupied_by,
            "current_step": self.current_step,
            "started_at": self.started_at,
            "last_check": self.last_check,
            "error_message": self.error_message,
            "exit_retry_count": self.exit_retry_count,
            "step_log": self.step_log,
        }


class ZoneStateMachine:
    """区域管控状态机（单门 q001）"""

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

        self.on_event: Optional[Callable[[], Awaitable[None]]] = None

        self._status.door_code = config.zone.entry_door_code
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
    def door_status(self) -> str:
        return self._status.door_status

    def door_status_by_code(self, door_code: str) -> int:
        """根据 RCS doorCode 返回门状态 (0=离线, 1=开门, 2=关门)"""
        if door_code == self.config.zone.entry_door_code:
            return 1 if self._status.door_status == "1" else 2
        return 0

    # ── RCS 控制接口 ──────────────────────────

    async def handle_open(self, door_code: str, agv_id: str = "") -> (bool, str):
        """RCS doorStatus=1 — 开门"""
        entry = self.config.zone.entry_door_code

        if door_code == entry:
            if self.is_busy:
                return False, f"区域流程忙碌中: {self._state.value}"
            self._cancel_event.clear()
            self._status = ZoneFlowStatus()
            self._status.door_code = entry
            self._status.zone_id = self.config.zone.zone_id
            self._status.current_agv = agv_id
            self._status.started_at = datetime.now().isoformat()
            self._task = asyncio.create_task(self._enter_flow())
            self._publish()
            return True, "进入区域流程已启动"

        return False, f"未知门编号: {door_code}"

    async def handle_close(self, door_code: str) -> (bool, str):
        """RCS doorStatus=2 — 关门"""
        entry = self.config.zone.entry_door_code

        if door_code == entry:
            if self._state == ZoneFlowState.INSIDE:
                # 立即标记关门
                self._status.door_status = "2"
                self._log_step(3, "q001 已关(RCS下发)", "q001_closed", "info", "",
                               {"door": entry, "status": "closed"})
                self._publish()
                # 启动退出流程
                self._cancel_event.clear()
                self._task = asyncio.create_task(self._exit_flow())
                return True, "退出区域流程已启动"
            else:
                return False, f"当前状态不可关 q001: {self._state.value}"

        return False, f"未知门编号: {door_code}"

    # ── 进入区域流程 ──────────────────────────

    async def _enter_flow(self):
        """进入区域：POST enter, 间隔1s重试直到 granted"""
        try:
            self._set_state(ZoneFlowState.ENTERING)
            self._status.current_step = 1

            result = None
            attempt = 0
            zone_id = self.config.zone.zone_id
            enter_url = self.config.zone.enter_url

            while not self._cancel_event.is_set():
                attempt += 1
                try:
                    self._log_step(1, f"请求进入区域(第{attempt}次)", "enter_zone", "send",
                                   f"POST {enter_url}",
                                   {"zone_id": zone_id, "client_id": self.config.zone.client_id})
                    result = await self.zone.enter()
                    self._log_step(1, "进入区域成功", "enter_zone", "recv",
                                   f"POST {enter_url}",
                                   {"permission_id": result.permission_id,
                                    "zone_id": zone_id, "status": "granted"})
                    break
                except ZoneClientError as e:
                    if "被占用" in str(e):
                        self._status.zone_status = "occupied"
                        self._status.zone_occupied_by = str(e).split(":")[-1].strip() if ":" in str(e) else ""
                        self._publish()
                        logger.info("Zone 被占用, 1s后重试 (第%d次)", attempt)
                        await asyncio.sleep(1)
                        continue
                    raise

            if result is None:
                raise ZoneClientError("进入区域被取消")

            # 进入成功，标记开门
            self._set_state(ZoneFlowState.INSIDE)
            self._status.door_status = "1"
            self._status.current_step = 2
            self._status.zone_status = "granted"
            self._log_step(2, "q001 已开(区域已进入)", "q001_open", "info", "",
                           {"door": self._status.door_code, "status": "open"})
            self._publish()
            logger.info("Zone: 进入区域完成, q001 已开")

        except asyncio.CancelledError:
            logger.info("Zone 进入流程被取消")
            await self._cleanup()
        except ZoneClientError as e:
            logger.error("Zone 进入流程失败: %s", e)
            self._status.error_message = str(e)
            self._set_state(ZoneFlowState.ERROR)
            await self._cleanup()

    # ── 退出区域流程 ──────────────────────────

    async def _exit_flow(self):
        """退出区域：POST exit, 最多20次间隔5s, 直到 released"""
        try:
            self._set_state(ZoneFlowState.EXITING)
            self._status.current_step = 3

            released = False
            exit_url = self.config.zone.exit_url
            zone_id = self.config.zone.zone_id
            max_retries = 20
            retry_interval = 5

            for attempt in range(1, max_retries + 1):
                if self._cancel_event.is_set():
                    raise asyncio.CancelledError()

                try:
                    self._log_step(3, f"退出区域(第{attempt}/{max_retries}次)", "exit_zone", "send",
                                   f"POST {exit_url}",
                                   {"zone_id": zone_id, "client_id": self.config.zone.client_id})
                    await self.zone.exit()
                    result_data = {"zone_id": zone_id, "status": "released"}
                    self._log_step(3, "退出区域成功", "exit_zone", "recv",
                                   f"POST {exit_url}", result_data)
                    self._status.zone_status = "available"
                    self._status.zone_occupied_by = ""
                    released = True
                    break
                except ZoneClientError as e:
                    self._status.exit_retry_count = attempt
                    self._publish()
                    logger.warning("退出区域失败(第%d/%d次): %s", attempt, max_retries, e)
                    await asyncio.sleep(retry_interval)

            if not released:
                logger.error("退出区域失败，已达最大重试(%d次)", max_retries)
                self._status.error_message = f"退出区域失败({max_retries}次)"

            # 重置
            self._set_state(ZoneFlowState.IDLE)
            self._status.current_step = 0
            self._status.door_status = "2"
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

    # ── 取消 / 清理 ──────────────────────────

    async def cancel(self):
        if self._task and not self._task.done():
            self._cancel_event.set()
            self._task.cancel()
            logger.info("Zone 流程被取消")
        self._set_state(ZoneFlowState.IDLE)
        self._publish()

    async def _cleanup(self):
        try:
            if self.zone.is_occupied:
                logger.warning("Zone 清理: 尝试释放区域")
                await self.zone.exit_with_retry()
        except Exception as e:
            logger.error("Zone 清理释放区域失败: %s", e)
        self._set_state(ZoneFlowState.IDLE)

    async def force_door_state(self, door_code: str, status: str) -> bool:
        """强制设置门状态（调试/异常恢复用）"""
        if door_code == self._status.door_code:
            old = self._status.door_status
            self._status.door_status = status
            logger.info("Zone 强制设置 %s: %s → %s", door_code, old, status)
            self._log_step(0, f"强制设置 q001={'开' if status=='1' else '关'}", "force_door",
                           "info", "", {"door": door_code, "old": old, "new": status})
            self._publish()
            return True
        return False

    # ── 内部辅助 ──────────────────────────────

    def _set_state(self, new_state: ZoneFlowState):
        old = self._state
        self._state = new_state
        self._status.state = new_state
        logger.info("Zone 状态变更: %s → %s", old.value, new_state.value)

    def _log_step(self, step: int, step_name: str, action: str,
                  direction: str, url: str, payload: dict):
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
        if self.on_event:
            asyncio.ensure_future(self._safe_publish())

    async def _safe_publish(self):
        try:
            await self.on_event()
        except Exception as e:
            logger.warning("Zone 事件发布失败: %s", e)
