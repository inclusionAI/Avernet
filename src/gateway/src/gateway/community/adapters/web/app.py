"""FastAPI Web application entry point."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from gateway.community import __version__
from gateway.community.config import ConfigLoader
from gateway.community.logger import get_logger, get_logger_plugin
from gateway.community.tracer import get_tracer_plugin

logger = get_logger("webserver")

DOCS_TAGS = [
    {"name": "health", "description": "Health check endpoints."},
    {"name": "test", "description": "Test and debug endpoints."},
]


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

    enable_docs = (
        config.module_config.web.enable_api_docs if config.module_config.web else True
    )

    app = FastAPI(
        title=config.app_name,
        description="teamclawgw community edition — open-source gateway skeleton.",
        version=__version__,
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
        openapi_url="/openapi.json" if enable_docs else None,
        openapi_tags=DOCS_TAGS,
    )

    # Install tracing middleware (must happen before the app starts serving).
    tracer = get_tracer_plugin()
    tracer.setup(config.app_name)
    tracer.install_middleware(app)

    @app.get("/api/test", tags=["test"])
    async def hello() -> dict[str, str]:
        """Return a hello message."""
        logger.info("Hello endpoint called")
        return {"status": "healthy", "message": "hello, i am gw"}

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    return app


app = create_app()


def get_app() -> Any:
    """Return the configured app instance (convenience for uvicorn)."""
    return app
