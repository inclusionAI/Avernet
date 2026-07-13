"""FastAPI Web application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from secbaas.community.adapters.web.routers.admin import (
    admin_api_gateway_router,
    publish_admin_router,
)
from secbaas.community.adapters.web.routers.bcn_downlink import (
    bcn_downlink_router,
    bcn_exception_handler,
)
from secbaas.community.adapters.web.routers.bot_service import (
    bot_cmd_router,
    bot_http_conn_router,
    bot_http_router,
    bot_management_router,
    bot_open_folder_router,
    bot_wss_router,
    callback_router,
    publish_router,
)
from secbaas.community.adapters.web.routers.bot_service.bot_start_progress_router import (
    router as bot_start_progress_router,
)
from secbaas.community.adapters.web.routers.config_management import (
    api_gateway_router,
    bot_qpm_router,
    device_template_router,
    system_config_router,
    tenant_router,
)
from secbaas.community.adapters.web.routers.health_checker import (
    bot_health_checker_router,
    sandbox_device_router,
)
from secbaas.community.adapters.web.routers.internal import (
    cache_router,
    internal_health_router,
    internal_router,
)
from secbaas.community.adapters.web.routers.open_api import (
    open_api_message_router,
    open_api_run_router,
    open_api_session_router,
)
from secbaas.community.adapters.web.routers.paas_service import (
    device_router,
    local_paas_router,
    paas_facade_router,
)
from secbaas.community.adapters.web.routers.relay_session_router import (
    router as relay_session_router,
)
from secbaas.community.adapters.web.websocket import local_management_router
from secbaas.community.api import DomainError
from secbaas.community.api.bcn import BcnError
from secbaas.community.api.device_manage import (
    DEVICE_CREATION_ERROR_TO_HTTP_STATUS,
    DeviceCreationError,
)
from secbaas.community.bootstrap import (
    ApplicationContainer,
    init_container_config,
    initialize_services,
    set_container,
    shutdown_services,
)
from secbaas.community.config import Config, ConfigLoader
from secbaas.community.logger import get_logger, get_logger_plugin
from secbaas.community.spi.tracer import get_tracer_plugin, set_tracer_plugin

logger = get_logger("webserver")


def load_config() -> Config:
    """加载配置文件

    默认使用 sofapy 框架标准方案（get_config()），根据 SERVER_ENV 自动定位配置文件。
    当设置 SOFAPY_CONFIG_OVERLAY 环境变量时，由 ConfigLoader 加载指定 overlay
    的配置文件并自动与 application.yaml 合并，用于测试等需要覆盖配置的场景。
    """
    config = ConfigLoader.load()
    logger.info(f"Loaded config: app_name={config.app_name}, workers={config.workers}")
    return config


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理"""
    logger.info("Initializing application...")

    # ── Initialize DI container ────────────────────────────────────────────────
    container = ApplicationContainer()
    init_container_config(container)
    app.container = container
    set_container(container)

    # ── Eagerly initialise all components (database → plugins → repository → services) ──
    await initialize_services(container)

    try:
        yield
    finally:
        await shutdown_services(container)
        logger.info("Application shutdown complete")


# ── Exception handler functions (module-level for importability) ──────────────


async def domain_exception_handler(request: Request, exc: DomainError) -> JSONResponse:
    if exc.http_status < 500:
        logger.warning(f"DomainError: {exc.error_code} - {exc.message}")
    else:
        logger.error(f"DomainError: {exc.error_code} - {exc.message}", exc_info=True)
    return JSONResponse(
        status_code=exc.http_status,
        content={"detail": {"error_code": exc.error_code, "message": exc.message}},
    )


