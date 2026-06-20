"""
模拟器 HTTP 路由

实现 Angel 风淋门协议 + 区域管控协议的全部端点，
以及模拟器管理端点（供 WebUI 调用）。
"""

import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .models import (
    AngelControlRequest, AngelControlResponse, AngelStatusResponse,
    ZoneEnterRequest, ZoneEnterResponse, ZoneEnterConflict,
    ZoneExitRequest, ZoneExitResponse, ZoneStatusResponse,
)
from .state import SimController

logger = logging.getLogger(__name__)


def create_router(sim: SimController) -> APIRouter:
    router = APIRouter()

    # ── 健康检查 ──────────────────────────────

    @router.get("/actuator/health")
    async def health():
        return PlainTextResponse("1000")

    # ═══════════════════════════════════════════
    #  Angel 风淋门协议
    # ═══════════════════════════════════════════

    @router.post("/acs/door/{door_id}")
    async def control_door(door_id: str, req: AngelControlRequest):
        """控制门 (POST)"""
        req_dict = req.model_dump(exclude_none=True)
        result = sim.control_door(door_id, req.command,
                                  req.Direction or "", req.RobotName or "")
        sim._log("control", door_id,
                 f"指令={req.command} dir={req.Direction or '-'} agv={req.RobotName or '-'}",
                 req_body=req_dict, resp_body=result,
                 method="POST", path=f"/acs/door/{door_id}")
        return result

    @router.get("/acs/door/{door_id}")
    async def query_door(door_id: str):
        """查询门状态 (GET)"""
        result = sim.query_door(door_id)
        sim._log("query", door_id,
                 f"状态={result.get('doorStatus','?')}",
                 req_body={"door_id": door_id}, resp_body=result,
                 method="GET", path=f"/acs/door/{door_id}")
        return result

    # ═══════════════════════════════════════════
    #  区域管控协议
    # ═══════════════════════════════════════════

    @router.post("/api/zones/enter")
    async def zone_enter(req: ZoneEnterRequest, request: Request):
        """请求进入区域"""
        req_dict = req.model_dump(exclude_none=True)
        body, status_code = sim.zone_enter(req.zone_id, req.client_id)
        resp_dict = body if status_code == 409 else body
        sim._log("zone", req.zone_id,
                 f"进入 client={req.client_id} → {status_code}",
                 req_body=req_dict, resp_body=resp_dict,
                 method="POST", path="/api/zones/enter")
        if status_code == 409:
            return ZoneEnterConflict(**body)
        return body

    @router.post("/api/zones/exit")
    async def zone_exit(req: ZoneExitRequest):
        """退出区域"""
        req_dict = req.model_dump(exclude_none=True)
        body, _ = sim.zone_exit(req.zone_id, req.client_id)
        sim._log("zone", req.zone_id,
                 f"退出 client={req.client_id}",
                 req_body=req_dict, resp_body=body,
                 method="POST", path="/api/zones/exit")
        return body

    @router.get("/api/zones/status")
    async def zone_status(zone_id: str):
        """查询区域状态"""
        result = sim.zone_status(zone_id)
        sim._log("zone", zone_id, f"查询",
                 req_body={"zone_id": zone_id}, resp_body=result,
                 method="GET", path="/api/zones/status")
        return result

    # ═══════════════════════════════════════════
    #  模拟器管理 (WebUI 调用)
    # ═══════════════════════════════════════════

    @router.get("/api/sim/status")
    async def get_sim_status():
        """获取模拟器完整状态"""
        snap = sim.snapshot()
        return snap.model_dump()

    @router.post("/api/sim/door/set")
    async def set_door_state(request: Request):
        """手动设置门状态 (支持JSON body或query params)"""
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        door_id = body.get("door_id") or request.query_params.get("door_id", "")
        state = body.get("state") or request.query_params.get("state", "0")
        sim._log("manual", door_id, f"设置门状态: {state}", req_body=body if body else {"door_id": door_id, "state": state},
                 resp_body={"status": "ok", "door_id": door_id, "state": state},
                 method="POST", path="/api/sim/door/set")
        ok = sim.manual_set_door_state(door_id, state)
        if not ok:
            raise HTTPException(400, f"未知门ID: {door_id}")
        return {"status": "ok", "door_id": door_id, "state": state}

    @router.post("/api/sim/door/fault")
    async def inject_fault(request: Request):
        """注入/清除门故障"""
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        door_id = body.get("door_id") or request.query_params.get("door_id", "")
        enable = body.get("enable", True) if "enable" in body else request.query_params.get("enable", "true") != "false"
        sim._log("manual", door_id, f"注入故障: enable={enable}", req_body=body if body else {"door_id": door_id, "enable": enable},
                 method="POST", path="/api/sim/door/fault")
        sim.manual_inject_fault(door_id, enable)
        return {"status": "ok", "door_id": door_id, "fault": enable}

    @router.post("/api/sim/zone/busy")
    async def set_zone_busy(request: Request):
        """强制设置区域占用/释放"""
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        busy = body.get("busy", True) if "busy" in body else request.query_params.get("busy", "true") != "false"
        client = body.get("client") or request.query_params.get("client", "")
        sim._log("manual", sim.zone.zone_id, f"设置区域: busy={busy} client={client}", req_body=body if body else {"busy": busy, "client": client},
                 method="POST", path="/api/sim/zone/busy")
        sim.manual_set_zone_busy(busy, client)
        return {"status": "ok", "busy": busy}

    @router.post("/api/sim/config/delays")
    async def set_delays(request: Request):
        """设置门过渡延时"""
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        open_delay = float(body.get("open_delay", "2.0") if "open_delay" in body else request.query_params.get("open_delay", "2.0"))
        close_delay = float(body.get("close_delay", "2.0") if "close_delay" in body else request.query_params.get("close_delay", "2.0"))
        sim._log("config", "delays", f"设置延时 open={open_delay}s close={close_delay}s", req_body=body if body else {"open_delay": open_delay, "close_delay": close_delay},
                 method="POST", path="/api/sim/config/delays")
        sim.manual_set_delays(open_delay, close_delay)
        return {"status": "ok", "open_delay": open_delay, "close_delay": close_delay}

    @router.post("/api/sim/reset")
    async def reset_sim():
        """重置模拟器"""
        sim.reset_all()
        return {"status": "ok", "message": "模拟器已重置"}

    @router.get("/api/sim/logs")
    async def get_logs():
        """获取请求日志"""
        return {"logs": sim.request_log[-100:]}

    return router
