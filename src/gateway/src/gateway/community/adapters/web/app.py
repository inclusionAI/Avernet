"""FastAPI Web application entry point.

The gateway serves no hand-written per-operation routes: one catch-all forwards
every ``/openapi/v1`` request to its domain's upstream, and ``/openapi.json`` is
generated from each upstream's published description (via the schema catalog).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from gateway.community import __version__
from gateway.community.adapters.web._forward import _ALL_METHODS, forward_request
from gateway.community.config import ConfigLoader
from gateway.community.logger import get_logger, get_logger_plugin
from gateway.community.tracer import get_tracer_plugin

logger = get_logger("webserver")

DOCS_TAGS = [
    {"name": "health", "description": "Health check endpoints."},
    {"name": "test", "description": "Test and debug endpoints."},
]

_API_DESCRIPTION = "Avernet gateway — config-driven forwarding surface."


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

    # Wire the composition root. Imported lazily inside the factory so the adapters
    # layer keeps no *static* dependency on bootstrap — function-body imports are
    # exempt from the layer-boundary check by design (composition at call time).
    from gateway.community.bootstrap import build_authenticator, build_forwarding

    authenticator = build_authenticator()
    forwarding = build_forwarding()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await forwarding.start_refresh()
        try:
            yield
        finally:
            await forwarding.stop_refresh()

    app = FastAPI(
        title=config.app_name,
        description="teamclawgw community edition — config-driven gateway.",
        version=__version__,
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
        openapi_url="/openapi.json" if enable_docs else None,
        openapi_tags=DOCS_TAGS,
        lifespan=lifespan,
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

    # Hand the composed subsystems to the delivery layer via app.state.
    app.state.authenticator = authenticator
    app.state.domain_map = forwarding.domain_map
    app.state.forwarder = forwarding.forwarder

    # Serve the generated OpenAPI (config ⋈ each domain's published description)
    # instead of FastAPI's route introspection. Regenerated per call so it tracks
    # the catalog's background refresh.
    def _served_openapi() -> dict[str, Any]:
        return forwarding.served_openapi(
            authenticator.route_security,
            title=config.app_name,
            version=__version__,
            description=_API_DESCRIPTION,
        )

    app.openapi = _served_openapi  # type: ignore[method-assign]

    # The catch-all forwarding entrypoint — registered last so gateway-local
    # routes (health, test, docs) win. Excluded from the generated schema.
    app.add_api_route(
        "/{full_path:path}",
        forward_request,
        methods=_ALL_METHODS,
        include_in_schema=False,
    )

    return app


app = create_app()


def get_app() -> Any:
    """Return the configured app instance (convenience for uvicorn)."""
    return app
