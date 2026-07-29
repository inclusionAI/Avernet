"""HTTP middleware for the FastAPI app.

Extracted from ``api/app.py`` to keep the composition root focused on
wiring rather than middleware bodies (Rule 9 — single-purpose files).

Public entry point: :func:`install_middleware`. Callers (the
composition root in ``app.py``) pass in the resolved ``AuthPlugin``
and ``TracerPlugin``; this module owns the order in which middleware
are attached and the CORS origin list. Tracing lives behind the
``TracerPlugin`` capability — this module imports no tracer SDK.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    avernet_tenant_scope,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from agentclaw.community.di.config import CorsConfig
    from agentclaw.community.plugin_api.auth import AuthPlugin
    from agentclaw.community.plugin_api.tracer import TracerPlugin


logger = get_logger()


# Gateway appends this opaque compatibility value to only these Caller reads
# and writes. Strip it at the ASGI boundary before authentication, business
# middleware, and Uvicorn's completion-time access log can observe it.
_CTOKEN_COMPATIBILITY_PATHS = (
    re.compile(r"^/api/bots/[^/]+/caller-context$"),
    re.compile(r"^/api/bots/[^/]+/mcps/[^/]+/call-type$"),
    re.compile(r"^/api/v1/user-lists/(?:check|correct)$"),
)


def _remove_compatibility_ctoken(scope: dict) -> None:
    """Remove only the opaque gateway ``ctoken`` from opted-in endpoints.

    Mutating the original ASGI scope is intentional: subsequent request
    objects, auth context construction, and Uvicorn's access logger all use
    this same scope. Other query parameters remain available for normal
    validation, including the strict unknown-parameter checks on Caller APIs.
    """
    if scope.get("type") != "http":
        return
    path = scope.get("path", "")
    if not any(pattern.fullmatch(path) for pattern in _CTOKEN_COMPATIBILITY_PATHS):
        return

    raw_query_string = scope.get("query_string", b"")
    if not raw_query_string:
        return
    pairs = parse_qsl(
        raw_query_string.decode("latin-1"),
        keep_blank_values=True,
    )
    filtered_pairs = [(key, value) for key, value in pairs if key != "ctoken"]
    if len(filtered_pairs) == len(pairs):
        return
    scope["query_string"] = urlencode(filtered_pairs, doseq=True).encode("ascii")


class CompatibilityCtokenMiddleware(BaseHTTPMiddleware):
    """Erase gateway-only ctoken before any authentication or logging layer."""

    async def dispatch(self, request: Request, call_next):
        _remove_compatibility_ctoken(request.scope)
        return await call_next(request)


# =============================================================================
# UserContextMiddleware
# =============================================================================
class UserContextMiddleware(BaseHTTPMiddleware):
    """Inject the logged-in user into ``request.state`` for downstream
    consumers (OpenClawEnterprise etc.).

    Delegates identity resolution to the injected ``AuthPlugin`` (Rule
    14: mode never reaches this middleware — the right impl is bound by
    DI at composition root).
    """

    def __init__(self, app, auth_plugin):
        super().__init__(app)
        # auth_plugin is resolved at ``app.add_middleware`` time (see below).
        self._auth_plugin = auth_plugin

    async def dispatch(self, request: Request, call_next):
        # Auth failure must NOT block the request — leave user=None and
        # let routes decide whether to enforce auth.
        try:
            from agentclaw.community.adapters.http.auth.dependencies import _build_auth_context
            ctx = _build_auth_context(request)
            request.state.user = await self._auth_plugin.resolve_user_from_request(ctx)
        except Exception:
            request.state.user = None

        response = await call_next(request)
        return response


# =============================================================================
# AvernetTenantMiddleware
# =============================================================================
class AvernetTenantMiddleware:
    """Bind each request's data-isolation tenant for the request's lifetime.

    Public-API requests (``/openapi/v1/*``) resolve their tenant through the
    single seam ``resolve_avernet_tenant``; every other path — the internal API
    and anything non-public — is the default tenant. ``avernet_tenant_scope``
    resets on the way out (including on error), so a tenant never survives its
    request or leaks into the next one that reuses the worker.

    Deliberately a **pure ASGI middleware, not ``BaseHTTPMiddleware``**. The
    tenant lives in a ``ContextVar``, and ``BaseHTTPMiddleware`` has a fragile
    history with context propagation (it runs the downstream app in a child
    anyio task, so which context a ``set``/``reset`` lands in has depended on the
    Starlette version). A pure ASGI middleware sets the ``ContextVar`` in the
    exact coroutine/context that then awaits the downstream app and resets it in
    that same context — no task hop, so visibility downstream and a correct
    reset are guaranteed regardless of Starlette internals.

    Installed *outside* ``UserContextMiddleware`` (see ``install_middleware``) so
    the auth plugin's own DB reads already run under the request's tenant.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope["path"].startswith("/openapi/v1/"):
            # Lazy import: the openapi_v1 package pulls in every public router,
            # so keep it off the middleware module-load path.
            from agentclaw.community.adapters.http.openapi_v1.dependencies import (
                resolve_avernet_tenant,
            )
            tenant = resolve_avernet_tenant(Request(scope))
        else:
            tenant = DEFAULT_AVERNET_TENANT

        with avernet_tenant_scope(tenant):
            await self.app(scope, receive, send)


# =============================================================================
# Trace-id middleware (maps the tracer's trace id → response header)
# =============================================================================
class TraceIdMappingMiddleware(BaseHTTPMiddleware):
    """Resolve a trace_id from the injected ``TracerPlugin``, stash it on
    ``request.state``, and echo it back on every response as ``X-Trace-ID``
    — including error responses.

    The trace id comes from ``tracer.current_trace_id()`` (Rule 14: the right
    tracer is bound by DI at the composition root — corp reads the active
    span, community mints a per-request id, local/test returns ``None`` so no
    header is emitted, exactly the pre-seam behavior).

    Stashing on ``request.state`` lets the exception handlers in
    ``api/app.py`` add the same header to their JSONResponse as
    belt-and-suspenders: even if a future middleware ordering change
    means this middleware doesn't see the response, the handler still
    emits the trace_id.

    The tracer is supplied at ``app.add_middleware`` time (see
    ``install_middleware``); the tracer's own middleware is installed
    *outside* this one, so the trace context exists by the time
    ``dispatch`` reads it.
    """

    def __init__(self, app, *, tracer: "TracerPlugin"):
        super().__init__(app)
        self._tracer = tracer

    async def dispatch(self, request: Request, call_next):
        trace_id = self._tracer.current_trace_id()
        request.state.trace_id = trace_id

        request_id = request.headers.get("X-Request-ID")
        if trace_id and request_id:
            logger.info(
                "trace_mapping request_id=%s trace_id=%s path=%s",
                request_id, trace_id, request.url.path,
            )

        response = await call_next(request)
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id
        return response


# =============================================================================
# CORS with regex-pattern support
# =============================================================================
class RegexCORSMiddleware(CORSMiddleware):
    """CORS middleware that accepts both fixed origins and regex patterns.

    Overrides ``__call__`` so a regex match dynamically adds the origin to
    ``allow_origins`` for the duration of the call (then restores the
    list to avoid unbounded growth).
    """

    def __init__(self, app, allow_origin_regex: list[str] | None = None, **kwargs):
        # 保存正则模式
        self._allow_origin_regex = allow_origin_regex or []
        self._compiled_patterns = [re.compile(pattern) for pattern in self._allow_origin_regex]
        super().__init__(app, **kwargs)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request as StarletteRequest
        request = StarletteRequest(scope)
        origin = request.headers.get("origin")

        # 如果 origin 匹配正则，临时添加到允许列表
        if origin and self._compiled_patterns:
            if any(pattern.match(origin) for pattern in self._compiled_patterns):
                # 保存原始值
                original_origins = self.allow_origins[:]
                # 添加匹配的 origin
                if origin not in self.allow_origins:
                    self.allow_origins.append(origin)
                # 调用父类处理
                await super().__call__(scope, receive, send)
                # 恢复原始列表（避免累积）
                self.allow_origins = original_origins
                return

        # 否则走默认逻辑
        await super().__call__(scope, receive, send)


# =============================================================================
# Public entry point
# =============================================================================
def install_middleware(
    app: "FastAPI",
    *,
    auth_plugin: "AuthPlugin",
    tracer: "TracerPlugin",
    cors_config: "CorsConfig | None" = None,
) -> None:
    """Attach the project's middleware stack to ``app``.

    The CORS allow-list comes from the injected :class:`CorsConfig` (the ``cors``
    yaml block) — corp env overlays carry the corp browser origins; a community
    deployment lists its own. When ``cors_config`` is ``None`` (e.g. a hand-rolled
    test consumer), the neutral :class:`CorsConfig` default (localhost origins) is
    used.

    Order matters: CORS first (so preflights short-circuit before auth),
    then UserContextMiddleware, then TraceIdMappingMiddleware, then the
    tracer's own middleware via ``tracer.install(app)``.

    Starlette ``add_middleware`` prepends, so the tracer (installed last)
    is *outermost* — it establishes the trace context before
    TraceIdMappingMiddleware reads it. The tracer impl decides what, if
    anything, to attach (corp: SofaTracer middleware; community: a
    per-request id middleware; local/test: nothing).
    """
    from agentclaw.community.di.config import CorsConfig

    cors = cors_config if cors_config is not None else CorsConfig()
    app.add_middleware(
        RegexCORSMiddleware,
        allow_origins=list(cors.allow_origins),
        allow_origin_regex=list(cors.allow_origin_regex),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注入用户上下文中间件（在 CORS 之后，tracer 之前）
    app.add_middleware(UserContextMiddleware, auth_plugin=auth_plugin)

    # Establish the request's avernet_tenant. Added right after (so, outside)
    # UserContextMiddleware — Starlette prepends, so a later add is outer — so
    # the auth plugin's own DB reads run under the request's tenant. No injected
    # dependency: resolve_avernet_tenant is one function for every profile.
    app.add_middleware(AvernetTenantMiddleware)

    # 关联前端 X-Request-ID 与 trace id（在 tracer 中间件内部运行）
    app.add_middleware(TraceIdMappingMiddleware, tracer=tracer)

    # 安装 tracer 插件的中间件（由 DI 按 profile 绑定的实现决定行为）
    tracer.install(app)

    # Add last so it is outermost: it must sanitize the shared ASGI scope
    # before tracer, auth context, and default access logging use it.
    app.add_middleware(CompatibilityCtokenMiddleware)
