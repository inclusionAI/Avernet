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
from agentclaw.community.di.modules_bootstrap import register_corp_modules, resolve_extra_modules

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

injector = build_injector(profile=_deploy_profile, extra_modules=resolve_extra_modules(_deploy_profile))

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
from agentclaw.community.adapters.http.access.router import user_list_router  # noqa: E402
from agentclaw.community.adapters.http.access.router import user_router  # noqa: E402
from agentclaw.community.adapters.http.org.router import router as org_user_router  # noqa: E402
from agentclaw.community.adapters.http.expert_chat import router as expert_chats_router  # noqa: E402
from agentclaw.community.adapters.http.bot_chat import router as bot_chat_router  # noqa: E402
from agentclaw.community.adapters.http.bot_chat.otel_router import router as bot_chat_otel_router  # noqa: E402
from agentclaw.community.adapters.http.bot_chat.relation_router import router as bot_chat_relation_router  # noqa: E402
from agentclaw.community.adapters.http.system_config.router import router as system_config_router  # noqa: E402
from agentclaw.community.adapters.http.common_config.router import router as common_config_router  # noqa: E402
from agentclaw.community.adapters.http.skills_pool import router as skills_pool_ops_router  # noqa: E402
from agentclaw.community.adapters.http.beta_quota.router import router as beta_quota_router  # noqa: E402
from agentclaw.community.adapters.http.channel.router import router as channel_router  # noqa: E402
from agentclaw.community.adapters.http.quality.router import router as quality_router  # noqa: E402
# The task surface is internal: execute/dashboard/list, the report and bbs
# operations, the discovery phase and the engine's push callbacks all answer
# under ``/api/v1/collaboration/tasks`` in ``adapters/http/task``. The
# ``/openapi/v1`` namespace is reserved for the domains the gateway's shipped
# configuration routes and secures (tests/community/contracts/gateway/
# test_public_namespace.py), so ``openapi_v1/task`` stays unmounted until that
# configuration declares the collaboration domain.
from agentclaw.community.adapters.http.openapi_v1.task.router import router as task_router  # noqa: E402
from agentclaw.community.adapters.http.work_orders.router import router as work_orders_http_router  # noqa: E402
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
from agentclaw.community.adapters.http.session_resources import internal_router as session_resources_internal_router, router as session_resources_router  # noqa: E402
from agentclaw.community.adapters.http.mcp import router as mcp_router  # noqa: E402
from agentclaw.community.adapters.http.cron import router as cron_router  # noqa: E402
from agentclaw.community.adapters.http.cron.cron_noauth_router import router as cron_noauth_router  # noqa: E402
from agentclaw.community.adapters.http.aicoding import notify_router  # noqa: E402
from agentclaw.community.adapters.http.aicoding.architect_rebind_router import router as architect_rebind_router  # noqa: E402
from agentclaw.community.adapters.http.bot_management import router as bot_management_router  # noqa: E402
from agentclaw.community.adapters.http.caller_identity.router import router as caller_identity_router  # noqa: E402
from agentclaw.community.adapters.http.bot_dormant import router as bot_dormant_router  # noqa: E402
from agentclaw.community.adapters.http.bot_dormant.router import internal_router as bot_dormant_internal_router  # noqa: E402
from agentclaw.community.adapters.http.spaces_internal import router as spaces_internal_router  # noqa: E402
from agentclaw.community.adapters.http.service_bot.router_build import router as service_bot_router  # noqa: E402
from agentclaw.community.adapters.http.service_bot.router_publish import router as service_bot_publish_router  # noqa: E402
from agentclaw.community.adapters.http.bot_collaborator import router as bot_collaborator_router  # noqa: E402
from agentclaw.community.adapters.http.task import task_internal_router, task_callback_router  # noqa: E402
# skills / skillsets / skill_scan / skill_auth 全部切换到新架构 (core/skill_center + device plugin 抽象)
from agentclaw.community.adapters.http.skill_center import skills, skillsets, skill_scan, skill_auth, skill_category, verify, sync, batch_sync, installations_internal  # noqa: E402

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
from agentclaw.community.di.config import CorsConfig, SecretNamesConfig  # noqa: E402
from agentclaw.community.plugin_api.auth import AuthPlugin  # noqa: E402
from agentclaw.community.plugin_api.secret_resolver import SecretResolver  # noqa: E402
from agentclaw.community.plugin_api.tracer import TracerPlugin  # noqa: E402
from agentclaw.community.utils.gateway_principal_config import (  # noqa: E402
    init_principal_verifier_config,
)

