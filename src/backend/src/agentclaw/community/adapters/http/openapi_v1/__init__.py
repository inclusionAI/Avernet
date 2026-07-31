"""Public ``/openapi/v1/bots`` API surface — the redesigned external contract.

These are **new, purpose-built** routers for the redesigned API (definition
only; handlers are stubs). The gateway forwards ``/openapi/v1/bots/...`` here
verbatim and generates its served doc from this surface. This is distinct from —
and does not reuse — the legacy ``/api/...`` routers.

The sub-resource groups (channels, identity, mcp, resources, routines, skills)
are mounted **before** the bots group so their literal path segments
(``/openapi/v1/bots/channels`` …) resolve ahead of the agent-CRUD wildcard
``/openapi/v1/bots/{bot_id}``.
"""

from __future__ import annotations

from fastapi import APIRouter

from .bots import router as bots_router
from .contracts import ENGINE_RUNTIME_ERROR_RESPONSES, ERROR_RESPONSES
from .engine_runtime.approvals import router as engine_approvals_router
from .engine_runtime.connection import router as engine_connection_router
from .engine_runtime.engine import router as engine_engine_router
from .engine_runtime.models import router as engine_models_router
from .engine_runtime.sessions import router as engine_sessions_router
from .channels import router as channels_router
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
_SUBGROUPS = [
    channels_router,
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
# These sit one segment BELOW the `{bot_id}` wildcard, so their order relative
# to each other and to `_SUBGROUPS` is free — but they must still precede the
# bots router, which owns `/openapi/v1/bots/{bot_id}` itself.
_ENGINE_RUNTIME_GROUPS = [
    engine_sessions_router,
    engine_engine_router,
    engine_models_router,
    engine_approvals_router,
    engine_connection_router,
]


def build_public_router() -> APIRouter:
    """Assemble the ``/openapi/v1/bots`` public router.

    ``ERROR_RESPONSES`` is attached here rather than on each handler so the
    published schema documents the envelope this surface actually returns on
    failure — every group, every route, one declaration.
    """
    public = APIRouter()
    for router in _SUBGROUPS:
        public.include_router(router, responses=ERROR_RESPONSES)
    for router in _ENGINE_RUNTIME_GROUPS:
        public.include_router(router, responses=ENGINE_RUNTIME_ERROR_RESPONSES)
    public.include_router(bots_router, responses=ERROR_RESPONSES)
    return public


__all__ = ["build_public_router", "PUBLIC_API_PREFIX", "_ENGINE_RUNTIME_GROUPS"]
