"""
ASAP Adapter 主入口

启动 FastAPI Web 服务，整合所有模块：
  - 端口 5012（API + WebUI + SSE）
  - 健康检查 /actuator/health
  - RCS 对接接口
  - 风淋流程控制
  - WebUI 仪表盘
  - 内置模拟器（/sim/ 路径，WebUI 中启动/关闭）
"""

import logging
import asyncio
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
from .rcs_reporter import RcsReporter
from .state_machine import StateMachine
from .router import create_router
from .logger import setup_logging

# ── 模拟器（可选导入，不存在时降级） ──
try:
    from sim_controller.state import SimController
    from sim_controller.router import create_router as create_sim_router
    _sim_controller = SimController()
    _sim_available = True
except ImportError:
    _sim_controller = None
    _sim_available = False
    logger.warning("sim_controller 模块未安装，模拟器不可用")

logger = logging.getLogger(__name__)


def create_app(config: AppConfig) -> FastAPI:
    """创建并配置 FastAPI 应用"""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """应用生命周期：启动→运行→关闭"""
        # ── 启动 ──────────────────────────
        door = DoorClient(config.angel)
        zone = ZoneClient(config.zone)
        rcs = RcsReporter(config.rcs)
        sm = StateMachine(config, door, zone, rcs)

        # ── 模拟器状态 ────────────────────
        app.state.sim_controller = _sim_controller
        app.state.sim_enabled = False
        app.state._sim_available = _sim_available
        app.state._orig_door_base_url = config.angel.base_url

        # 初始化 SSE 事件总线
        sse_clients: list = []
        app.state.sse_clients = sse_clients

        # 初始化 RCS 请求日志（循环缓冲区，保留最近 200 条）
        app.state.request_log = []
        app.state.request_log_counter = 0

        async def _on_event(event):
            payload = event.model_dump_json()
            dead = []
            for q in sse_clients:
                try:
                    q.put_nowait(f"data: {payload}\n\n")
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                sse_clients.remove(q)

        sm.on_event = _on_event

        # 存入 app.state 供路由使用
        app.state.door = door
        app.state.zone = zone
        app.state.rcs = rcs
        app.state.sm = sm
        app.state.config = config

        logger.info("ASAP Adapter 启动完成，端口 %d", config.server.port)

        yield

        # ── 关闭 ──────────────────────────
        logger.info("ASAP Adapter 正在关闭...")
        await sm.cancel()
        await door.close()
        await zone.close()
        await rcs.close()
        logger.info("ASAP Adapter 已关闭")

    app = FastAPI(
        title="ASAP Adapter",
        description="风淋门-区域管控协议适配器",
        version="1.0.0",
        lifespan=lifespan,
    )

    # ── 路由 ──────────────────────────────
    from .router import create_router as _create_router
    router = _create_router(app)
    app.include_router(router)

    # ── 模拟器路由（可选挂载） ──
    if _sim_available:
        sim_router = create_sim_router(_sim_controller)
        app.include_router(sim_router, prefix="/sim")
        logger.info("模拟器路由已挂载到 /sim")
    else:
        logger.info("模拟器不可用，/sim 路由未挂载")

    # ── WebUI 静态文件 ───────────────────
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def index():
            return FileResponse(str(static_dir / "index.html"))

        @app.get("/upgrade")
        async def upgrade_page():
            # 升级功能已集成到主页面，/upgrade 重定向到首页
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/")

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