async def validation_exception_handler(
    request: Request, exc: ValueError
) -> JSONResponse:
    logger.error(f"ValidationError: {exc}", exc_info=True)
    return JSONResponse(
        status_code=400,
        content={"detail": {"error_code": "INVALID_REQUEST", "message": str(exc)}},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.critical(f"UnhandledException: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "error_code": "INTERNAL_ERROR",
                "message": "Internal server error",
            }
        },
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Sets up tracer, logging, exception handlers, and includes all routers.
    The DI container is initialized in the lifespan handler.
    """
    app = FastAPI(
        title="SecBaaS API",
        description="SecBaaS API",
        version="1.0.0",
        lifespan=lifespan,
    )

    # ── tracer plugin selection ────────────────────────────────────────────────
    import os as _os
    from importlib.metadata import entry_points

    _is_sofa_mode = _os.getenv("SECBAAS_RUN_MODE", "bare").lower() == "sofa"
    _tracer = None
    if _is_sofa_mode:
        for ep in entry_points(group="secbaas.tracer"):
            if ep.name == "sofa":
                _tracer = ep.load()()
                break
    if _tracer is None:
        from secbaas.community.plugins.tracer.bare import BareTracerPlugin

        _tracer = BareTracerPlugin()
    set_tracer_plugin(_tracer)

    tracer_plugin = get_tracer_plugin()
    tracer_plugin.setup("secbaas")
    tracer_plugin.install_middleware(app)

    config = load_config()
    log_config = config.log_config
    logger_plugin = get_logger_plugin()
    logger_plugin.configure(
        log_level=log_config.log_level or "INFO",
        log_dir=log_config.log_dir or "",
        app_name=config.app_name or "secbaas",
        trace_log_dir=getattr(log_config, "trace_log_dir", "") or "",
    )

    # ── Exception handlers ─────────────────────────────────────────────────────
    @app.exception_handler(BcnError)
    async def _bcn_error_handler(request: Request, exc: BcnError) -> JSONResponse:
        return await bcn_exception_handler(request, exc)

    app.add_exception_handler(DomainError, domain_exception_handler)

    @app.exception_handler(DeviceCreationError)
    async def device_creation_exception_handler(
        request: Request, exc: DeviceCreationError
    ) -> JSONResponse:
        status_code = DEVICE_CREATION_ERROR_TO_HTTP_STATUS.get(str(exc.error_code), 500)
        if (
            status_code == 500
            and str(exc.error_code) not in DEVICE_CREATION_ERROR_TO_HTTP_STATUS
        ):
            logger.warning(
                "DeviceCreationError has unmapped error_code='%s' at %s, defaulting to 500",
                exc.error_code,
                request.url.path,
            )
        if status_code < 500:
            logger.warning(
                "DeviceCreationError at %s: %s - %s",
                request.url.path,
                exc.error_code,
                exc.message,
            )
        else:
            logger.error(
                "DeviceCreationError at %s: %s - %s",
                request.url.path,
                exc.error_code,
                exc.message,
                exc_info=True,
            )
        detail: dict = {
            "error_code": str(exc.error_code),
            "message": exc.message,
        }
        if exc.context is not None and config.user_config.get("app", {}).get(
            "local_debug", False
        ):
            detail["diagnostic"] = exc.context
        return JSONResponse(status_code=status_code, content={"detail": detail})

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning(
            "RequestValidationError: path=%s, method=%s, errors=%s",
            request.url.path,
            request.method,
            exc.errors(),
        )
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()},
        )

    app.add_exception_handler(ValueError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # ── Register routers ───────────────────────────────────────────────────────
    app.include_router(api_gateway_router)
    app.include_router(cache_router)
    app.include_router(device_router)
    app.include_router(device_template_router)
    app.include_router(system_config_router)
    app.include_router(admin_api_gateway_router)
    app.include_router(bot_management_router)
    app.include_router(bot_start_progress_router)
    app.include_router(bot_wss_router)
    app.include_router(bot_http_conn_router)
    app.include_router(bot_http_router)
    app.include_router(bot_cmd_router)
    app.include_router(bot_open_folder_router)
    app.include_router(bot_qpm_router)
    app.include_router(tenant_router)
    app.include_router(paas_facade_router)
    app.include_router(relay_session_router)
    app.include_router(local_paas_router)
    app.include_router(publish_router)
    app.include_router(callback_router)
    app.include_router(publish_admin_router)
    app.include_router(sandbox_device_router)
    app.include_router(bot_health_checker_router)
    app.include_router(open_api_message_router)
    app.include_router(open_api_run_router)
    app.include_router(open_api_session_router)
    app.include_router(bcn_downlink_router)
    app.include_router(local_management_router)
    app.include_router(internal_router)
    app.include_router(internal_health_router)

    # ── Mount extra routers registered by extensions (enterprise, etc.) ──────
    from ._router_registry import get_extra_routers

    for extra_router in get_extra_routers():
        app.include_router(extra_router)

    @app.get("/hello")
    async def hello() -> dict[str, str]:
        logger.info("Hello endpoint called")
        return {"message": "hello, i am sofapy"}

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "healthy"}

    if _os.getenv("SINGLEBOX_COVERAGE") == "1":
        from secbaas.community.adapters.web.singlebox_coverage import (
            install_singlebox_coverage_middleware,
        )

        install_singlebox_coverage_middleware(app)

    return app


app = create_app()
