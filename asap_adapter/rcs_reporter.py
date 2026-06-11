"""
RCS/WDCS 状态上报客户端

负责向 RCS/WDCS 系统上报门状态变化：
  - POST /changeDoorStatus  (主动上报)
  - 状态上报频率限流
"""

import asyncio
import logging
from typing import Optional

import httpx

from .config import RcsConfig
from .models import (
    RcsChangeStatusRequest, RcsChangeStatusResponse,
)

logger = logging.getLogger(__name__)


class RcsReporterError(Exception):
    """RCS 上报客户端异常"""
    pass


class RcsReporter:
    """RCS 状态上报客户端"""

    def __init__(self, config: RcsConfig):
        self.config = config
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._last_report_time: float = 0.0

    async def close(self):
        await self._client.aclose()

    # ── 上报门状态 ────────────────────────────

    async def report_door_status(
        self,
        door_num: str,
        door_status: str,  # "1"=开门, "2"=关门
    ) -> Optional[RcsChangeStatusResponse]:
        """
        上报门状态到 RCS
        受 report_interval 限流，防止频繁上报
        """
        if not self.config.change_status_url:
            logger.debug("RCS上报URL未配置，跳过状态上报")
            return None

        # 限流
        now = asyncio.get_event_loop().time()
        if now - self._last_report_time < self.config.report_interval:
            logger.debug("RCS上报频率受限，跳过")
            return None
        self._last_report_time = now

        request = RcsChangeStatusRequest(
            doorNum=door_num,
            doorStatus=door_status,
        )

        try:
            logger.info("上报门状态到RCS: door=%s status=%s", door_num, door_status)
            resp = await self._client.post(
                self.config.change_status_url,
                json=request.model_dump(),
            )
            resp.raise_for_status()
            data = resp.json()
            return RcsChangeStatusResponse(**data)

        except httpx.TimeoutException as e:
            logger.warning("RCS状态上报超时: %s", e)
            return None
        except httpx.HTTPError as e:
            logger.warning("RCS状态上报HTTP错误: %s", e)
            return None
        except (ValueError, KeyError) as e:
            logger.warning("RCS状态上报响应解析失败: %s", e)
            return None

    # ── 便捷方法 ──────────────────────────────

    async def report_door_open(self, door_id: str):
        """上报门已打开"""
        door_code = self.config.door_code_mapping.get(door_id, door_id)
        await self.report_door_status(door_code, "1")

    async def report_door_closed(self, door_id: str):
        """上报门已关闭"""
        door_code = self.config.door_code_mapping.get(door_id, door_id)
        await self.report_door_status(door_code, "2")
