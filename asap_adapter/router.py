"""
HTTP API 路由

提供：
  - RCS/WDCS 对接接口（控制/状态查询）
  - ASAP 管理接口（启动流程/手动控制）
  - SSE 事件推送（WebUI 实时更新）
  - 健康检查
"""

import asyncio
import json
import logging
from typing import AsyncGenerator
from datetime import datetime

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, PlainTextResponse

from .models import (
    AngelDoorStatus,
    RcsDoorControlRequest, RcsDoorControlResponse,
    RcsStatusQueryRequest, RcsStatusQueryResponse, RcsStatusData,
)
from .state_machine import StateMachine, DoorClientError, ZoneClientError

logger = logging.getLogger(__name__)


def _get_sm(request: Request) -> StateMachine:
    return request.app.state.sm


def _door_code_to_id(door_code: str, sm: StateMachine) -> str:
    """将 RCS doorCode 映射为 ASAP door_id"""
    mapping = sm.config.rcs.door_code_mapping
    for door_id, code in mapping.items():
        if code == door_code:
            return door_id
    logger.warning("未找到doorCode[%s]的映射，默认使用外门", door_code)
    return sm.config.angel.outer_door_id


def create_router(app: FastAPI) -> APIRouter:
    """创建路由器，关联 FastAPI 应用"""
    router = APIRouter()

    # ── 健康检查 ──────────────────────────────

    @router.get("/actuator/health")
    async def health():
        """健康检查（返回纯文本 1000，与 RCS 协议一致）"""
        return PlainTextResponse("1000")

    # ── SSE 事件流 ────────────────────────────

    @router.get("/api/sse/events")
    async def sse_events(request: Request):
        """SSE 事件推送"""
        sse_clients = request.app.state.sse_clients
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        sse_clients.append(queue)

        async def generate() -> AsyncGenerator[str, None]:
            try:
                # 先发送当前状态快照
                sm = _get_sm(request)
                status = sm.status
                snapshot = json.dumps({
                    "timestamp": datetime.now().isoformat(),
                    "event_type": "snapshot",
                    "data": status.model_dump(),
                }, ensure_ascii=False)
                yield f"data: {snapshot}\n\n"

                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield msg
                    except asyncio.TimeoutError:
                        yield f"data: {json.dumps({'event_type': 'heartbeat'})}\n\n"
            finally:
                if queue in sse_clients:
                    sse_clients.remove(queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── RCS 门禁控制 ──────────────────────────

    @router.post("/api/rcs/controlDoor")
    async def rcs_control_door(req: RcsDoorControlRequest, request: Request):
        """
        RCS 门禁控制入口
        当 status=1(开门) 时启动完整风淋流程
        当 status=2(关门) 时执行手动关门
        """
        sm = _get_sm(request)
        logger.info("RCS控制请求: door=%s status=%d agv=%s",
                    req.doorCode, req.status, req.deviceCode)

        if req.status == 1:
            success = await sm.start(agv_id=req.deviceCode)
            if not success:
                return RcsDoorControlResponse(
                    code=2001,
                    msg=f"风淋流程忙碌中，当前状态: {sm.state.value}",
                )
            return RcsDoorControlResponse(msg="风淋流程已启动")

        elif req.status == 2:
            try:
                door_id = _door_code_to_id(req.doorCode, sm)
                await sm.manual_close_door(door_id)
                return RcsDoorControlResponse(msg=f"门[{door_id}]已关闭")
            except (DoorClientError, ZoneClientError) as e:
                return RcsDoorControlResponse(code=2002, msg=f"关门失败: {e}")

        return RcsDoorControlResponse(code=2003, msg=f"未知状态: {req.status}")

    # ── RCS 门状态查询 ────────────────────────

    @router.post("/api/rcs/doorStatus")
    async def rcs_door_status(req: RcsStatusQueryRequest, request: Request):
        """RCS 门状态查询"""
        sm = _get_sm(request)
        try:
            door_id = _door_code_to_id(req.doorCode, sm)
            status = await sm.query_door_status(door_id)

            rcs_status = 0
            if status == AngelDoorStatus.OPENED:
                rcs_status = 1
            elif status == AngelDoorStatus.CLOSED:
                rcs_status = 2

            return RcsStatusQueryResponse(
                data=RcsStatusData(status=rcs_status),
            )
        except (DoorClientError, ZoneClientError) as e:
            return RcsStatusQueryResponse(code=9999, msg=str(e))

    # ── ASAP 管理接口 ─────────────────────────

    @router.get("/api/asap/status")
    async def get_asap_status(request: Request):
        """获取风淋系统整体状态"""
        sm = _get_sm(request)
        return sm.status.model_dump()

    @router.post("/api/asap/start")
    async def start_air_shower(request: Request, agv_id: str = ""):
        """启动风淋流程"""
        sm = _get_sm(request)
        success = await sm.start(agv_id=agv_id)
        if not success:
            raise HTTPException(
                status_code=409,
                detail=f"风淋流程忙碌中，当前状态: {sm.state.value}",
            )
        return {"message": "风淋流程已启动", "state": sm.state.value}

    @router.post("/api/asap/cancel")
    async def cancel_air_shower(request: Request):
        """取消风淋流程"""
        sm = _get_sm(request)
        await sm.cancel()
        return {"message": "风淋流程已取消", "state": sm.state.value}

    @router.post("/api/asap/manual/open")
    async def manual_open(request: Request, door_id: str):
        """手动开门"""
        sm = _get_sm(request)
        try:
            await sm.manual_open_door(door_id)
            return {"message": f"门[{door_id}]已打开"}
        except (DoorClientError, ZoneClientError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/api/asap/manual/close")
    async def manual_close(request: Request, door_id: str):
        """手动关门"""
        sm = _get_sm(request)
        try:
            await sm.manual_close_door(door_id)
            return {"message": f"门[{door_id}]已关闭"}
        except (DoorClientError, ZoneClientError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    return router
