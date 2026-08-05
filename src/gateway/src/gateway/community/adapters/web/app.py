"""FastAPI Web application entry point.

The gateway serves no hand-written per-operation routes: one catch-all forwards
every ``/openapi/v1`` request to its domain's upstream, one WebSocket route is
mounted per domain declaring the socket plane (so which prefix it answers is
configuration, not code), and ``/openapi.json`` is generated from each
upstream's published description (via the schema catalog).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from gateway.community import __version__
from gateway.community.adapters.web._forward import _ALL_METHODS, forward_request
from gateway.community.adapters.web._log_redaction import install_credential_redaction
from gateway.community.adapters.web._relay_ws import forward_websocket, relay_routes
from gateway.community.adapters.web.admin import router as admin_router
from gateway.community.config import ConfigLoader
from gateway.community.logger import get_logger, get_logger_plugin
from gateway.community.tracer import get_tracer_plugin

logger = get_logger("webserver")

DOCS_TAGS = [
    {"name": "health", "description": "Health check endpoints."},
    {"name": "test", "description": "Test and debug endpoints."},
]

_API_DESCRIPTION = "Avernet Gateway — A configuration-driven forwarding plane (UNDER ACTIVE DEVELOPMENT)."


def create_app() -> FastAPI:
    """Build and configure the gateway FastAPI application."""
    config = ConfigLoader.load()

    get_logger_plugin().configure(
        log_level=config.log_config.log_level,
        log_dir=config.log_config.log_dir,
        app_name=config.app_name,
        trace_log_dir=config.log_config.trace_log_dir,
    )

    # Before anything is served: a socket credential travels in the query string
    # (a WebSocket client cannot set headers), and uvicorn logs the request
    # target with its query on every handshake. Installed here rather than in a
    # runner plugin so every runner gets it, bare and otherwise.
    install_credential_redaction()

    enable_docs = (
        config.module_config.web.enable_api_docs if config.module_config.web else True
    )

    from gateway.community.bootstrap import bootstrap_app

    bs = bootstrap_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await bs.forwarding.start_refresh()
        try:
            yield
        finally:
            await bs.forwarding.stop_refresh()
            bs.shutdown()

    app = FastAPI(
        title=config.app_name,
        description="Avernet gateway community edition — config-driven gateway.",
        version=__version__,
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
        openapi_url="/openapi.json" if enable_docs else None,
        openapi_tags=DOCS_TAGS,
        lifespan=lifespan,
        swagger_ui_parameters={"supportedSubmitMethods": []},
    )

    tracer = get_tracer_plugin()
    tracer.setup(config.app_name)
    tracer.install_middleware(app)

    @app.get(
        "/api/test",
        tags=["test"],
        description="Test endpoint to verify API connectivity",
    )
    async def hello() -> dict[str, str]:
        logger.info("Hello endpoint called")
        return {"status": "healthy", "message": "hello, i am gw"}

    @app.get(
        "/health",
        tags=["health"],
        description="Kubernetes liveness probe endpoint for container orchestration",
    )
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.state.authenticator = bs.authenticator
    app.state.domain_map = bs.forwarding.domain_map
    app.state.forwarder = bs.forwarding.forwarder
    app.state.ws_forwarder = bs.forwarding.ws_forwarder
    app.state.principal_signer = bs.principal_signer
    app.state.access_key_issuer = bs.access_key_issuer
    app.state.app_registrar = bs.app_registrar

    _default_openapi = app.openapi

    def _served_openapi() -> dict[str, Any]:
        return bs.served_openapi(
            title=config.app_name,
            version=__version__,
            description=_API_DESCRIPTION,
        )

    app.openapi = _served_openapi  # type: ignore[method-assign]

    # Admin endpoints (credential issuance/registration) — explicit routes, so
    # they win over the catch-all forward. Unauthenticated (single-box/dev only).
    app.include_router(admin_router, prefix="/admin")

    # One socket entrypoint per domain declaring the websocket protocol. Driven
    # from configuration, so no domain is named here; a domain that declares
    # only http gets no socket route, and a socket domain is refused by the HTTP
    # catch-all below.
    domain_map = bs.forwarding.domain_map
    for domain in domain_map.websocket_domains():
        for route in relay_routes(domain.mount_prefix):
            app.add_api_websocket_route(route, forward_websocket)

    app.add_api_route(
        "/{full_path:path}",
        forward_request,
        methods=_ALL_METHODS,
        include_in_schema=False,
    )

    return app


app = create_app()


def get_app() -> Any:
    return app
