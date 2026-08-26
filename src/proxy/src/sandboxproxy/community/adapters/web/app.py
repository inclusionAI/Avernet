"""FastAPI application factory for the sandbox-proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sandboxproxy.community.api.identity import resolve_instance_id
from sandboxproxy.community.bootstrap import (
    ApplicationContainer,
    initialize_services,
    shutdown_services,
)
from sandboxproxy.community.config import Config
from sandboxproxy.community.logger import get_logger

logger = get_logger("app")

_CONTAINER: ApplicationContainer | None = None


def get_container() -> ApplicationContainer:
    global _CONTAINER
    if _CONTAINER is None:
        _CONTAINER = bootstrap_app(return_container=True)  # pragma: no cover
    return _CONTAINER


def bootstrap_app(*, return_container: bool = False) -> Any:
    from sandboxproxy.community.config import ConfigLoader
    from sandboxproxy.community.logger import get_logger_plugin

    loaded = ConfigLoader.load()

    logger_plugin = get_logger_plugin()
    logger_plugin.configure(
        log_level=loaded.log_config.log_level or "INFO",
        log_dir=loaded.log_config.log_dir or "",
        app_name=loaded.app_name or "sandboxproxy",
        trace_log_dir=getattr(loaded.log_config, "trace_log_dir", "") or "",
    )

    logger.info("config loaded: app_name=%s", loaded.app_name)

    container = ApplicationContainer()
    container.config.from_dict(_container_config_dict(loaded))
    initialize_services(container)

    if return_container:
        return container

    app = build_app(container, loaded)
    return app


def _container_config_dict(loaded: Config) -> dict[str, Any]:
    from sandboxproxy.community.api.identity import resolve_worker_pid

    user_config = loaded.user_config
    return {
        "user_config": user_config.model_dump(),
        "plugins": {
            "resolver": user_config.plugins.resolver,
            "relay_client": user_config.plugins.relay_client,
        },
        "instance": resolve_instance_id(),
        "worker_pid": resolve_worker_pid(),
        "socket_path": None,
    }


def build_app(container: ApplicationContainer, loaded: Config) -> FastAPI:
    from sandboxproxy.community.adapters.web.routes import build_router

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        relay_client = container.relay_client()
        relay_server = container.relay_server()
        forwarding = container.forwarding()
        target_resolver = container.target_resolver()

        await relay_client.start()
        await forwarding.start()
        await relay_server.start()
        resolver_start = getattr(target_resolver, "start", None)
        if resolver_start is not None:
            await resolver_start()
        logger.info("app started")
        try:
            yield
        finally:
            resolver_shutdown = getattr(target_resolver, "shutdown", None)
            if resolver_shutdown is not None:
                await resolver_shutdown()
            await relay_server.shutdown()
            await forwarding.shutdown()
            await relay_client.shutdown()
            shutdown_services(container)
            logger.info("app shut down")

    app = FastAPI(
        title="sandboxproxy",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.container = container
    app.state.config = loaded

    cors_config = loaded.user_config.proxy.cors
    if cors_config.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_config.allowed_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            max_age=cors_config.max_age,
        )

    app.include_router(build_router(container, loaded))
    return app


app = bootstrap_app()
