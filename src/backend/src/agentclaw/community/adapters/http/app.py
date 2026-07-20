"""FastAPI composition root.

Responsibilities — and only these:
  1. Build the DI container (must run before any router import that
     resolves a singleton at module load).
  2. Run pre-import side effects (``AGENTCLAW_CONFIG_PATH``, OpenClaw
     DB configuration).
  3. Construct ``app = FastAPI(lifespan=_app_lifespan)`` and attach
     the injector. The lifespan body discovers every
     ``Lifecycle`` participant in the injector and drives the four
     phases (``bootstrap`` → ``startup`` → yield → ``shutdown`` →
     ``teardown``) concurrently within each phase.
  4. Register exception handlers, the health endpoint, all routers.
  5. Delegate middleware → ``api/middleware.py``.

Middleware bodies live in :mod:`agentclaw.community.adapters.http.middleware`. Startup
and shutdown work lives on the components that own it — they
implement :class:`agentclaw.community.kernel.lifecycle.Lifecycle`.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

# ─── DI container bootstrap ──────────────────────────────────────────────
# Build the injector at module import time. Routers and services
# resolve their deps through it via ``Injected(X)``. The injector is
# bound to FastAPI's ``app.state`` via ``attach_injector(app, injector)``
# further down (after ``app = FastAPI(...)``), which is the canonical
# wiring. No module-global injector handle exists — every consumer
# goes through DI or ``app.state.injector``.
from agentclaw.community.di import (
    DeployProfile,
    build_injector,
    validate_deploy_environment,
)
from agentclaw.community.di.config_bootstrap import register_config_provider
from agentclaw.community.di.modules_bootstrap import register_corp_modules

# Single mandatory switch: read the deploy profile once, here at the
# composition root. ``detect()`` errors out if ``DEPLOY_PROFILE`` is unset
# or unknown — every launch site sets it (see scripts/ and conf/docker).
_deploy_profile = DeployProfile.detect()
validate_deploy_environment()

# Select the configuration source before anything reads config: under ``corp``
# this installs the sofapy-backed provider; other profiles stay on the YAML
# default. Must precede ``build_injector`` (its config-reading providers).
register_config_provider(_deploy_profile)  # noqa: FLA010 — composition root, must run at import time before build_injector reads config

# Supply the corp infrastructure-module column (corp branch only). Must precede
# ``build_injector`` — ``modules_for(CORP)`` reads this registry. No-op for
# community / test / singlebox (B8).
register_corp_modules(_deploy_profile)  # noqa: FLA010 — composition root, before build_injector

injector = build_injector(profile=_deploy_profile)

# Startup integrity check: resolve a small set of critical bindings
# now so misconfiguration surfaces at boot instead of on first request.
# Gated on ``SERVER_ENV`` — fires in ``pre`` and ``prod`` (where every
# prod-only dep is expected to resolve), skipped in ``dev`` / local
# (where ZDAS handle, Arca sandbox config, etc. aren't reachable).
from agentclaw.community.utils.env_utils import get_current_env  # noqa: E402 post-DI-bootstrap

if get_current_env() in ("pre", "prod"):
    from agentclaw.community.di.container import eager_check_critical_bindings  # noqa: E402 post-DI-bootstrap

    eager_check_critical_bindings(injector)
# ──────────────────────────────────────────────────────────────────────────


# =============================================================================
# Pre-import side effects: AGENTCLAW_CONFIG_PATH + OpenClaw DB config
# =============================================================================
def _set_openclaw_config_path():
    """Set AGENTCLAW_CONFIG_PATH env var so OpenClaw can find application.yaml."""
    try:
        from pathlib import Path
        # B11: configs live in the community subtree (agentclaw/community/configs);
        # a deploy's assembled runtime `configs/` (cwd) holds them too. This file is
        # at agentclaw/community/adapters/http/app.py, so parents[2] is
        # agentclaw/community.
        possible_config_dirs = [
            Path.cwd() / "configs",
            Path(__file__).resolve().parents[2] / "configs",  # agentclaw/community/configs
        ]
        for config_dir in possible_config_dirs:
            if config_dir.exists() and (config_dir / "application.yaml").exists():
                os.environ["AGENTCLAW_CONFIG_PATH"] = str(config_dir)
                logging.info(f"Set AGENTCLAW_CONFIG_PATH to {config_dir}")
                break
    except Exception as e:
        logging.warning(f"Could not set AGENTCLAW_CONFIG_PATH: {e}")


_set_openclaw_config_path()  # noqa: FLA010 — composition root, must run at import time before OpenClaw imports below read AGENTCLAW_CONFIG_PATH


# =============================================================================
# Router imports (post-DI-bootstrap)
# =============================================================================
from agentclaw.community.adapters.http.token_exchange import router as token_exchange_router  # noqa: E402
from agentclaw.community.adapters.http.yuque import router as yuque_router  # noqa: E402
from agentclaw.community.adapters.http.devices.router import router as device_router  # noqa: E402
from agentclaw.community.adapters.http.access.router import access_router as whitelist_router  # noqa: E402
from agentclaw.community.adapters.http.access.router import user_router  # noqa: E402
from agentclaw.community.adapters.http.expert_chat import router as expert_chats_router  # noqa: E402
from agentclaw.community.adapters.http.bot_chat import router as bot_chat_router  # noqa: E402
from agentclaw.community.adapters.http.bot_chat.otel_router import router as bot_chat_otel_router  # noqa: E402
from agentclaw.community.adapters.http.bot_chat.relation_router import router as bot_chat_relation_router  # noqa: E402
from agentclaw.community.adapters.http.system_config.router import router as system_config_router  # noqa: E402
from agentclaw.community.adapters.http.common_config.router import router as common_config_router  # noqa: E402
from agentclaw.community.adapters.http.beta_quota.router import router as beta_quota_router  # noqa: E402
from agentclaw.community.adapters.http.channel.router import router as channel_router  # noqa: E402
from agentclaw.community.adapters.http.quality.router import router as quality_router  # noqa: E402
from agentclaw.community.adapters.http.bot_render_screen.router import router as render_screen_router  # noqa: E402
from agentclaw.community.adapters.http.antprocess import router as antprocess_router  # noqa: E402
from agentclaw.community.adapters.http.antcode.router import router as antcode_router  # noqa: E402
from agentclaw.community.adapters.http.bot_public import bot_public_auth_router, bot_public_router, bot_public_noauth_router  # noqa: E402
from agentclaw.community.adapters.http.oss_to_nas.router import router as oss_to_nas_router  # noqa: E402
from agentclaw.community.adapters.http.system import system_health_router, system_readiness_router, system_disk_usage_router  # noqa: E402
from agentclaw.community.adapters.http.desktop.router import bot_router as desktop_bot_router, device_router as desktop_device_router  # noqa: E402
from agentclaw.community.adapters.http.harness.router import router as harness_router  # noqa: E402
from agentclaw.community.adapters.http.economy.router import router as economy_governance_router  # noqa: E402
from agentclaw.community.adapters.http.economy.admin_router import admin_router as economy_governance_admin_router  # noqa: E402
from agentclaw.community.adapters.http.economy.workflow_router import workflow_router as economy_governance_workflow_router  # noqa: E402
from agentclaw.community.adapters.http.approvals.router import router as approvals_router  # noqa: E402
from agentclaw.community.adapters.http.identity.router import router as identity_router  # noqa: E402
from agentclaw.community.adapters.http.aicoding.router import router as aicoding_router  # noqa: E402
from agentclaw.community.adapters.http.aicoding.data_proxy_router import router as aicoding_data_proxy_router  # noqa: E402
from agentclaw.community.adapters.http.aicoding.workitem_noauth_router import router as workitem_noauth_router  # noqa: E402
from agentclaw.community.adapters.http.enums.router import router as enums_router  # noqa: E402
from agentclaw.community.adapters.http.resources import router as resources_router  # noqa: E402
from agentclaw.community.adapters.http.mcp import router as mcp_router  # noqa: E402
from agentclaw.community.adapters.http.cron import router as cron_router  # noqa: E402
from agentclaw.community.adapters.http.cron.cron_noauth_router import router as cron_noauth_router  # noqa: E402
from agentclaw.community.adapters.http.aicoding import notify_router  # noqa: E402
from agentclaw.community.adapters.http.aicoding.architect_rebind_router import router as architect_rebind_router  # noqa: E402
from agentclaw.community.adapters.http.bot_management import router as bot_management_router  # noqa: E402
from agentclaw.community.adapters.http.caller_identity.router import router as caller_identity_router  # noqa: E402
from agentclaw.community.adapters.http.bot_dormant import router as bot_dormant_router  # noqa: E402
from agentclaw.community.adapters.http.bot_dormant.router import internal_router as bot_dormant_internal_router  # noqa: E402
from agentclaw.community.adapters.http.service_bot.router_build import router as service_bot_router  # noqa: E402
from agentclaw.community.adapters.http.service_bot.router_publish import router as service_bot_publish_router  # noqa: E402
from agentclaw.community.adapters.http.bot_collaborator import router as bot_collaborator_router  # noqa: E402
# skills / skillsets / skill_scan / skill_auth 全部切换到新架构 (core/skill_center + device plugin 抽象)
from agentclaw.community.adapters.http.skill_center import skills, skillsets, skill_scan, skill_auth, skill_category, verify, sync, batch_sync  # noqa: E402

from fastapi_injector import attach_injector  # noqa: E402

# =============================================================================
# Lifespan — generic Lifecycle dispatch over DI-discovered participants
# =============================================================================
from agentclaw.community.kernel.lifecycle import discover_lifecycle_participants  # noqa: E402
from agentclaw.community.log import get_logger  # noqa: E402

logger = get_logger()


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    """Application lifespan — runs every Lifecycle participant's hooks.

    Four phases, two each direction:

      Startup:   bootstrap()  ->  startup()
      Shutdown:  shutdown()   ->  teardown()

    Within a phase, all participants run concurrently via
    ``asyncio.gather``. The next phase only begins after the previous
    phase's coroutines have all resolved. Setup direction is
    fail-fast; teardown direction is log-and-continue.
    """
    participants = discover_lifecycle_participants(injector)
    logger.info(
        "[lifecycle] %d participants discovered: %s",
        len(participants),
        [type(p).__name__ for p in participants],
    )

    # ── Setup phase 1: bootstrap (infra prereqs, e.g. DB schema)
    await asyncio.gather(*(p.bootstrap() for p in participants))

    # ── Setup phase 2: startup (service-tier work)
    await asyncio.gather(*(p.startup() for p in participants))

    # ── ``yield`` is the FastAPI lifespan pattern (replaces the
    # deprecated ``@app.on_event(...)`` decorators). The
    # ``@asynccontextmanager`` decorator turns this single-yield
    # coroutine into an async context manager whose body runs in two
    # halves: everything *before* ``yield`` runs at app startup; the
    # coroutine then suspends at ``yield`` and FastAPI begins accepting
    # HTTP requests. When the app is told to shut down (SIGTERM,
    # uvicorn graceful stop, TestClient context exit), the coroutine
    # resumes from immediately *after* ``yield`` and the shutdown
    # phases run before the process exits.
    #
    # Reference: https://fastapi.tiangolo.com/advanced/events/
    yield

    # ── Teardown phase 1: shutdown (stop services)
    shutdown_results = await asyncio.gather(
        *(p.shutdown() for p in participants),
        return_exceptions=True,
    )
    for p, r in zip(participants, shutdown_results):
        if isinstance(r, Exception):
            logger.error(
                "[lifecycle] shutdown failed for %r: %s",
                type(p).__name__, r, exc_info=r,
            )

    # ── Teardown phase 2: teardown (release infra)
    teardown_results = await asyncio.gather(
        *(p.teardown() for p in participants),
        return_exceptions=True,
    )
    for p, r in zip(participants, teardown_results):
        if isinstance(r, Exception):
            logger.error(
                "[lifecycle] teardown failed for %r: %s",
                type(p).__name__, r, exc_info=r,
            )


app = FastAPI(lifespan=_app_lifespan)

# The injector was built at the top of this file (before router
# imports). Now that ``app`` exists, attach the same injector so
# route handlers can resolve via Injected(...) and
# request.app.state.injector.
attach_injector(app, injector)


# =============================================================================
# Middleware (delegated to api/middleware.py)
# =============================================================================
from agentclaw.community.adapters.http.middleware import install_middleware  # noqa: E402
from agentclaw.community.di.config import CorsConfig  # noqa: E402
from agentclaw.community.plugin_api.auth import AuthPlugin  # noqa: E402
from agentclaw.community.plugin_api.tracer import TracerPlugin  # noqa: E402

install_middleware(
    app,
    auth_plugin=injector.get(AuthPlugin),
    tracer=injector.get(TracerPlugin),
    cors_config=injector.get(CorsConfig),
)

if os.environ.get("SINGLEBOX_COVERAGE") == "1":
    from agentclaw.community.adapters.http.singlebox_coverage import (  # noqa: E402
        install_singlebox_coverage_middleware,
    )

    install_singlebox_coverage_middleware(app)


# =============================================================================
# Exception translation: DomainError / DataProxyError -> HTTP response
# =============================================================================
# Rule 7 (Core Independence): core/ defines error subclasses without any
# HTTP knowledge. The adapter layer (this file) owns the mapping from
# error class -> HTTP status. A different adapter (gRPC, CLI) would
# supply its own mapping.
#
# Handlers are registered on the base class; Starlette dispatches subclass
# instances via MRO. The DomainError body is {"detail": <str>} — same
# shape that fastapi.HTTPException would have produced. DataProxyError
# carries a richer detail dict ({error, op, bot_id}) that aixharness
# depends on, so it has its own handler.
#
# Unmapped subclasses default to 500. An architecture test
# (tests/architecture/test_domain_error_status_map_complete.py) asserts
# every concrete DomainError subclass has an entry here.
from fastapi.responses import JSONResponse  # noqa: E402
from agentclaw.community.core.aicoding.services.data_proxy_service import (  # noqa: E402
    DataProxyError,
    EngineUnreachable,
    EngineUrlNotConfigured,
)
from agentclaw.community.core.errors import (  # noqa: E402
    Conflict,
    DomainError,
    Forbidden,
    InternalError,
    LoginRedirectRequired,
    NotFound,
    Unauthorized,
    ValidationError,
)
from agentclaw.community.core.caller_identity.contracts import (  # noqa: E402
    CallerCallTypeInvalidError,
    CallerIdentityNotFoundError,
    CallerIdentityPermissionError,
    CallerIdentityReadOnlyError,
    CallerLockEpochError,
    CallerMcpNotFoundError,
    CallerMcpSyncError,
)

_DOMAIN_ERROR_STATUS_MAP: dict[type[DomainError], int] = {
    ValidationError:       400,
    Unauthorized:          401,
    LoginRedirectRequired: 302,
    Forbidden:             403,
    NotFound:              404,
    Conflict:              409,
    InternalError:         500,
    CallerIdentityPermissionError: 403,
    CallerIdentityNotFoundError: 404,
    CallerIdentityReadOnlyError: 409,
    CallerLockEpochError: 409,
    CallerMcpNotFoundError: 404,
    CallerMcpSyncError: 500,
    CallerCallTypeInvalidError: 500,
}

_DATA_PROXY_ERROR_STATUS_MAP: dict[type[DataProxyError], int] = {
    EngineUrlNotConfigured: 500,
    EngineUnreachable:      502,
}


def _trace_headers(request: Request) -> dict[str, str]:
    """Return ``{"X-Trace-ID": <id>}`` for echoing on error responses.

    The TraceIdMappingMiddleware also sets this header on success, but
    we duplicate it on the error path so the trace_id is guaranteed
    on every response regardless of middleware ordering.
    """
    trace_id = getattr(request.state, "trace_id", None)
    return {"X-Trace-ID": trace_id} if trace_id else {}


@app.exception_handler(DomainError)
async def _domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    status = _DOMAIN_ERROR_STATUS_MAP.get(type(exc), 500)
    # 4xx/3xx are expected client-side flow (bad input, missing auth) — logging
    # every one would be noise. 5xx means a core service signalled an internal
    # failure; emit a full traceback so the cause is recoverable from logs.
    if status >= 500:
        logger.exception(
            "[DomainError 5xx] %s on %s %s: %s",
            type(exc).__name__, request.method, request.url.path, exc.detail,
        )
    return JSONResponse(
        status_code=status,
        content={"detail": exc.detail},
        headers=_trace_headers(request),
    )


@app.exception_handler(DataProxyError)
async def _data_proxy_error_handler(
    request: Request, exc: DataProxyError,
) -> JSONResponse:
    """Translate aicoding data-proxy errors into the wire shape aixharness
    expects: ``{"detail": {"error": ..., "op": ...}}``.

    Kept separate from the DomainError handler because the response body
    carries ``op`` on top of the message — the global
    ``{"detail": <str>}`` shape would lose it.
    """
    status = _DATA_PROXY_ERROR_STATUS_MAP.get(type(exc), 500)
    detail: dict[str, object] = {"error": exc.message, "op": exc.op}
    if status >= 500:
        logger.exception(
            "[DataProxyError 5xx] %s on %s %s: %s",
            type(exc).__name__, request.method, request.url.path, exc.message,
        )
    return JSONResponse(
        status_code=status,
        content={"detail": detail},
        headers=_trace_headers(request),
    )


# Catch-all for unhandled non-DomainError exceptions. Returns the same
# {"detail": ...} JSON shape (status 500) instead of Starlette's default
# plain-text "Internal Server Error" body, so the wire format is uniform
# across every error path. Always logs the traceback — by definition we
# didn't expect this exception, so the trace is the only debug signal.
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "[Unhandled exception] %s on %s %s",
        type(exc).__name__, request.method, request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
        headers=_trace_headers(request),
    )


# =============================================================================
# Health check endpoint
# =============================================================================
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}


# =============================================================================
# Router registration
# =============================================================================
# 1. AgentClaw 自有路由
app.include_router(token_exchange_router)
app.include_router(yuque_router)
app.include_router(device_router)
app.include_router(expert_chats_router)  # 新增：用户与专家Bot对话管理
app.include_router(bot_chat_router)  # 个人对话（Langfuse trace 查询）
app.include_router(bot_chat_otel_router)  # bot-chat OTLP 日志写入
app.include_router(bot_chat_relation_router)  # bot-chat 业务任务关系写入
app.include_router(whitelist_router)
app.include_router(user_router)
app.include_router(system_config_router)
app.include_router(common_config_router)
app.include_router(beta_quota_router)
app.include_router(channel_router)
app.include_router(quality_router)
try:
    app.include_router(render_screen_router)
    logger.info("[RenderScreen] Router registered successfully: prefix=%s", render_screen_router.prefix)
except Exception as e:
    logger.exception("[RenderScreen] Failed to register router: %s", e)
app.include_router(antprocess_router)
app.include_router(antcode_router)  # AntCode 集成
app.include_router(bot_public_auth_router)
app.include_router(bot_public_router)
app.include_router(bot_public_noauth_router)  # 免鉴权版本（与鉴权版本并存）
app.include_router(workitem_noauth_router)  # 工作项免鉴权接口 (route URL still /api/public/dima)
app.include_router(oss_to_nas_router)
app.include_router(system_health_router)
app.include_router(system_readiness_router)
app.include_router(system_disk_usage_router)
app.include_router(desktop_device_router)
app.include_router(desktop_bot_router)

# 2. OpenClawEnterprise 路由（统一挂载到 /api 下）
# 注意：OpenClaw 的路由前缀和 AgentClaw 不冲突
# - AgentClaw: /api/v1/devices
# - OpenClaw: /api/resources, /api/sessions, /api/chat, /api/skills, /api/skillsets
app.include_router(resources_router)
app.include_router(skills.router)
app.include_router(skillsets.router)
app.include_router(approvals_router)
app.include_router(mcp_router)
app.include_router(identity_router)
app.include_router(aicoding_router)
app.include_router(aicoding_data_proxy_router)
app.include_router(architect_rebind_router)
app.include_router(bot_management_router.router)
app.include_router(caller_identity_router)
app.include_router(bot_dormant_router.router)
app.include_router(bot_dormant_internal_router)
app.include_router(service_bot_router)
app.include_router(service_bot_publish_router)
app.include_router(bot_collaborator_router)
app.include_router(skill_scan.router)
app.include_router(skill_auth.router)
app.include_router(skill_category.router)
app.include_router(verify.router)
app.include_router(sync.router)
app.include_router(batch_sync.router)
app.include_router(cron_router)
app.include_router(cron_noauth_router)  # Cron 免鉴权接口
app.include_router(notify_router)
# Harness Engineering: patch template management & diagnosis
app.include_router(harness_router)
# Economy Governance: notification & audit
app.include_router(economy_governance_router)
app.include_router(economy_governance_admin_router)
app.include_router(economy_governance_workflow_router)
app.include_router(enums_router)

# Runtime-mode-conditional routers (bound by DI: empty in prod, populated
# in local boots via ``TestingInfrastructureModule``). The app does not
# branch on mode here — composition root decides what gets mounted.
from agentclaw.community.di.optional_routers import OptionalRouters  # noqa: E402
for _r in injector.get(OptionalRouters).routers:
    app.include_router(_r)
