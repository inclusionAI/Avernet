"""FastAPI Web application entry point (community edition).

Mirrors the original ``teamclawgw/servers/web/app.py`` but swaps the
``sofapy_base`` calls for SPI accessors so the open-source package has no
framework dependency: app creation, logging and tracing all resolve through
the ``gateway.community`` plugin layer.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from gateway.community.config import ConfigLoader
from gateway.community.logger import get_logger, get_logger_plugin
from gateway.community.tracer import get_tracer_plugin

logger = get_logger("webserver")


def create_app() -> FastAPI:
    """Build and configure the gateway FastAPI application."""
    config = ConfigLoader.load()

    # Configure logging early so startup messages are visible.
    get_logger_plugin().configure(
        log_level=config.log_config.log_level,
        log_dir=config.log_config.log_dir,
        app_name=config.app_name,
        trace_log_dir=config.log_config.trace_log_dir,
    )

    app = FastAPI(title=config.app_name)

    # Install tracing middleware (must happen before the app starts serving).
    tracer = get_tracer_plugin()
    tracer.setup(config.app_name)
    tracer.install_middleware(app)

    @app.get("/api/test")
    async def hello() -> dict[str, str]:
        """Return a hello message."""
        logger.info("Hello endpoint called")
        return {"status": "healthy", "message": "hello, i am gw"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    return app


app = create_app()


def get_app() -> Any:
    """Return the configured app instance (convenience for uvicorn)."""
    return app
