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
import os
import re
from typing import AsyncGenerator, Optional
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, PlainTextResponse

from pydantic import BaseModel, Field

from .models import (
    AngelDoorStatus,
    RcsDoorControlRequest, RcsDoorControlResponse,
    RcsStatusQueryRequest, RcsStatusQueryResponse, RcsStatusData,
)
from .door_translator import AirShowerTranslator
from .door_client import DoorClient, DoorClientError
from .zone_client import ZoneClient, ZoneClientError
from .config import AppConfig


class RcsConfigUpdate(BaseModel):
    """RCS 配置更新请求"""
    change_status_url: str = ""
    report_interval: float = 0.5
    door_code_mapping: Optional[dict] = None  # {"DOOR01": "1001", "DOOR02": "1002"}


class LogQueryRequest(BaseModel):
    """日志查询请求"""
    module: Optional[str] = Field(None, description="按模块名过滤")
    level: Optional[str] = Field(None, description="按级别过滤")
    keyword: Optional[str] = Field(None, description="关键词搜索")
    limit: int = Field(100, ge=1, le=1000, description="返回条数")
    offset: int = Field(0, ge=0, description="从尾部跳过的行数")


class AngelConfigUpdate(BaseModel):
    """AB 门配置更新"""
    base_url: str = ""
    outer_door_id: str = ""
    inner_door_id: str = ""


class ZoneConfigUpdate(BaseModel):
    """区域管控配置更新"""
    enter_url: str = ""
    exit_url: str = ""
    status_url: str = ""
    entry_door_code: Optional[str] = None
    zone_poll_interval: Optional[float] = None


# ── 请求日志记录 ──────────────────────────


def _log_req(request: Request, category: str, endpoint: str,
             req_body: dict, resp_body, resp_status: int = 200, method: str = "POST"):
    """记录 RCS 请求日志到 app.state.request_log"""
    from datetime import datetime
    log = request.app.state.request_log
    ctr = request.app.state.request_log_counter
    ctr += 1
    request.app.state.request_log_counter = ctr
    entry = {
        "id": ctr,
        "time": datetime.now().strftime("%H:%M:%S.%f")[:12],
        "category": category,
        "endpoint": endpoint,
        "method": method,
        "request": req_body,
        "response": resp_body if isinstance(resp_body, dict) else str(resp_body),
        "status": resp_status,
    }
    log.append(entry)
    # 超过 200 条时丢弃最早的
    while len(log) > 200:
        log.pop(0)


class ZoneConfigUpdate(BaseModel):
    """区域管控配置更新"""
    enter_url: str = ""
    exit_url: str = ""
    status_url: str = ""

logger = logging.getLogger(__name__)


def _get_translator(request: Request) -> AirShowerTranslator:
    return request.app.state.translator


