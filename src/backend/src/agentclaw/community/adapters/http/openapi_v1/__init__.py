"""Public ``/openapi/v1/bots`` API surface — the redesigned external contract.

These are **new, purpose-built** routers for the redesigned API (definition
only; handlers are stubs). The gateway forwards ``/openapi/v1/bots/...`` here
verbatim and generates its served doc from this surface. This is distinct from —
and does not reuse — the legacy ``/api/...`` routers.

Addressing rule
---------------

Every operation is addressed ``/openapi/v1/bots/<component>/…``: the component's
**literal** name comes first, and a bot-scoped operation takes ``{bot_id}`` as
the first segment *after* it — never before it, and never with a ``/bot/``
segment in between. The ``bots`` component is the one exception and only
because it *is* the component the base names: it owns ``/openapi/v1/bots`` and
``/openapi/v1/bots/{bot_id}``, and its own sub-resources (``/status``,
``/passport``, ``/restart``, ``/auth-status``, ``/engine-config``) hang off the
bot record beneath it.

The rule exists so a router file states its own address. Under the old shape a
reader of ``engine_runtime/sessions/router.py`` could not tell whether
``/openapi/v1/bots/{bot_id}/sessions`` was served there or by a
``{bot_id}``-shaped route in the bots component, and a second owner under the
same base could not be added at all — the reason BCS moved its own control
plane to ``/openapi/v1/bots/collaboration/{bot_id}``.

Because the bots component keeps the bare ``/openapi/v1/bots/{bot_id}``, a bot
whose id equals a component name is unreachable at that address. The reserved
names are fixed and documented in ``docs/openapi-v1/README.md``; a test asserts
the doc's list still equals the routes'.

Mount order
-----------

The sub-resource groups are mounted **before** the bots group. The wildcard
``/openapi/v1/bots/{bot_id}`` matches any single segment, so the groups that
publish a single-segment literal — ``resources`` and ``routines``, which serve
their own collection roots — would otherwise resolve as "the bot named
``resources``". Every other component is only reachable at two segments or
more, so ordering no longer decides its fate; keeping one rule for all of them
is cheaper than a per-group exception a later edit would get wrong.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .bots import router as bots_router
from .contracts import ENGINE_RUNTIME_ERROR_RESPONSES, ERROR_RESPONSES
from .dependencies import require_principal
from .engine_runtime.approvals import router as engine_approvals_router
from .engine_runtime.connection import router as engine_connection_router
from .engine_runtime.engine import router as engine_engine_router
from .engine_runtime.models import router as engine_models_router
from .engine_runtime.sessions import router as engine_sessions_router
from .identity import router as identity_router
from .mcp import router as mcp_router
from .resources import router as resources_router
from .routines import router as routines_router
from .skills import router as skills_router

# Every public route lives under this prefix. Exported so app-level handlers can
# tell a public request from an internal one (e.g. to envelope validation errors
# only on this surface).
PUBLIC_API_PREFIX = "/openapi/v1"

# Order matters: literal sub-groups first, the `{bot_id}` wildcard group last.
# See "Mount order" above for which literals actually depend on it.
_SUBGROUPS = [
    identity_router,
    mcp_router,
    resources_router,
    routines_router,
    skills_router,
]

# Track C — the engine-runtime groups. Mounted separately because they document
# two extra failure statuses (501/504) that the rest of the surface cannot
# return; attaching those surface-wide would make every already-shipped category
# advertise failures it cannot produce.
#
# Each is now literal-prefixed (`/openapi/v1/bots/sessions/{bot_id}` …), so they
# cannot shadow one another and their order relative to each other and to
# `_SUBGROUPS` is free. They are still mounted before the bots router for the
# one rule stated above, not because any of them needs it.
_ENGINE_RUNTIME_GROUPS = [
    engine_sessions_router,
    engine_engine_router,
    engine_models_router,
    engine_approvals_router,
    engine_connection_router,
]

# Authentication for the whole surface, declared once. Every handler already
# takes ``principal: PrincipalDep``, so this changes nothing today — its value is
# that a route added later cannot *omit* it. Verification is what refuses a
# caller this surface cannot scope (``core/gateway_principal/verifier.py``
# admits only identity sets naming an end user), and a route that never reaches
# that check is a route the refusal does not cover.
#
# Declaring it in both places costs one call, not two: FastAPI caches a
# dependency's result per request, so a handler's own ``PrincipalDep`` resolves
# to this same invocation.
_PUBLIC_AUTH = [Depends(require_principal)]


def build_public_router() -> APIRouter:
    """Assemble the ``/openapi/v1/bots`` public router.

    ``ERROR_RESPONSES`` and ``_PUBLIC_AUTH`` are attached here rather than on
    each handler so the published schema documents the envelope this surface
    actually returns on failure, and so every route requires an authenticated
    caller — every group, every route, one declaration.
    """
    public = APIRouter()
    for router in _SUBGROUPS:
        public.include_router(
            router, responses=ERROR_RESPONSES, dependencies=_PUBLIC_AUTH
        )
    for router in _ENGINE_RUNTIME_GROUPS:
        public.include_router(
            router,
            responses=ENGINE_RUNTIME_ERROR_RESPONSES,
            dependencies=_PUBLIC_AUTH,
        )
    public.include_router(
        bots_router, responses=ERROR_RESPONSES, dependencies=_PUBLIC_AUTH
    )
    return public


__all__ = ["build_public_router", "PUBLIC_API_PREFIX", "_ENGINE_RUNTIME_GROUPS"]
