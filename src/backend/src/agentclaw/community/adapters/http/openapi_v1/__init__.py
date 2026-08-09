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

Naming the end user
-------------------

Every operation that scopes to a user takes a required **``user_id`` query
parameter** — whatever the method, whatever the body. It is never a body field
and never a path segment: it is not an attribute of any resource here, it is who
the call is for, so a body (which describes the resource) and a path (which
names it) are both the wrong place. ``bot_id`` is unaffected — it stays in the
path where it addresses a bot and in the query string where it is a parameter.
The full reasoning is in ``principal.py``; handlers declare ``UserIdDep``.

Four operations are exempt because they have no user dimension to scope by:
``check_bot_name`` answers a tenant-wide uniqueness question, and
``list_mcp_servers`` / ``list_mcp_tenants`` / ``get_mcp_server`` read a
marketplace catalogue that is identical for every caller in the tenant. They
still require an authenticated caller — ``_PUBLIC_AUTH`` below — they just have
no user-shaped answer to give. The load-test group is exempt on the same
grounds and for the plainest reason of all: it reads and writes nothing.

Planes
------

Every group here served HTTP only until the load-test group added a WebSocket.
That changes nothing about the rule above — ``_PUBLIC_AUTH`` reaches a socket
route as it reaches any other, and ``require_principal`` verifies the same
signed header the gateway puts in a handshake — but two mechanical facts follow
from it. A WebSocket route has no OpenAPI representation, so it appears in none
of the generated-document assertions and its address is governed by
``test_loadtest_endpoints.py`` instead; and the response tables below are an
HTTP notion, so attaching one to a group with socket routes describes only its
HTTP half.

One thing the socket plane does **not** currently get: ``AvernetTenantMiddleware``
and the public access log both return early on a non-HTTP scope, so a WebSocket
runs under the *default* tenant and leaves no access line. That is harmless for
a route that reads and writes nothing — the only socket route here — and it is
the first thing to fix before adding one that does, because a tenant-scoped
read under the default tenant is a data-isolation failure, not a missing log.

The Bot Logs group is a different exclusion, and the sharpest thing to know
about this rule. ``GET …/bots/logs/traces`` already takes a required
``user_id`` — but there it means *whose traces to read*, a filter, and any
caller presenting both a user and an App identity may name someone else's. Here
it means *whose call this is*, and naming someone else is a 403. Same spelling,
opposite contract, and the published document will carry both. Do not "unify"
them without deciding which meaning the address should have.

Because of those exemptions the dependency is declared **per handler**, not per
group: two of the four sit inside groups whose other routes do take it (1 of 13
in ``bots``, 3 of 6 in ``mcp``), so a group-level dependency would put the
parameter on them. ``test_explicit_user_id.py`` is what makes a new route
impossible to forget — the same trade ``test_path_convention.py`` makes for the
addressing rule above.

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
from .contracts import (
    ENGINE_RUNTIME_ERROR_RESPONSES,
    ERROR_RESPONSES,
    USER_SCOPED_ERROR_RESPONSES,
)
from .dependencies import require_principal
from .engine_runtime.approvals import router as engine_approvals_router
from .engine_runtime.connection import router as engine_connection_router
from .engine_runtime.engine import router as engine_engine_router
from .engine_runtime.models import router as engine_models_router
from .engine_runtime.sessions import router as engine_sessions_router
from .identity import router as identity_router
from .loadtest import router as loadtest_router
from .mcp import router as mcp_router
from .bot_logs import router as logs_router
from .resources import router as resources_router
from .routines import router as routines_router
from .skills import router as skills_router

# Every public route lives under this prefix. Exported so app-level handlers can
# tell a public request from an internal one (e.g. to envelope validation errors
# only on this surface).
PUBLIC_API_PREFIX = "/openapi/v1"

# The groups that answer no 403, because no route in them is scoped by the
# *caller's* user: Bot Logs never derived a user from the credential at all, and
# the load-test group has nothing to scope — its two endpoints answer a constant
# and echo their input, so they touch no user's data on the way.
#
# It is not that Bot Logs has no `user_id` — `GET …/bots/logs/traces` has taken
# a required one since #692. It means something else there, and the difference
# is why that group stays out of this rule rather than being folded into it: on
# the rest of the surface `user_id` is *whose call this is*, and must be the
# caller (403 otherwise); on that one operation it is *whose traces to read*, a
# filter over a tenant-level observability surface that already requires both a
# user and an App identity (`route_security`, `require_user_and_app_principal`).
# Bringing it under this rule would remove a capability that route exists to
# provide. See the note in the spec's Out of Scope.
_GROUPS_WITHOUT_CALLER_SCOPE = [
    logs_router,
    loadtest_router,
]

# The groups where SOME routes take a `user_id` and some do not — `bots`
# (12 of 13) and `mcp` (3 of 6). They keep the surface-wide response table here
# and declare the 403 per route, so the four exempt operations do not advertise
# a failure they cannot produce. See "Naming the end user" above.
_MIXED_GROUPS = [
    mcp_router,
]

# Order matters: every literal sub-group — these three lists — is registered
# before the `{bot_id}` wildcard group, which `build_public_router` mounts last.
# See "Mount order" above for which literals actually depend on it. Splitting the
# literals across three lists is about which *response table* each gets; it does
# not change that they all precede `bots`.
_SUBGROUPS = [
    identity_router,
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

    The response tables and ``_PUBLIC_AUTH`` are attached here rather than on
    each handler so the published schema documents the envelope this surface
    actually returns on failure, and so every route requires an authenticated
    caller — every group, every route, one declaration.

    Which response table a group gets depends on whether all of its routes are
    user-scoped. The two mixed groups declare their 403 per route instead; the
    ``user_id`` parameter itself is always per handler (see "Naming the end
    user" above).
    """
    public = APIRouter()
    for router in _GROUPS_WITHOUT_CALLER_SCOPE + _MIXED_GROUPS:
        public.include_router(
            router, responses=ERROR_RESPONSES, dependencies=_PUBLIC_AUTH
        )
    for router in _SUBGROUPS:
        public.include_router(
            router, responses=USER_SCOPED_ERROR_RESPONSES, dependencies=_PUBLIC_AUTH
        )
    for router in _ENGINE_RUNTIME_GROUPS:
        public.include_router(
            router,
            responses=ENGINE_RUNTIME_ERROR_RESPONSES,
            dependencies=_PUBLIC_AUTH,
        )
    # `bots` is mixed too, but stays last for the wildcard-ordering rule above.
    public.include_router(
        bots_router, responses=ERROR_RESPONSES, dependencies=_PUBLIC_AUTH
    )
    return public


__all__ = ["build_public_router", "PUBLIC_API_PREFIX", "_ENGINE_RUNTIME_GROUPS"]
