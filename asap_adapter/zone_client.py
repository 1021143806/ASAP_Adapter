"""
区域管控客户端

封装对区域管控 API 的调用：
  - POST /api/zones/enter  请求进入区域（独占）
  - POST /api/zones/exit   退出区域
  - GET  /api/zones/status 查询区域状态
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx

from .config import ZoneConfig
from .models import (
    ZoneEnterRequest, ZoneEnterResponse,
    ZoneExitRequest, ZoneExitResponse,
    ZoneStatusResponse,
)

logger = logging.getLogger(__name__)


class ZoneClientError(Exception):
    """区域管控客户端异常"""
    pass


class ZoneClient:
    """区域管控 HTTP 客户端"""

    def __init__(self, config: ZoneConfig):
        self.config = config
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._permission_id: Optional[str] = None
        self._log_target: Optional[list] = None

    def set_log_target(self, target: list):
        """设置统一日志缓冲区（由 main.py 注入）"""
        self._log_target = target

    def _log_req(self, source: str, method: str, endpoint: str,
                 req_body, resp_body, resp_status: int = 200):
        """记录 Zone API 请求到统一日志缓冲区"""
        if self._log_target is None:
            return
        entry = {
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

    @property
    def is_occupied(self) -> bool:
        """当前是否持有区域占用"""
        return self._permission_id is not None

    # ── 请求进入区域 ──────────────────────────

    async def enter(self) -> ZoneEnterResponse:
        """
        请求进入区域
        返回 200: 成功，获得 permission_id
        返回 409: 区域被占用，抛出 ZoneClientError
        """
        request = ZoneEnterRequest(
            zone_id=self.config.zone_id,
            client_id=self.config.client_id,
        )
        req_dict = request.model_dump()
        try:
            resp = await self._client.post(self.config.enter_url, json=req_dict)

            if resp.status_code == 409:
                err = resp.json()
                self._log_req("zone", "POST", self.config.enter_url, req_dict, err, 409)
                raise ZoneClientError(
                    f"区域[{self.config.zone_id}]被占用: {err.get('occupied_by', 'unknown')}"
                )

            resp.raise_for_status()
            data = resp.json()
            result = ZoneEnterResponse(**data)
            self._permission_id = result.permission_id
            self._log_req("zone", "POST", self.config.enter_url, req_dict, data, 200)
            logger.info("获得区域占用: zone=%s permission=%s",
                        self.config.zone_id, self._permission_id)
            return result

        except httpx.TimeoutException as e:
            self._log_req("zone", "POST", self.config.enter_url, req_dict, str(e), 0)
            raise ZoneClientError(f"请求区域超时: {self.config.zone_id}") from e
        except httpx.HTTPError as e:
            self._log_req("zone", "POST", self.config.enter_url, req_dict, str(e), 0)
            raise ZoneClientError(f"请求区域 HTTP错误: {e}") from e
        except (ValueError, KeyError) as e:
            self._log_req("zone", "POST", self.config.enter_url, req_dict, str(e), 0)
            raise ZoneClientError(f"请求区域 响应解析失败: {e}") from e

    # ── 带重试的进入 ──────────────────────────

    async def enter_with_retry(
        self,
        max_retries: Optional[int] = None,
        retry_interval: Optional[float] = None,
    ) -> ZoneEnterResponse:
        """
        带重试的请求进入区域
        区域被占用时等待并重试，直到超限返回失败
        """
        max_retries = max_retries or self.config.max_retries
        retry_interval = retry_interval or self.config.retry_interval

        for attempt in range(1, max_retries + 1):
            try:
                return await self.enter()
            except ZoneClientError as e:
                if "被占用" in str(e):
                    logger.warning("区域被占用，重试 %d/%d: %s",
                                   attempt, max_retries, e)
                    if attempt < max_retries:
                        await asyncio.sleep(retry_interval)
                    continue
                raise

        raise ZoneClientError(
            f"请求区域失败，已达最大重试次数({max_retries})"
        )

    # ── 退出区域 ──────────────────────────────

    async def exit(self) -> ZoneExitResponse:
        """退出区域"""
        request = ZoneExitRequest(
            zone_id=self.config.zone_id,
            client_id=self.config.client_id,
        )
        req_dict = request.model_dump()
        try:
            resp = await self._client.post(self.config.exit_url, json=req_dict)
            resp.raise_for_status()
            data = resp.json()
            result = ZoneExitResponse(**data)
            self._permission_id = None
            self._log_req("zone", "POST", self.config.exit_url, req_dict, data, 200)
            logger.info("释放区域成功: zone=%s", self.config.zone_id)
            return result

        except httpx.TimeoutException as e:
            self._log_req("zone", "POST", self.config.exit_url, req_dict, str(e), 0)
            raise ZoneClientError(f"退出区域超时: {self.config.zone_id}") from e
        except httpx.HTTPError as e:
            self._log_req("zone", "POST", self.config.exit_url, req_dict, str(e), 0)
            raise ZoneClientError(f"退出区域 HTTP错误: {e}") from e
        except (ValueError, KeyError) as e:
            self._log_req("zone", "POST", self.config.exit_url, req_dict, str(e), 0)
            raise ZoneClientError(f"退出区域 响应解析失败: {e}") from e

    # ── 带重试的退出 ──────────────────────────

    async def exit_with_retry(
        self,
        max_retries: Optional[int] = None,
        retry_interval: Optional[float] = None,
    ) -> ZoneExitResponse:
        """带重试的退出区域，确保最终释放"""
        last_error = None
        max_retries = max_retries or self.config.exit_max_retries
        retry_interval = retry_interval or self.config.exit_retry_interval

        for attempt in range(1, max_retries + 1):
            try:
                return await self.exit()
            except ZoneClientError as e:
                last_error = e
                logger.warning("退出区域失败 %d/%d: %s",
                               attempt, max_retries, e)
                if attempt < max_retries:
                    await asyncio.sleep(retry_interval)

        raise ZoneClientError(
            f"退出区域失败，已达最大重试次数({max_retries}): {last_error}"
        )

    # ── 查询区域状态 ──────────────────────────

    async def get_status(self) -> ZoneStatusResponse:
        """查询区域状态"""
        try:
            params = {"zone_id": self.config.zone_id}
            resp = await self._client.get(self.config.status_url, params=params)
            resp.raise_for_status()
            data = resp.json()
            self._log_req("zone", "GET", self.config.status_url,
                         {"zone_id": self.config.zone_id}, data, 200)
            return ZoneStatusResponse(**data)

        except httpx.TimeoutException as e:
            self._log_req("zone", "GET", self.config.status_url,
                         {"zone_id": self.config.zone_id}, str(e), 0)
            raise ZoneClientError(f"查询区域状态超时: {self.config.zone_id}") from e
        except httpx.HTTPError as e:
            self._log_req("zone", "GET", self.config.status_url,
                         {"zone_id": self.config.zone_id}, str(e), 0)
            raise ZoneClientError(f"查询区域状态 HTTP错误: {e}") from e
        except (ValueError, KeyError) as e:
            self._log_req("zone", "GET", self.config.status_url,
                         {"zone_id": self.config.zone_id}, str(e), 0)
            raise ZoneClientError(f"查询区域状态 响应解析失败: {e}") from e