def _door_code_to_id(door_code: str, request: Request) -> str:
    """将 RCS doorCode 映射为 ACS door_id"""
    translator = _get_translator(request)
    door_id = translator._door_id_by_code(door_code)
    if door_id is None:
        logger.warning("未找到doorCode[%s]的映射", door_code)
        return request.app.state.config.angel.outer_door_id
    return door_id


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
                translator = _get_translator(request)
                status = translator.get_status()
                snapshot = json.dumps({
                    "timestamp": datetime.now().isoformat(),
                    "event_type": "snapshot",
                    "data": status.dump(),
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

    # ── RCS 门状态查询 + 控制（合并端点）─────────

    @router.post("/api/rcs/doorStatus")
    async def rcs_door_status(request: Request):
        """
        RCS 门状态查询 + 门禁控制（单一入口）
        根据请求体中是否含 status 字段区分：
          - 含 status(1/2): 控制请求 → 阻塞式
          - 仅 doorCode:    查询请求 → 即时返回
        """
        from datetime import datetime
        entry_code = request.app.state.config.zone.entry_door_code

        # 解析请求体
        data = await request.json()
        door_code = data.get("doorCode", "")
        control_status = data.get("status", 0)  # 0=查询, 1=开门, 2=关门

        req_dict = dict(data)

        # ── 控制请求（含 status=1/2） ──
        if control_status in (1, 2):
            action_label = "开门" if control_status == 1 else "关门"
            logger.info("RCS控制请求: door=%s status=%d", door_code, control_status)

            # 区域管控门 (q001)
            if door_code == entry_code:
                zsm = request.app.state.zone_sm
                if control_status == 1:
                    ok, msg = await zsm.handle_open(door_code,
                        data.get("deviceCode", ""))
                    code = 1000 if ok else 2001
                else:
                    ok, msg = await zsm.handle_close(door_code)
                    code = 1000 if ok else 2002
                resp = RcsDoorControlResponse(code=code, msg=msg)
                _log_req(request, "control", "/api/rcs/doorStatus",
                         req_dict, resp.model_dump(), 200)
                return resp

            # 风淋门 (1001/1002) — 协议翻译
            translator = _get_translator(request)
            translator._status.rcs_query_count += 1
            translator._status.rcs_last_query = (
                f"{datetime.now().strftime('%H:%M:%S')} "
                f"控制 doorCode={door_code} {action_label}"
            )
            code, msg = await translator.handle_control(
                door_code, control_status,
                data.get("deviceCode", ""))
            resp = RcsDoorControlResponse(code=code, msg=msg)
            _log_req(request, "control", "/api/rcs/doorStatus",
                     req_dict, resp.model_dump(), 200)
            return resp

        # ── 查询请求（仅 doorCode） ──
        query_req = RcsStatusQueryRequest(doorCode=door_code)
        sm_ref = getattr(request.app.state, 'translator', None)
        if sm_ref:
            sm_ref._status.rcs_query_count += 1
            sm_ref._status.rcs_last_query = (
                f"{datetime.now().strftime('%H:%M:%S')} "
                f"doorCode={door_code}"
            )

        logger.info("RCS状态查询: door=%s", door_code)

        # 区域管控门
        if door_code == entry_code:
            zsm = request.app.state.zone_sm
            try:
                rcs_status = zsm.door_status_by_code(door_code)
                resp = RcsStatusQueryResponse(data=RcsStatusData(status=rcs_status))
            except Exception as e:
                resp = RcsStatusQueryResponse(code=9999, msg=str(e))
            _log_req(request, "query", "/api/rcs/doorStatus",
                     req_dict, resp.model_dump(), 200)
            return resp

        # 风淋门 — 协议翻译
        translator = _get_translator(request)
        code, msg, data_dict = await translator.handle_query(door_code)
        if code == 1000 and data_dict:
            resp = RcsStatusQueryResponse(
                code=code, msg=msg,
                data=RcsStatusData(status=data_dict["status"]))
        else:
            resp = RcsStatusQueryResponse(code=code, msg=msg)
        _log_req(request, "query", "/api/rcs/doorStatus",
                 req_dict, resp.model_dump(), 200)
        return resp

    # ── ASAP 管理接口 ─────────────────────────

    @router.get("/api/asap/request-log")
    async def get_request_log(request: Request, limit: int = 50):
        """获取 RCS 请求日志"""
        log = request.app.state.request_log
        return {"total": len(log), "logs": log[-limit:]}

    @router.get("/api/asap/status")
    async def get_asap_status(request: Request):
        """获取风淋系统整体状态"""
        translator = _get_translator(request)
        return translator.get_status().dump()

    @router.get("/api/asap/zone-status")
    async def get_zone_status(request: Request):
        """获取区域管控状态"""
        zsm = request.app.state.zone_sm
        return zsm.status.dump()

    @router.post("/api/asap/zone/force-door")
    async def force_zone_door(request: Request):
        """
        强制设置区域管控门状态（调试/异常恢复）
        Body: {"door_code": "q001", "status": "1"}  # 1=开, 2=关
        """
        data = await request.json()
        door_code = data.get("door_code", "")
        status = str(data.get("status", "2"))
        if door_code not in ("q001", "q002", "", None):
            return {"code": 2001, "msg": f"无效门编号: {door_code}"}
        if status not in ("1", "2"):
            return {"code": 2002, "msg": f"无效状态: {status}，需为 1(开) 或 2(关)"}

        zsm = request.app.state.zone_sm
        ok = await zsm.force_door_state(door_code, status)
        if ok:
            return {"code": 1000, "msg": f"已强制设置 {door_code} 为 {'开' if status == '1' else '关'}"}
        return {"code": 2003, "msg": f"设置失败，未识别门编号: {door_code}"}

    @router.post("/api/asap/refresh-doors")
    async def refresh_doors(request: Request):
        """主动刷新门状态（向真实设备/模拟器查询当前状态，更新缓存）"""
        translator = _get_translator(request)
        door_ids = [
            translator._status.door1.door_id,
            translator._status.door2.door_id,
        ]
        results = {}
        for door_id in door_ids:
            if door_id:
                try:
                    _, _, data = await translator.handle_query(
                        translator._door_code_by_id(door_id))
                    if data:
                        results[door_id] = data
                except Exception:
                    results[door_id] = {"error": "query failed"}
        return {
            "door1": translator._status.door1.__dict__,
            "door2": translator._status.door2.__dict__,
            "results": results,
        }

    # ── RCS 配置管理 ──────────────────────────

    @router.get("/api/asap/config/rcs")
    async def get_rcs_config(request: Request):
        """获取 RCS 配置"""
        rcs = request.app.state.rcs
        return {
            "change_status_url": rcs.config.change_status_url,
            "report_interval": rcs.config.report_interval,
            "door_code_mapping": rcs.config.door_code_mapping,
        }

    @router.post("/api/asap/config/rcs")
    async def update_rcs_config(request: Request, cfg: RcsConfigUpdate):
        """更新 RCS 配置（运行时生效，持久化到 overrides.json）"""
        rcs = request.app.state.rcs
        rcs.config.change_status_url = cfg.change_status_url
        if cfg.report_interval > 0:
            rcs.config.report_interval = cfg.report_interval
        # 门编码映射
        if cfg.door_code_mapping is not None:
            rcs.config.door_code_mapping = cfg.door_code_mapping
        # 持久化到 overrides.json（重启后保留）
        from .config import save_override
        save_override("rcs", "change_status_url", cfg.change_status_url)
        if cfg.report_interval > 0:
            save_override("rcs", "report_interval", cfg.report_interval)
        if cfg.door_code_mapping is not None:
            save_override("rcs", "door_code_mapping", cfg.door_code_mapping)
        logger.info("RCS配置已更新并持久化: change_status_url=%s mapping=%s",
                     cfg.change_status_url, cfg.door_code_mapping)
        return {
            "status": "ok",
            "change_status_url": rcs.config.change_status_url,
            "report_interval": rcs.config.report_interval,
            "door_code_mapping": rcs.config.door_code_mapping,
        }

    # ── AB 门配置管理 ───────────────────────

    @router.get("/api/asap/config/angel")
    async def get_angel_config(request: Request):
        """获取 AB 门配置"""
        cfg = request.app.state.config
        return {
            "base_url": cfg.angel.base_url,
            "outer_door_id": cfg.angel.outer_door_id,
            "inner_door_id": cfg.angel.inner_door_id,
        }

    @router.post("/api/asap/config/angel")
    async def update_angel_config(request: Request, cfg: AngelConfigUpdate):
        """更新 AB 门配置（运行时生效，持久化到 overrides.json）"""
        config = request.app.state.config
        changed = False
        from .config import save_override

        if cfg.base_url:
            config.angel.base_url = cfg.base_url
            save_override("angel", "base_url", cfg.base_url)
            request.app.state.door.set_sim_mode(False)
            changed = True
        if cfg.outer_door_id:
            config.angel.outer_door_id = cfg.outer_door_id
            save_override("angel", "outer_door_id", cfg.outer_door_id)
            changed = True
        if cfg.inner_door_id:
            config.angel.inner_door_id = cfg.inner_door_id
            save_override("angel", "inner_door_id", cfg.inner_door_id)
            changed = True

        # 同步门ID到模拟器
        if changed and request.app.state._sim_available and request.app.state.sim_controller:
            request.app.state.sim_controller.set_door_ids(
                config.angel.outer_door_id,
                config.angel.inner_door_id,
            )

        if changed:
            logger.info("AB门配置已更新: base_url=%s, outer=%s, inner=%s",
                        config.angel.base_url, config.angel.outer_door_id, config.angel.inner_door_id)
        return {
            "status": "ok",
            "base_url": config.angel.base_url,
            "outer_door_id": config.angel.outer_door_id,
            "inner_door_id": config.angel.inner_door_id,
        }

    # ── 区域管控配置管理 ────────────────────

    @router.get("/api/asap/config/zone")
    async def get_zone_config(request: Request):
        """获取区域管控配置"""
        cfg = request.app.state.config
        return {
            "enter_url": cfg.zone.enter_url,
            "exit_url": cfg.zone.exit_url,
            "status_url": cfg.zone.status_url,
            "entry_door_code": cfg.zone.entry_door_code,
            "zone_poll_interval": cfg.zone.zone_poll_interval,
        }

    @router.post("/api/asap/config/zone")
    async def update_zone_config(request: Request, cfg: ZoneConfigUpdate):
        """更新区域管控配置（运行时生效，持久化到 overrides.json）"""
        config = request.app.state.config
        if cfg.enter_url:
            config.zone.enter_url = cfg.enter_url
            from .config import save_override
            save_override("zone", "enter_url", cfg.enter_url)
        if cfg.exit_url:
            config.zone.exit_url = cfg.exit_url
            from .config import save_override
            save_override("zone", "exit_url", cfg.exit_url)
        if cfg.status_url:
            config.zone.status_url = cfg.status_url
            from .config import save_override
            save_override("zone", "status_url", cfg.status_url)
        if cfg.zone_poll_interval is not None and cfg.zone_poll_interval > 0:
            config.zone.zone_poll_interval = cfg.zone_poll_interval
            from .config import save_override
            save_override("zone", "zone_poll_interval", cfg.zone_poll_interval)
        if cfg.entry_door_code is not None:
            config.zone.entry_door_code = cfg.entry_door_code
            from .config import save_override
            save_override("zone", "entry_door_code", cfg.entry_door_code)
        logger.info("区域管控配置已更新")
        return {
            "status": "ok",
            "enter_url": config.zone.enter_url,
            "exit_url": config.zone.exit_url,
            "status_url": config.zone.status_url,
            "entry_door_code": config.zone.entry_door_code,
            "zone_poll_interval": config.zone.zone_poll_interval,
        }

    # ── 日志查询 ──────────────────────────────

    @router.post("/api/asap/logs")
    async def query_logs(req: LogQueryRequest, request: Request):
        """查询运行日志（支持按模块/级别/关键词过滤）"""
        # 日志文件路径: 相对路径基于项目根目录（app.state.config 中存储）
        app_cfg = request.app.state.config
        log_path_str = app_cfg.log.file
        log_file = Path(log_path_str)
        if not log_file.is_absolute():
            # 项目根目录 = config.py 所在目录的父目录
            base_dir = Path(__file__).resolve().parent.parent
            log_file = base_dir / log_file

        if not log_file.exists():
            return {"total": 0, "lines": []}

        # 日志行正则: 时间 | 级别 | 模块 | 消息
        log_pattern = re.compile(
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| (\w+) \s*\| (.+?) \s*\| (.+)$"
        )

        raw_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()

        # 从尾部 offset 开始取
        start = max(0, len(raw_lines) - req.offset - req.limit)
        end = len(raw_lines) - req.offset if req.offset > 0 else len(raw_lines)
        candidate_lines = raw_lines[start:end]

        matched = []
        for line in candidate_lines:
            m = log_pattern.match(line)
            if not m:
                continue
            level, module, message = m.group(1), m.group(2), m.group(3)

            # 过滤级别
            if req.level and level.upper() != req.level.upper():
                continue
            # 过滤模块（子串匹配）
            if req.module and req.module.lower() not in module.lower():
                continue
            # 关键词搜索
            if req.keyword and req.keyword.lower() not in message.lower():
                continue

            matched.append({
                "line": line,
                "level": level,
                "module": module,
                "message": message,
            })

        return {"total": len(matched), "lines": matched[-req.limit:]}

    # ── 模拟器控制 ──────────────────────────

    # ── 模拟器开关 ────────────────────────────

    @router.get("/api/asap/config/all")
    async def get_unified_config(request: Request):
        """获取统一配置（/data/config.toml）"""
        from .config import read_unified_config
        return read_unified_config()

    @router.post("/api/asap/config/all")
    async def save_unified_config(request: Request):
        """保存统一配置到 /data/config.toml（自动版本递增，热更新）"""
        from .config import save_unified_config, apply_runtime_string, UNIFIED_CONFIG_PATH, read_unified_config
        data = await request.json()
        result = save_unified_config(data)
        if result.get("success"):
            # 热更新到内存
            try:
                raw = read_unified_config().get("raw", "")
                if raw:
                    apply_runtime_string(request.app.state.config, raw)
            except Exception as e:
                logger.warning("热更新统一配置失败: %s", e)
            # 同步到 SimController
            sim_ctrl = getattr(request.app.state, 'sim_controller', None)
            if sim_ctrl:
                sim_ctrl.config.auto_open_delay = request.app.state.config.sim.auto_open_delay
                sim_ctrl.config.auto_close_delay = request.app.state.config.sim.auto_close_delay
                sim_ctrl.config.zone_always_busy = request.app.state.config.sim.zone_always_busy
                sim_ctrl.zone.zone_id = request.app.state.config.sim.zone_id
        return result

    @router.post("/api/asap/sim/enable")
    async def sim_enable(request: Request):
        """启用模拟器模式（DoorClient/ZoneClient 重定向到本地模拟端点）"""
        if not request.app.state._sim_available:
            return {"status": "error", "message": "模拟器模块未安装"}
        if request.app.state.sim_enabled:
            return {"status": "ok", "message": "模拟器已启用"}
        door: DoorClient = request.app.state.door
        zone: ZoneClient = request.app.state.zone
        config: AppConfig = request.app.state.config
        # 保存原始 URL（仅首次）
        if not hasattr(request.app.state, "_orig_zone_cfg"):
            request.app.state._orig_zone_cfg = {
                "enter_url": config.zone.enter_url,
                "exit_url": config.zone.exit_url,
                "status_url": config.zone.status_url,
            }
        # 重定向 door 到本地模拟端点
        door.set_sim_mode(True)
        # 重定向 zone 到本地模拟端点
        config.zone.enter_url = f"http://127.0.0.1:{config.server.port}/sim/api/zones/enter"
        config.zone.exit_url = f"http://127.0.0.1:{config.server.port}/sim/api/zones/exit"
        config.zone.status_url = f"http://127.0.0.1:{config.server.port}/sim/api/zones/status"
        # 重置模拟器状态
        request.app.state.sim_controller.reset_all()
        request.app.state.sim_enabled = True
        logger.info("模拟器已启用")
        return {"status": "ok", "message": "模拟器已启用，Door/Zone 已重定向到本地仿真"}

    @router.post("/api/asap/sim/disable")
    async def sim_disable(request: Request):
        """关闭模拟器模式（恢复生产环境配置）"""
        if not request.app.state.sim_enabled:
            return {"status": "ok", "message": "模拟器已关闭"}
        door: DoorClient = request.app.state.door
        config: AppConfig = request.app.state.config
        # 恢复原始 door URL
        door.set_sim_mode(False)
        # 恢复原始 zone URL
        orig = getattr(request.app.state, "_orig_zone_cfg", None)
        if orig:
            config.zone.enter_url = orig["enter_url"]
            config.zone.exit_url = orig["exit_url"]
            config.zone.status_url = orig["status_url"]
        request.app.state.sim_enabled = False
        logger.info("模拟器已关闭，恢复生产环境配置")
        return {"status": "ok", "message": "模拟器已关闭，已恢复生产环境配置"}

    @router.get("/api/asap/sim/status")
    async def sim_status(request: Request):
        """获取模拟器状态（是否启用、模拟器快照）"""
        enabled = request.app.state.sim_enabled
        if request.app.state._sim_available:
            snap = request.app.state.sim_controller.snapshot()
            return {
                "enabled": enabled,
                "available": True,
                "simulator": snap.model_dump(),
            }
        return {"enabled": False, "available": False, "message": "模拟器模块未安装"}

    # ── 模拟器配置管理 ─────────────────────────

    @router.get("/api/asap/config/sim")
    async def get_sim_config(request: Request):
        """获取模拟器配置"""
        cfg = request.app.state.config
        sim_ctrl = getattr(request.app.state, 'sim_controller', None)
        sim_snap = sim_ctrl.snapshot() if sim_ctrl and getattr(request.app.state, 'sim_enabled', False) else None
        return {
            "auto_open_delay": cfg.sim.auto_open_delay,
            "auto_close_delay": cfg.sim.auto_close_delay,
            "zone_always_busy": cfg.sim.zone_always_busy,
            "zone_id": cfg.sim.zone_id,
            "enabled": getattr(request.app.state, 'sim_enabled', False),
            "available": getattr(request.app.state, '_sim_available', False),
            "sim_config": sim_snap.config.model_dump() if sim_snap else None,
        }

    class SimConfigUpdate(BaseModel):
        auto_open_delay: Optional[float] = None
        auto_close_delay: Optional[float] = None
        zone_always_busy: Optional[bool] = None
        zone_id: Optional[str] = None

    @router.post("/api/asap/config/sim")
    async def update_sim_config(request: Request, cfg: SimConfigUpdate):
        """更新模拟器配置（运行时生效 + 持久化）"""
        config = request.app.state.config
        sim_ctrl = getattr(request.app.state, 'sim_controller', None)

        if cfg.auto_open_delay is not None and cfg.auto_open_delay > 0:
            config.sim.auto_open_delay = cfg.auto_open_delay
            if sim_ctrl:
                sim_ctrl.config.auto_open_delay = cfg.auto_open_delay
            from .config import save_override
            save_override("sim", "auto_open_delay", cfg.auto_open_delay)
        if cfg.auto_close_delay is not None and cfg.auto_close_delay > 0:
            config.sim.auto_close_delay = cfg.auto_close_delay
            if sim_ctrl:
                sim_ctrl.config.auto_close_delay = cfg.auto_close_delay
            from .config import save_override
            save_override("sim", "auto_close_delay", cfg.auto_close_delay)
        if cfg.zone_always_busy is not None:
            config.sim.zone_always_busy = cfg.zone_always_busy
            if sim_ctrl:
                sim_ctrl.config.zone_always_busy = cfg.zone_always_busy
            from .config import save_override
            save_override("sim", "zone_always_busy", cfg.zone_always_busy)
        if cfg.zone_id is not None:
            config.sim.zone_id = cfg.zone_id
            if sim_ctrl:
                sim_ctrl.zone.zone_id = cfg.zone_id
            from .config import save_override
            save_override("sim", "zone_id", cfg.zone_id)

        logger.info("模拟器配置已更新")
        return {
            "status": "ok",
            "auto_open_delay": config.sim.auto_open_delay,
            "auto_close_delay": config.sim.auto_close_delay,
            "zone_always_busy": config.sim.zone_always_busy,
            "zone_id": config.sim.zone_id,
        }

    # ── 升级管理 ──────────────────────────────

    @router.get("/api/asap/upgrade/version")
    async def get_upgrade_version():
        """获取当前版本信息"""
        from . import upgrade_service as us
        return {"success": True, "info": us.get_version_info()}

    @router.get("/api/asap/upgrade/records")
    async def get_upgrade_records():
        """获取升级记录"""
        from . import upgrade_service as us
        return {"success": True, "records": us.get_upgrade_records(),
                "exclude_patterns": us.get_exclude_patterns(),
                "max_backups": us.MAX_BACKUPS}

    @router.post("/api/asap/upgrade/upload")
    async def upload_upgrade(request: Request):
        """上传并执行升级包"""
        import tempfile
        import traceback
        from . import upgrade_service as us

        form = await request.form()
        file = form.get("file")
        if not file:
            raise HTTPException(status_code=400, detail="缺少 file 字段")

        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="仅支持 .zip 文件")

        remark = form.get("remark", "").strip()

        # 保存到临时文件
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
        os.close(tmp_fd)
        try:
            content = await file.read()
            with open(tmp_path, "wb") as f:
                f.write(content)
        except Exception as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            logger.error("保存上传文件失败: %s", traceback.format_exc())
            raise HTTPException(status_code=500, detail=f"保存上传文件失败: {str(e)}")

        try:
            result = us.do_upgrade(tmp_path, remark=remark)
        except Exception as e:
            logger.error("执行升级异常: %s", traceback.format_exc())
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise HTTPException(status_code=500, detail=f"执行升级异常: {str(e)}")

        if result["success"]:
            # 直接退出进程，依靠 supervisor autorestart 拉起（不依赖 upgrade_service 的缓存代码）
            import threading as _t
            _t.Thread(target=lambda: (_t.Event().wait(3), os._exit(0)), daemon=True).start()
            return {"success": True, "message": result["message"],
                    "backup": result.get("backup", "")}
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "升级失败"))

    # ── 配置文件直接编辑 ──────────────────────

    class ConfigFileContent(BaseModel):
        content: str

    # ── 运行时配置（热更新） ────────────────

    @router.get("/api/asap/config/runtime")
    async def get_runtime_config():
        """获取运行时配置 (config/runtime.toml) — 修改即时生效"""
        from .config import read_runtime
        content = read_runtime()
        return {"success": True, "content": content}

    @router.post("/api/asap/config/runtime")
    async def save_runtime_config(req: ConfigFileContent, request: Request):
        """保存运行时配置 — 写入后自动热更新，无需重启"""
        from .config import save_runtime, apply_runtime_string
        result = save_runtime(req.content)
        if not result.get("success"):
            return result
        try:
            # 热更新到内存
            apply_runtime_string(request.app.state.config, req.content)
            # 重建 DoorClient（base_url 变化时 httpx client 需重建）
            request.app.state.door.set_sim_mode(False)
            # 同步门ID到模拟器
            if request.app.state._sim_available and request.app.state.sim_controller:
                request.app.state.sim_controller.set_door_ids(
                    request.app.state.config.angel.outer_door_id,
                    request.app.state.config.angel.inner_door_id,
                )
            logger.info("运行时配置已热更新: base_url=%s, doors=%s/%s",
                        request.app.state.config.angel.base_url,
                        request.app.state.config.angel.outer_door_id,
                        request.app.state.config.angel.inner_door_id)
            result["hot_reloaded"] = True
        except Exception as e:
            logger.error("运行时配置热更新失败: %s", e)
            result["hot_reloaded"] = False
            result["warning"] = f"文件已保存，但热更新部分失败: {e}"
        return result

    # ── 静态配置（需重启） ──────────────────

    @router.get("/api/asap/config/env")
    async def get_env_config():
        """获取静态配置 (config/env.toml) — 修改需重启服务"""
        from .config import read_env
        content = read_env()
        return {"success": True, "content": content}

    @router.post("/api/asap/config/env")
    async def save_env_config(req: ConfigFileContent):
        """保存静态配置 — 写入后需重启服务生效"""
        from .config import save_env
        return save_env(req.content)

    # ── 向后兼容：旧 /api/asap/config/file 指向静态配置 ──

    @router.get("/api/asap/config/file")
    async def get_config_file():
        """[兼容] 获取静态配置 (config/env.toml)"""
        from .config import read_env
        content = read_env()
        return {"success": True, "content": content}

    @router.post("/api/asap/config/file")
    async def save_config_file(req: ConfigFileContent):
        """[兼容] 保存静态配置"""
        from .config import save_env
        return save_env(req.content)

    return router
