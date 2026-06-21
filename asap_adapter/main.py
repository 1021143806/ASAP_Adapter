"""
ASAP Adapter 主入口

启动 FastAPI Web 服务，整合所有模块：
   - 端口 5012（API + WebUI + 请求日志）
  - 健康检查 /actuator/health
  - RCS 对接接口
  - 风淋流程控制
  - WebUI 仪表盘
  - 内置模拟器（/sim/ 路径，WebUI 中启动/关闭）
"""

import logging
import json
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .config import load_config, AppConfig
from .door_client import DoorClient
from .zone_client import ZoneClient
from .door_translator import AirShowerTranslator
from .zone_state_machine import ZoneStateMachine
from .router import create_router
from .logger import setup_logging

# ── 模拟器（可选导入，不存在时降级） ──
try:
    from sim_controller.state import SimController
    from sim_controller.router import create_router as create_sim_router
    _sim_controller = None  # 在 create_app 中初始化
    _sim_available = True
except ImportError:
    _sim_controller = None
    _sim_available = False
    logger.warning("sim_controller 模块未安装，模拟器不可用")

logger = logging.getLogger(__name__)


def create_app(config: AppConfig) -> FastAPI:
    """创建并配置 FastAPI 应用"""

    # ── 模拟器初始化（使用配置的门ID） ──
    if _sim_available:
        from sim_controller.state import SimController as _SimCtor
        _app_sim = _SimCtor(
            outer_door_id=config.angel.outer_door_id,
            inner_door_id=config.angel.inner_door_id,
            zone_id=config.sim.zone_id,
        )
    else:
        _app_sim = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """应用生命周期：启动→运行→关闭"""
        # ── 统一请求日志（循环缓冲区，保留最近 500 条）──
        request_log = []
        app.state.request_log = request_log
        app.state.request_log_counter = 0

        # ── 客户端 ──────────────────────────
        door = DoorClient(config.angel)
        door.set_log_target(request_log)
        zone = ZoneClient(config.zone)
        zone.set_log_target(request_log)
        translator = AirShowerTranslator(config, door)
        zsm = ZoneStateMachine(config, zone)

        # ── 模拟器状态 ────────────────────
        app.state.sim_controller = _app_sim
        app.state.sim_enabled = False
        app.state._sim_available = _sim_available

        # 保存 zone 原始配置（用于关闭模拟器时恢复）
        app.state._orig_zone_cfg = {
            "enter_url": config.zone.enter_url,
            "exit_url": config.zone.exit_url,
            "status_url": config.zone.status_url,
        }
        app.state._orig_door_base_url = config.angel.base_url

        # 存入 app.state 供路由使用
        app.state.door = door
        app.state.zone = zone
        app.state.translator = translator
        app.state.zone_sm = zsm
        app.state.config = config

        # ── 后台区域状态轮询 ──────────────
        async def _zone_poll_loop():
            """定时轮询区域管控状态，检测外部释放"""
            await asyncio.sleep(5)  # 启动后稍等再开始
            while True:
                try:
                    interval = config.zone.zone_poll_interval
                    if interval <= 0:
                        interval = 300
                    if config.zone.status_url:
                        status = await zone.get_status()
                        zsm._status.zone_status = status.status
                        zsm._status.zone_occupied_by = status.occupied_by
                        zsm._status.last_check = datetime.now().isoformat()

                        # 如果状态机在 INSIDE 但区域被外部释放 → 重置本地状态
                        if (zsm.state.value == "inside"
                                and status.status == "available"
                                and not status.occupied_by):
                            logger.info("区域被外部释放(%s), 重置门状态为关闭", config.zone.zone_id)
                            try:
                                await zsm.cancel()
                            except Exception:
                                pass
                            zsm._status.door_status = "2"
                            zsm._publish()

                        logger.debug("区域状态轮询: %s → %s (by %s)",
                                     config.zone.zone_id, status.status, status.occupied_by)
                except Exception as e:
                    logger.warning("区域状态轮询异常: %s", e)
                await asyncio.sleep(interval)

        app.state._zone_poll_task = asyncio.create_task(_zone_poll_loop())

        logger.info("ASAP Adapter 启动完成，端口 %d", config.server.port)

        yield

        # ── 关闭 ──────────────────────────
        logger.info("ASAP Adapter 正在关闭...")
        # 取消后台轮询任务
        if hasattr(app.state, '_zone_poll_task'):
            app.state._zone_poll_task.cancel()
        await translator.cancel()
        await zsm.cancel()
        await door.close()
        await zone.close()
        logger.info("ASAP Adapter 已关闭")

    app = FastAPI(
        title="ASAP Adapter",
        description="风淋门-区域管控协议适配器",
        version="3.3.0",
        lifespan=lifespan,
    )

    # ── 路由 ──────────────────────────────
    from .router import create_router as _create_router
    router = _create_router(app)
    app.include_router(router)

    # ── 模拟器路由（可选挂载） ──
    if _sim_available and _app_sim:
        sim_router = create_sim_router(_app_sim)
        app.include_router(sim_router, prefix="/sim")
        logger.info("模拟器路由已挂载到 /sim, 门ID: %s/%s",
                     config.angel.outer_door_id, config.angel.inner_door_id)
    else:
        logger.info("模拟器不可用，/sim 路由未挂载")

    # ── WebUI 静态文件 ───────────────────
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def index():
            return FileResponse(
                str(static_dir / "index.html"),
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        @app.get("/zone")
        async def zone_page():
            return FileResponse(
                str(static_dir / "zone.html"),
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        @app.get("/config")
        async def config_page():
            return FileResponse(
                str(static_dir / "config.html"),
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        @app.get("/upgrade")
        async def upgrade_page():
            return FileResponse(
                str(static_dir / "upgrade.html"),
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        @app.get("/logs")
        async def logs_page():
            return FileResponse(
                str(static_dir / "logs.html"),
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        # 模拟器 WebUI（仅 sim_available 时挂载）
        if _sim_available:
            sim_static_dir = Path(__file__).parent.parent / "sim_controller" / "static"
            if sim_static_dir.exists():
                @app.get("/sim")
                async def sim_page():
                    return FileResponse(str(sim_static_dir / "index.html"))

    return app


def main():
    """入口函数"""
    config = load_config()
    setup_logging(config.log)
    app = create_app(config)

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        reload=config.server.reload,
        log_level=config.log.level.lower(),
    )


if __name__ == "__main__":
    main()
