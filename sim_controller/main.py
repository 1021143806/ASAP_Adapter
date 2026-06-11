"""
SimController 主入口

启动 FastAPI 模拟服务（端口 5112）。
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .state import SimController
from .router import create_router
from .logger import setup_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """创建并配置模拟器 FastAPI 应用"""

    sim = SimController()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.sim = sim
        logger.info("SimController 启动完成，端口 5112")
        yield
        logger.info("SimController 已关闭")

    app = FastAPI(
        title="SimController",
        description="风淋门/区域管控 模拟器",
        version="1.0.0",
        lifespan=lifespan,
    )

    # 路由
    router = create_router(sim)
    app.include_router(router)

    # WebUI
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def index():
            return FileResponse(str(static_dir / "index.html"))

    return app


def main():
    setup_logging()
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=5112, log_level="info")


if __name__ == "__main__":
    main()
