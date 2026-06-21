"""
Angel 风淋门协议客户端

封装对底层风淋门 HTTP 接口的调用：
  - POST /acs/door/{door_id}  控制门
  - GET  /acs/door/{door_id}  查询门状态
"""

import asyncio
import logging
from datetime import datetime
from urllib.parse import urljoin
from typing import Optional

import httpx

from .config import AngelConfig
from .models import (
    AngelControlRequest, AngelControlResponse,
    AngelStatusResponse, AngelDoorStatus, AngelCode,
)

logger = logging.getLogger(__name__)


class DoorClientError(Exception):
    """风淋门客户端异常"""
    pass


class DoorClient:
    """风淋门 HTTP 客户端"""

    def __init__(self, config: AngelConfig):
        self.config = config
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            base_url=config.base_url,
        )
        self._log_target: Optional[list] = None      # 统一日志缓冲区引用
        self._log_counter = 0

    def set_log_target(self, target: list):
        """设置统一日志缓冲区（由 main.py 注入）"""
        self._log_target = target

    def _log_req(self, source: str, method: str, endpoint: str,
                 req_body, resp_body, resp_status: int = 200):
        """记录 ACS 请求到统一日志缓冲区"""
        if self._log_target is None:
            return
        self._log_counter += 1
        entry = {
            "id": self._log_counter,
            "time": datetime.now().strftime("%H:%M:%S.%f")[:12],
            "source": source,
            "method": method,
            "endpoint": endpoint,
            "request": req_body if isinstance(req_body, dict) else str(req_body),
            "response": resp_body if isinstance(resp_body, dict) else str(resp_body),
            "status": resp_status,
        }
        self._log_target.append(entry)
        while len(self._log_target) > 500:
            self._log_target.pop(0)

    async def close(self):
        await self._client.aclose()

    def set_sim_mode(self, enabled: bool):
        """切换模拟模式，重定向到本地模拟端点"""
        import os
        # 动态端口：从环境或模块默认取，不硬编码 5012
        base_port = os.environ.get("ASAP_PORT", "5012")
        if enabled:
            new_base = f"http://127.0.0.1:{base_port}/sim"
        else:
            new_base = self.config.base_url
        # 重新创建 httpx 客户端以更新 base_url（httpx 的 base_url 不可变）
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            base_url=new_base,
        )
        logger.info("DoorClient %s 模拟模式: base_url=%s",
                     "启用" if enabled else "关闭", new_base)

    def _build_url(self, door_id: str) -> str:
        return f"/acs/door/{door_id}"

    # ── 开门 ──────────────────────────────────

    async def open_door(
        self,
        door_id: str,
        direction: str = "1",
        robot_name: str = "",
    ) -> AngelControlResponse:
        """发送开门指令"""
        request = AngelControlRequest(
            doorSerial=door_id,
            command="1",
            Direction=direction,
            RobotName=robot_name,
        )
        return await self._control(request, door_id)

    # ── 关门 ──────────────────────────────────

    async def close_door(self, door_id: str) -> AngelControlResponse:
        """发送关门指令"""
        request = AngelControlRequest(
            doorSerial=door_id,
            command="2",
        )
        return await self._control(request, door_id)

    # ── 控制（底层） ───────────────────────────

    async def _control(
        self,
        request: AngelControlRequest,
        door_id: str,
    ) -> AngelControlResponse:
        """调用 POST /acs/door/{door_id}"""
        url = self._build_url(door_id)
        req_dict = request.model_dump(exclude_none=True)
        try:
            resp = await self._client.post(url, json=req_dict)
            data = resp.json()
            result = AngelControlResponse(**data)
            self._log_req("acs", "POST", url, req_dict, data, resp.status_code)
            return result
        except httpx.TimeoutException as e:
            self._log_req("acs", "POST", url, req_dict, str(e), 0)
            raise DoorClientError(f"控制门超时: {door_id}") from e
        except httpx.HTTPError as e:
            self._log_req("acs", "POST", url, req_dict, str(e), 0)
            raise DoorClientError(f"控制门 HTTP错误: {door_id} -> {e}") from e
        except (ValueError, KeyError) as e:
            self._log_req("acs", "POST", url, req_dict, str(e), 0)
            raise DoorClientError(f"控制门 响应解析失败: {door_id} -> {e}") from e

    # ── 状态查询 ──────────────────────────────

    async def get_status(self, door_id: str) -> AngelStatusResponse:
        """调用 GET /acs/door/{door_id} 查询门状态"""
        url = self._build_url(door_id)
        try:
            resp = await self._client.get(url)
            data = resp.json()
            result = AngelStatusResponse(**data)
            self._log_req("acs", "GET", url, {"door_id": door_id}, data, resp.status_code)
            return result
        except httpx.TimeoutException as e:
            self._log_req("acs", "GET", url, {"door_id": door_id}, str(e), 0)
            raise DoorClientError(f"查询门状态超时: {door_id}") from e
        except httpx.HTTPError as e:
            self._log_req("acs", "GET", url, {"door_id": door_id}, str(e), 0)
            raise DoorClientError(f"查询门状态 HTTP错误: {door_id} -> {e}") from e
        except (ValueError, KeyError) as e:
            self._log_req("acs", "GET", url, {"door_id": door_id}, str(e), 0)
            raise DoorClientError(f"查询门状态 响应解析失败: {door_id} -> {e}") from e

    # ── 轮询直到门开 ──────────────────────────

    async def wait_for_open(
        self,
        door_id: str,
        timeout: Optional[float] = None,
        interval: Optional[float] = None,
    ) -> AngelStatusResponse:
        """轮询门状态直到 command==1 且 doorStatus==1"""
        timeout = timeout or self.config.poll_timeout
        interval = interval or self.config.poll_interval
        deadline = asyncio.get_event_loop().time() + timeout

        while True:
            status = await self.get_status(door_id)
            logger.debug("轮询门[%s]状态: command=%s doorStatus=%s code=%s",
                         door_id, status.command, status.doorStatus, status.code)

            if status.code == AngelCode.ERROR.value:
                raise DoorClientError(
                    f"门[{door_id}]异常: code={status.code} status={status.doorStatus}"
                )

            if status.doorStatus == AngelDoorStatus.FAULT.value:
                raise DoorClientError(f"门[{door_id}]故障")

            if status.command == "1" and status.doorStatus == AngelDoorStatus.OPENED.value:
                return status

            if asyncio.get_event_loop().time() >= deadline:
                raise DoorClientError(f"等待门[{door_id}]打开超时({timeout}s)")

            await asyncio.sleep(interval)

    # ── 轮询直到门关 ──────────────────────────

    async def wait_for_close(
        self,
        door_id: str,
        timeout: Optional[float] = None,
        interval: Optional[float] = None,
    ) -> AngelStatusResponse:
        """轮询门状态直到 doorStatus==0"""
        timeout = timeout or self.config.poll_timeout
        interval = interval or self.config.poll_interval
        deadline = asyncio.get_event_loop().time() + timeout

        while True:
            status = await self.get_status(door_id)
            logger.debug("轮询门[%s]状态: command=%s doorStatus=%s code=%s",
                         door_id, status.command, status.doorStatus, status.code)

            if status.code == AngelCode.ERROR.value:
                raise DoorClientError(
                    f"门[{door_id}]异常: code={status.code} status={status.doorStatus}"
                )

            if status.doorStatus == AngelDoorStatus.FAULT.value:
                raise DoorClientError(f"门[{door_id}]故障")

            if status.doorStatus == AngelDoorStatus.CLOSED.value:
                return status

            if asyncio.get_event_loop().time() >= deadline:
                raise DoorClientError(f"等待门[{door_id}]关闭超时({timeout}s)")

            await asyncio.sleep(interval)