# Resolve the key the gateway signs /openapi/v1 principals with. Done here
# because AvernetTenantMiddleware reads the verifier config from the raw ASGI
# layer, before any route and outside the injector — so the composition root
# pushes it in rather than the middleware pulling it out.
#
# Strict in ``pre``/``prod``, matching the eager binding check above and gated
# on the same SERVER_ENV: a deployment that serves the public API without a
# signing key answers 401 to every request while looking healthy, so it must
# fail the rollout instead. Local, dev, and singlebox legitimately have no key
# (singlebox ships it empty on purpose), so there it degrades to deny-everything
# rather than refusing to boot.
init_principal_verifier_config(
    injector.get(SecretResolver),
    injector.get(SecretNamesConfig).gateway_principal_signing_key,
    strict=get_current_env() in ("pre", "prod"),
)

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
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.exception_handlers import (  # noqa: E402
    http_exception_handler,
    request_validation_exception_handler,
)
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402
from agentclaw.community.core.aicoding.services.data_proxy_service import (  # noqa: E402
    DataProxyError,
    EngineUnreachable,
    EngineUrlNotConfigured,
)
from agentclaw.community.core.errors import (  # noqa: E402
    CallbackAuthError,
    CallbackCorrelationError,
    Conflict,
    DomainError,
    Forbidden,
    InternalError,
    LoginRedirectRequired,
    NotFound,
    Unauthorized,
    ValidationError,
)
from agentclaw.community.adapters.http.error_logging import (  # noqa: E402
    params_suffix,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (  # noqa: E402
    BotAccessRefusedError,
    GrantNotResolvableError,
    MissingPrincipalError,
    UserIdMismatchError,
)
from agentclaw.community.core.gateway_principal import (  # noqa: E402
    PrincipalVerificationError,
)
from agentclaw.community.core.caller_identity.contracts import (  # noqa: E402
    CallerCallTypeInvalidError,
    CallerCliNotFoundError, CallerCliSyncError,
    CallerIdentityAmbiguousError,
    CallerIdentityIrreversibleError,
    CallerIdentityNotFoundError,
    CallerIdentityPermissionError,
    CallerIdentityReadOnlyError,
    CallerLockEpochError,
    CallerMcpNotFoundError, CallerMcpSyncError,
)
from agentclaw.community.core.skill_center.errors import (  # noqa: E402
    McpPermissionDeniedError,
    LocalSkillNotReadyError,
    SkillSetAccessDeniedError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneLockUnavailableError,
    SkillSetControlPlaneNotFoundError,
    SkillSetRuntimeReconcileError,
)

_DOMAIN_ERROR_STATUS_MAP: dict[type[DomainError], int] = {
    ValidationError:       400,
    Unauthorized:          401,
    CallbackAuthError:     401,
    CallbackCorrelationError: 400,
    LoginRedirectRequired: 302,
    Forbidden:             403,
    NotFound:              404,
    Conflict:              409,
    InternalError:         500,
    CallerIdentityPermissionError: 403,
    CallerIdentityAmbiguousError: 409,
    CallerIdentityIrreversibleError: 409,
    CallerIdentityNotFoundError: 404,
    CallerIdentityReadOnlyError: 409,
    CallerLockEpochError: 409,
    CallerMcpNotFoundError: 404,
    CallerMcpSyncError: 500,
    CallerCliNotFoundError: 404,
    CallerCliSyncError: 500,
    CallerCallTypeInvalidError: 500,
    # ── SkillSet control plane ────────────────────────────────────────────
    # The legacy /api/skillsets routes used to translate these themselves, in a
    # router-level helper that returned a hand-built HTTPException. That put the
    # status decision inside the route and replaced the raised exception with a
    # fresh one, so the ``__cause__`` chain never reached a log. Mapping them
    # here instead means the route raises the situation and this layer alone
    # decides the wire, exactly as every other domain error already works.
    SkillSetControlPlaneNotFoundError: 404,
    SkillSetAccessDeniedError: 403,
    McpPermissionDeniedError: 403,
    # 400, not 409: the published wire echoes the reason code as a rejected
    # request and clients already parse it that way. Kept as-is deliberately.
    SkillSetControlPlaneConflictError: 400,
    # 409, not 503. The mutation fence could not be taken, so the command never
    # ran and the caller may retry — but the service itself is up and serving,
    # which is the only thing a 503 is allowed to mean. Answering 503 also tells
    # proxies and clients to back off the whole endpoint over what is a
    # per-Bot lock conflict.
    SkillSetControlPlaneLockUnavailableError: 409,
    SkillSetRuntimeReconcileError: 500,
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

def _is_public_api(request: Request) -> bool:
    """Whether this request belongs to the public OpenAPI surface."""
    from agentclaw.community.adapters.http.openapi_v1.responses import is_public_api
    return is_public_api(request)

def _uses_envelope_contract(request: Request) -> bool:
    return _is_public_api(request) or request.url.path.rstrip("/") == "/api/v1/work-orders/events"


def _public_error_envelope(
    status: int, request: Request, headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Envelope a public-surface failure that reached an app-level handler."""
    from agentclaw.community.adapters.http.openapi_v1.responses import (
        unmapped_error_response,
    )

    return unmapped_error_response(status, request, headers=headers)


def _public_mapped_error(request: Request, exc: Exception) -> JSONResponse | None:
    """The public envelope for ``exc`` if this surface maps it, else ``None``.

    ``None`` for every internal ``/api`` request too: those keep the
    ``{"detail": ...}`` shape their existing clients parse.
    """
    if not _uses_envelope_contract(request):
        return None
    from agentclaw.community.adapters.http.openapi_v1.responses import (
        mapped_error_response,
    )

    return mapped_error_response(exc, request)


@app.exception_handler(DomainError)
async def _domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    status = _DOMAIN_ERROR_STATUS_MAP.get(type(exc), 500)
    # Every DomainError that reaches here is logged with its traceback,
    # whatever status it maps to. The status is what the *caller* is told; it
    # says nothing about how much an operator needs to reconstruct the failure.
    # Keying the traceback off it lost real diagnostics: a lock error answering
    # 409 is raised ``from`` the cache failure underneath, and the log recorded
    # that the fence was unavailable while dropping the reason.
    #
    # Level still tracks status — 5xx is ours, 4xx is usually the caller's — so
    # existing alerting keyed on level is unaffected. Only the traceback is new.
    #
    # ``params_suffix`` is empty unless the public surface's ``@envelope_errors``
    # captured the handler's arguments on the way past, so the message shape is
    # unchanged for internal routes that never had them.
    if status >= 500:
        logger.exception(
            "[DomainError 5xx] %s on %s %s: %s%s",
            type(exc).__name__, request.method, request.url.path, exc.detail,
            params_suffix(request),
        )
    elif status >= 400:
        logger.warning(
            "[DomainError %s] %s on %s %s: %s%s",
            status, type(exc).__name__, request.method, request.url.path,
            exc.detail, params_suffix(request),
            exc_info=exc,
        )
    else:
        # 3xx is only ``LoginRedirectRequired``, an ordinary step in the login
        # flow rather than a failure. It used to be silent; it is logged at
        # info so "all errors carry a stack" holds without a redirect looking
        # like a fault to anything reading warnings.
        logger.info(
            "[DomainError %s] %s on %s %s: %s%s",
            status, type(exc).__name__, request.method, request.url.path,
            exc.detail, params_suffix(request),
            exc_info=exc,
        )
    if _uses_envelope_contract(request):
        return _public_error_envelope(status, request)
    return JSONResponse(
        status_code=status,
        content={"detail": exc.detail},
        headers=_trace_headers(request),
    )


@app.exception_handler(LocalSkillNotReadyError)
async def _local_skill_not_ready_handler(
    request: Request, exc: LocalSkillNotReadyError,
) -> JSONResponse:
    """The activation family's readiness refusal, mapped like a conflict.

    The shared mutation flow raises this before any write when the Bot cannot
    safely accept a mutation. ``/openapi/v1`` maps it to 409 in its own table;
    it is a plain ``Exception`` (not a ``DomainError``), so without this
    handler the legacy ``/api`` activation routes would answer an unhandled
    500 for a well-understood 4xx situation.
    """
    logger.warning(
        "[LocalSkillNotReadyError 409] %s %s%s",
        request.method, request.url.path, params_suffix(request),
    )
    public = _public_mapped_error(request, exc)
    if public is not None:
        return public
    return JSONResponse(
        status_code=409,
        content={"detail": "Bot is not ready"},
        headers=_trace_headers(request),
    )


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException,
) -> JSONResponse:
    """Envelope routing-level HTTP errors on the public surface.

    Starlette raises these *before* any router is reached — an unknown
    ``/openapi/v1/...`` path (404) or a wrong method on a known one (405) — and
    its built-in handler answers them, so neither ``@envelope_errors`` nor the
    generic catch-all ever sees them. They are among the most common failures a
    new integrator hits, so leaving them as ``{"detail": ...}`` breaks the
    contract exactly where it is first tested.

    Scoped by path like the other public translations; internal ``/api`` routes
    keep FastAPI's default shape, including any ``HTTPException`` they raise
    themselves.

    ``exc.headers`` is forwarded because the protocol headers on these responses
    carry the actionable part: a 405 without its ``Allow`` list tells the caller
    they got it wrong but not what would be right.
    """
    if _uses_envelope_contract(request):
        # The public response is the bare reason phrase — ``exc.detail`` is
        # replaced, not returned — so this line is the only place the raised
        # detail survives. It also covers an ``HTTPException`` raised *inside* a
        # public handler: ``@envelope_errors`` does not map that type, so it
        # arrives here with the handler's arguments already stashed.
        #
        # Every status carries the traceback. For a handler-raised one ("Upload
        # storage failed", "cron service returned no data") the raise site is
        # the diagnosis. For Starlette's own routing 404/405, raised before any
        # handler, the stack is framework internals and adds little — but the
        # two are indistinguishable from here, and a handler-raised 4xx is the
        # one worth keeping, so this does not try to tell them apart.
        is_server_error = exc.status_code >= 500
        log = logger.error if is_server_error else logger.warning
        log(
            "[Public %s] HTTPException on %s %s: %s%s",
            exc.status_code, request.method, request.url.path, exc.detail,
            params_suffix(request),
            exc_info=exc,
        )
        return _public_error_envelope(exc.status_code, request, exc.headers)
    # Internal ``/api`` routes keep FastAPI's response shape, but its default
    # handler logs nothing at all — an ``HTTPException`` raised inside a route
    # left no record whatsoever. Log it here before delegating, so the response
    # is unchanged and the raise site is still recoverable.
    is_server_error = exc.status_code >= 500
    log = logger.error if is_server_error else logger.warning
    log(
        "[HTTPException %s] on %s %s: %s%s",
        exc.status_code, request.method, request.url.path, exc.detail,
        params_suffix(request),
        exc_info=exc,
    )
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """Answer public-surface validation failures with the standard Envelope.

    FastAPI raises this *before* the handler runs, so the public routers'
    ``@envelope_errors`` decorator never sees it. Without this translation a
    malformed public request (missing field, bad ``cluster_name`` enum,
    out-of-range page size) would return FastAPI's ``{"detail": [...]}`` and
    break the uniform-envelope contract that surface promises.

    Scoped by path: internal ``/api`` routes keep FastAPI's default shape, so
    existing clients are unaffected.
    """
    from agentclaw.community.adapters.http.openapi_v1.responses import error_response

    if _uses_envelope_contract(request):
        # "Invalid request" is all the caller gets, so which field failed is
        # only knowable from here. ``loc``/``type``/``msg`` only — the ``input``
        # each error carries is the caller's raw value, which is exactly the
        # payload this surface must not copy into a log file.
        logger.warning(
            "[Public 422] validation failed on %s %s: %s",
            request.method, request.url.path,
            [
                {"loc": e.get("loc"), "type": e.get("type"), "msg": e.get("msg")}
                for e in exc.errors()
            ],
            exc_info=exc,
        )
        message = "缺少必填字段text" if any(
            "缺少必填字段text" in str(error.get("msg", "")) for error in exc.errors()
        ) else "Invalid request"
        return error_response(422, message, request)
    # Internal routes keep FastAPI's ``{"detail": [...]}`` body, whose default
    # handler logs nothing. Same treatment as above: log, then delegate. The
    # ``input`` each error carries is the caller's raw payload, so only
    # ``loc``/``type``/``msg`` are recorded here too.
    logger.warning(
        "[422] validation failed on %s %s: %s",
        request.method, request.url.path,
        [
            {"loc": e.get("loc"), "type": e.get("type"), "msg": e.get("msg")}
            for e in exc.errors()
        ],
        exc_info=exc,
    )
    return await request_validation_exception_handler(request, exc)


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
            "[DataProxyError 5xx] %s on %s %s: %s%s",
            type(exc).__name__, request.method, request.url.path, exc.message,
            params_suffix(request),
        )
    else:
        logger.warning(
            "[DataProxyError %s] %s on %s %s: %s%s",
            status, type(exc).__name__, request.method, request.url.path,
            exc.message, params_suffix(request),
            exc_info=exc,
        )
    return JSONResponse(
        status_code=status,
        content={"detail": detail},
        headers=_trace_headers(request),
    )


@app.exception_handler(MissingPrincipalError)
@app.exception_handler(PrincipalVerificationError)
async def _principal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer an unverifiable caller with a 401 — and *only* a 401.

    Both errors are raised in a **dependency** (``require_principal``, which
    every public route declares), so they surface inside
    ``solve_dependencies`` before the handler runs and ``@envelope_errors``
    never sees them. The catch-all below already mapped them to the right
    status via ``ENVELOPE_ERRORS``, so this handler exists for a different
    reason: *where* it is registered.

    Starlette splits registered handlers by key. ``Exception`` becomes
    ``ServerErrorMiddleware``'s handler, and that middleware sends the response
    and then unconditionally re-raises ("We always continue to raise the
    exception", ``starlette/middleware/errors.py``) so the server can log a
    crash. The result was a correct 401 on the wire followed by a hundred-odd
    lines of ASGI traceback per request, ruinous on this path because an auth
    misconfiguration makes *every* request take it.

    Registering the concrete types instead puts them in the inner
    ``ExceptionMiddleware``, which answers and does not re-raise. The status
    and body still come from ``ENVELOPE_ERRORS`` via ``_public_mapped_error``,
    so this adds a route out of the stack, not a second opinion on the answer.

    The log line below does now carry ``exc_info``, so a misconfiguration still
    puts a traceback behind every request — a deliberate trade, and a much
    smaller one than what this handler was written to stop: the framework's
    re-raise dumped the whole ASGI stack twice per request, while this is one
    formatted chain for the exception itself. If the volume ever does bite, the
    fix is to drop ``exc_info`` here specifically rather than to unregister
    these types, which would bring the re-raise back.
    """
    # ``exc`` carries the operator-facing diagnosis on the verification path —
    # the token's ``alg``/``kid`` and the fingerprint of the key we judged it
    # against. It is logged, never returned: the response is the same fixed
    # "Unauthorized" the table gives every failure here, so a forger still
    # cannot tell which part of a token was rejected.
    logger.warning(
        "[Public 401] %s on %s %s: %s%s",
        type(exc).__name__, request.method, request.url.path, exc,
        params_suffix(request),
        exc_info=exc,
    )
    mapped = _public_mapped_error(request, exc)
    if mapped is not None:
        return mapped
    # Raised off the public surface — an internal ``/api`` route reaching the
    # verifier directly. Those keep the ``{"detail": ...}`` shape their clients
    # parse rather than being handed an Envelope they do not expect.
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
        headers=_trace_headers(request),
    )


@app.exception_handler(UserIdMismatchError)
async def _user_id_mismatch_handler(
    request: Request, exc: UserIdMismatchError,
) -> JSONResponse:
    """Answer a request that named a user its caller may not act for — 403.

    Registered as a concrete type for the same reason as the handler above: it
    is raised in ``require_user_id``, a **dependency** every user-scoped public
    route declares, so ``@envelope_errors`` never sees it and letting it reach
    the ``Exception`` catch-all would answer correctly and then re-raise through
    ``ServerErrorMiddleware``, adding an ASGI traceback to every occurrence.

    Which ids disagreed is already logged by ``require_user_id``; this line
    records that the request was refused, and where.
    """
    logger.warning(
        "[Public 403] %s on %s %s: %s%s",
        type(exc).__name__, request.method, request.url.path, exc,
        params_suffix(request),
        exc_info=exc,
    )
    mapped = _public_mapped_error(request, exc)
    if mapped is not None:
        return mapped
    # Unreachable while the dependency is only mounted on the public surface;
    # kept so a future internal caller of the same seam gets the ``{"detail":
    # ...}`` shape its clients parse rather than an Envelope.
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
        headers=_trace_headers(request),
    )


@app.exception_handler(GrantNotResolvableError)
async def _grant_not_resolvable_handler(
    request: Request, exc: GrantNotResolvableError,
) -> JSONResponse:
    """Answer an application caller with no grant for what it addressed — 404.

    Registered as a concrete type for the same reason as the two handlers above:
    it is raised in a **dependency**, so ``@envelope_errors`` never sees it.

    **The body must be byte-identical to a nonexistent bot's**, which is why
    this goes through ``_public_mapped_error`` with the same status rather than
    composing its own. An application that could tell "not granted" from "no
    such bot" would have an enumeration oracle for every bot id in the tenant —
    and the grant check would leak precisely what it exists to protect.

    ``403`` would be exactly wrong: on this surface it means "you are
    authenticated and this is not yours", which confirms the bot exists.
    """
    logger.warning(
        "[Public 404] %s on %s %s: %s%s",
        type(exc).__name__, request.method, request.url.path, exc,
        params_suffix(request),
        exc_info=exc,
    )
    mapped = _public_mapped_error(request, exc)
    if mapped is not None:
        return mapped
    return JSONResponse(
        status_code=404,
        content={"detail": "Not found"},
        headers=_trace_headers(request),
    )


@app.exception_handler(BotAccessRefusedError)
async def _bot_access_refused_handler(
    request: Request, exc: BotAccessRefusedError,
) -> JSONResponse:
    """Answer a caller below an operation's collaborator level — 404.

    Registered as a concrete type for the same reason as the handler above: it
    is raised in a **dependency**, so ``@envelope_errors`` never sees it.

    **The body must be byte-identical to a nonexistent bot's**, which is why
    this goes through ``_public_mapped_error`` with the same status rather than
    composing its own. A caller who could tell "not permitted" from "no such
    bot" would have an enumeration oracle over every bot in the tenant — and
    the check would leak precisely what it exists to protect.
    """
    logger.warning(
        "[Public 404] %s on %s %s: %s%s",
        type(exc).__name__, request.method, request.url.path, exc,
        params_suffix(request),
        exc_info=exc,
    )
    mapped = _public_mapped_error(request, exc)
    if mapped is not None:
        return mapped
    return JSONResponse(
        status_code=404,
        content={"detail": "Not found"},
        headers=_trace_headers(request),
    )


# Catch-all for unhandled non-DomainError exceptions. Returns the same
# {"detail": ...} JSON shape (status 500) instead of Starlette's default
# plain-text "Internal Server Error" body, so the wire format is uniform
# across every error path. Logs the traceback for anything genuinely
# unexpected — by definition we didn't expect it, so the trace is the only
# debug signal.
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # A mapped public error can still land here: @envelope_errors only wraps the
    # handler, so an ENVELOPE_ERRORS type raised in a **dependency** is raised
    # before the handler runs and arrives as an "unhandled" exception.
    # Consulting the same table here is what makes such a request its mapped
    # status rather than a 500, wherever the error was raised. One table, two
    # entry points.
    #
    # The auth seam's two errors no longer reach this handler — they have their
    # own registration above, so that a routine 401 does not pay for
    # ServerErrorMiddleware's re-raise. Everything else mapped still arrives
    # here, and the traceback note below is why that is tolerable for the rest:
    # they are rare, where an unverifiable caller is not.
    mapped = _public_mapped_error(request, exc)
    if mapped is not None:
        # Level still tracks status — an expected client-side refusal is not a
        # 5xx — but the traceback is emitted either way, matching how the
        # DomainError handler now treats every status.
        if mapped.status_code < 500:
            logger.warning(
                "[Public %s] %s on %s %s%s",
                mapped.status_code, type(exc).__name__, request.method,
                request.url.path, params_suffix(request),
                exc_info=exc,
            )
            return mapped
        logger.exception(
            "[Public %s] %s on %s %s%s",
            mapped.status_code, type(exc).__name__, request.method,
            request.url.path, params_suffix(request),
        )
        return mapped

    logger.exception(
        "[Unhandled exception] %s on %s %s%s",
        type(exc).__name__, request.method, request.url.path,
        params_suffix(request),
    )
    # The public surface guarantees the Envelope on every response, including
    # the ones nobody anticipated. Enumerating each new escapee in
    # ENVELOPE_ERRORS is whack-a-mole — services keep growing error types, and
    # transport failures (httpx, socket) are not domain errors at all. This
    # backstop closes the class: a specific mapping still gives a precise
    # status, and anything else at least stays in the contract.
    if _uses_envelope_contract(request):
        return _public_error_envelope(500, request)
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
app.include_router(user_list_router)
app.include_router(user_router)
app.include_router(org_user_router)  # GET /api/v1/org/user?user_id=<work_no>: directory identity, signed-principal auth
app.include_router(system_config_router)
app.include_router(common_config_router)
app.include_router(skills_pool_ops_router)
app.include_router(beta_quota_router)
app.include_router(channel_router)
app.include_router(quality_router)
app.include_router(task_internal_router)
app.include_router(task_callback_router)
app.include_router(task_router)
app.include_router(work_orders_http_router)
try:
    app.include_router(render_screen_router)
    logger.info("[RenderScreen] Router registered successfully: prefix=%s", render_screen_router.prefix)
except Exception as e:
    logger.exception("[RenderScreen] Failed to register router: %s", e)
app.include_router(antprocess_router)
app.include_router(antcode_router)  # AntCode 集成
app.include_router(bot_public_auth_router)
app.include_router(bot_public_router)
app.include_router(bot_public_noauth_router)  
app.include_router(workitem_noauth_router)  # 工作项接口 (route URL still /api/public/dima)
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
app.include_router(session_resources_router)
app.include_router(session_resources_internal_router)
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
app.include_router(spaces_internal_router)
app.include_router(service_bot_router)
app.include_router(service_bot_publish_router)
app.include_router(bot_collaborator_router)
app.include_router(skill_scan.router)
app.include_router(skill_auth.router)
app.include_router(skill_category.router)
app.include_router(verify.router)
app.include_router(sync.router)
app.include_router(batch_sync.router)
app.include_router(installations_internal.internal_router)
app.include_router(cron_router)
app.include_router(cron_noauth_router)
app.include_router(notify_router)
# Harness Engineering: patch template management & diagnosis
app.include_router(harness_router)
# Economy Governance: notification & audit
app.include_router(economy_governance_router)
app.include_router(economy_governance_admin_router)
app.include_router(economy_governance_workflow_router)
app.include_router(enums_router)
app.include_router(task_internal_router)
app.include_router(task_callback_router)

# Runtime-mode-conditional routers (bound by DI: empty in prod, populated
# in local boots via ``TestingInfrastructureModule``). The app does not
# branch on mode here — composition root decides what gets mounted.
from agentclaw.community.di.optional_routers import OptionalRouters  # noqa: E402
for _r in injector.get(OptionalRouters).routers:
    app.include_router(_r)

# 3. Public /openapi/v1/bots surface — new, definition-only routers for the
# redesigned external contract (not a re-mount of the handlers above; see
# adapters/http/openapi_v1). Handlers are stubs until the implementation lands.
from agentclaw.community.adapters.http.openapi_v1 import build_public_router  # noqa: E402
app.include_router(build_public_router())
