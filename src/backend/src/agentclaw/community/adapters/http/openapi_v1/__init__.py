"""Public ``/openapi/v1/bots`` API surface — the redesigned external contract.

These are **new, purpose-built** routers for the redesigned API (definition
only; handlers are stubs). The gateway forwards ``/openapi/v1/bots/...`` here
verbatim and generates its served doc from this surface. This is distinct from —
and does not reuse — the legacy ``/api/...`` routers.

Addressing rule
---------------

Every bot-scoped operation is addressed
``/openapi/v1/bots/{bot_id}/<component>/…``: the bot comes first, and the
component's literal name hangs off it. A bot is the noun this API is about, so
the address names the bot before it names what about the bot, and every
operation on one bot shares one prefix.

The bot is therefore always a **path** parameter — never a query parameter,
never a body field. Which bot an operation acts on is the address, not an
argument to the call, and a client that must be told to put the same id in two
places has been told the address twice.

That is also what makes authorization mostly mechanical rather than
per-operation. The grant dependencies (``require_granted_own_bot`` /
``require_granted_addressed_bot`` — one per grant-checked admission mode, so
the declaration says which id model a route has) read the addressed bot off
the path, the same way, for all but four operations on the surface.

**The four are not an oversight, and their handler-side checks are
load-bearing.** The ``{skill_id}`` skills operations resolve by
``(skill, actor)``, so the addressed bot's *owner* arrives on the record rather
than on the wire — a collaborator reaches a skill on someone else's bot
routinely — and there is nothing for the shared dependency to look a grant up
against until that read has happened. They are named in
``admission.SKILL_SCOPED_OPERATIONS``, mounted without the group-level
dependency rather than exempted from one, and they check the grant themselves.
**Deleting those checks as obsolete would leave the four unauthorized; the
dependency does not cover them.**

So ``TODO(#960)`` is *narrowed* by this rule, from seven operations to four, and
stays open — see ``principal.py`` and ``admission.SKILL_SCOPED_OPERATIONS``.

The account-level operations are the ones with no single bot to name: creating
a bot, listing them, checking a name, the ``authorized`` groups, the tenant-wide
MCP catalogue, the trace query (which reads *across* bots), and the load-test
endpoints. They keep a literal where a bot id would otherwise be read, and they
are the only things that do.

Because ``{bot_id}`` is a single wildcard segment, a bot whose id equals a
literal served in that position is unreachable at that address. Bot-first
addressing is what keeps that list short: every bot-scoped component name moved
one segment deeper, where it collides with nothing, leaving only the
account-level literals above. The names are documented in
``docs/openapi-v1/README.md``; a test asserts the doc's list still equals the
routes'.

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

Who may call: a person, or an application acting for one
--------------------------------------------------------

Two caller shapes reach this surface, and the difference is whether a human is
on the wire at all.

A **person** is the ordinary case and is unchanged by everything below: their
``user_id`` must name themselves, and every read and write is scoped to them.

An **application acting alone** presents its own credential and no end user. It
names the user it acts for in the same ``user_id`` parameter, and that parameter
is *authorized against a grant* — a record saying "this application may act as
this person on this bot" (``core/bot_app_grant``) — rather than compared with a
caller that is not there. The application then inherits exactly that person's
access, re-adjudicated on every request by the same gates they would face. It is
never more: nothing about their authority is stored in the grant, so there is
nothing to go stale, and the application loses a bot the moment the person does.

**Which operations admit it is a per-operation decision, written down.**
``admission.py`` holds one entry per operation; an operation absent from that
table refuses a machine caller, so a route added later is refused by omission
rather than by someone remembering not to opt in.
``test_admission_inventory.py`` is what makes the omission loud.

Refusals are indistinguishable on purpose. An application that reaches an
operation it may not gets the same ``401`` an unauthenticated caller gets; one
that names a bot it holds no grant for gets the same ``404`` a nonexistent bot
gets, byte for byte. Anything finer would be an enumeration oracle.

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

Retiring addresses
------------------

Nothing was removed. Every address this surface answered before bot-first
addressing still answers, at the same shape, with the same parameters in the
same places — including the ones this rule exists to remove, a ``bot_id`` in
the query string and one in a request body. They live in ``deprecated/``,
publish ``deprecated: true`` in the document, and answer with ``Deprecation``
and ``Sunset`` headers (``deprecation.py``).

The window runs from **2026-08-15** to **2027-08-15**. Removal is driven by
traffic rather than by the date — the access log says when an address has no
callers left, and that is when it goes — so the sunset is the outer bound a
client can plan against, not a countdown. ``test_legacy_parity.py`` asserts
each retiring address and its replacement reach the same decision, which is the
whole of the compatibility promise.

Mount order
-----------

The sub-resource groups are mounted **before** the bots group, and the retiring
addresses before both. The wildcard ``/openapi/v1/bots/{bot_id}`` matches any
single segment, so a group publishing a literal there resolves as "the bot
named ``resources``" if the bots router is reached first.

Under bot-first addressing that hazard is confined to the groups above that
genuinely keep a literal in the ``{bot_id}`` segment — the account-level ones,
and the retiring addresses, which are literal-first by definition. The current
bot-scoped groups are all ``{bot_id}``-first and could be mounted in any order.
One rule for all of them is still cheaper than a per-group exception a later
edit would get wrong.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .authorized_apps import app_view_router as authorized_bots_router
from .authorized_apps import router as authorized_apps_router
from .bots import router as bots_router
from .bots.engine_config import router as engine_config_router
from .deprecated import (
    ENGINE_RUNTIME_GROUPS as _LEGACY_ENGINE_RUNTIME,
    GRANT_CHECKED_GROUPS as _LEGACY_GRANT_CHECKED,
    SELF_CHECKED_GROUPS as _LEGACY_SELF_CHECKED,
)
from .contracts import (
    ENGINE_RUNTIME_ERROR_RESPONSES,
    ERROR_RESPONSES,
    USER_SCOPED_ERROR_RESPONSES,
)
from .dependencies import require_principal
from .principal import require_granted_addressed_bot, require_granted_own_bot
from .engine_runtime.approvals import router as engine_approvals_router
from .engine_runtime.connection import router as engine_connection_router
from .engine_runtime.engine import router as engine_engine_router
from .engine_runtime.models import router as engine_models_router
from .engine_runtime.sessions import router as engine_sessions_router
from .identity import router as identity_router
from .local import router as local_router
from .loadtest import router as loadtest_router
from .mcp import router as mcp_router
from .bot_logs import router as logs_router
from .resources import router as resources_router
from .routines import router as routines_router
from .skills import router as skills_router
from .service_publications import (
    edit_lock_router as service_edit_lock_router,
    router as service_lifecycle_router,
)

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

# Order matters: every group in these lists is registered before the `{bot_id}`
# wildcard group, which `build_public_router` mounts last. Under bot-first
# addressing most of them no longer depend on it — they open with `{bot_id}`
# themselves — but the account-level literals below still do, and so do the
# retiring addresses. See "Mount order" above. Splitting them across lists is
# about which *response table* each gets; it does not change that they all
# precede `bots`.
_SUBGROUPS = [
    # Both authorization groups precede `bots` below. `authorized_apps_router`
    # sits *under* `{bot_id}` so path shape already keeps it distinct, but
    # `authorized_bots_router` is a top-level literal and genuinely depends on
    # this order: nothing else stops a future `/openapi/v1/{something}` from
    # claiming it.
    authorized_apps_router,
    authorized_bots_router,
    # Mixed, like `bots`: its two collection operations declare the grant check
    # per route, and its four `{skill_id}` operations resolve the bot's owner
    # from the skill record and check it themselves. A group-level dependency
    # here would refuse an application holding a valid grant on a *shared* bot,
    # because it would look the grant up against the delegating user rather than
    # the owner. See `skills/router.py` and `admission.SKILL_SCOPED_OPERATIONS`.
    skills_router,
    # Local workflows are human-only rather than grant-checked. Their admission
    # entries and gateway route security refuse application-only callers.
    local_router,
    # These groups resolve the addressed owner through OwnerIdDep, which also
    # performs the application-grant check. Their handlers then enforce the
    # live Bot collaborator relation at member level before any publication read.
    service_lifecycle_router,
    service_edit_lock_router,
]

# The groups where **every** route is GRANT_CHECKED_OWN_BOT — it names a bot and resolves it
# owner-scoped — so the grant check can be declared once for the group instead
# of on each of its 25 routes.
#
# Declared per group rather than surface-wide because the check is not a no-op
# everywhere: on an operation that names no bot it would refuse an application
# outright, which is exactly wrong for the listings (Mode B) and the
# account-level reads (C/OPEN). `bots` is mixed and declares it per route;
# `admission.py` is the authority on which is which, and
# `test_admission_inventory.py` fails if a route's declaration and its mode
# disagree.
#
# The engine-runtime groups are not here because they are not own-bot: they may
# address a shared bot, so their mount below declares the *addressed-bot*
# dependency instead.
_GRANT_CHECKED_SUBGROUPS = [
    # Engine config is bots-component work at an engine-component address: it
    # reads and writes a stored blob, so it takes the ordinary error table
    # rather than the engine-runtime one, and mounting it here is what gives it
    # both. See ``bots/engine_config.py``.
    engine_config_router,
    identity_router,
    resources_router,
    routines_router,
]

# Track C — the engine-runtime groups. Mounted separately because they document
# two extra failure statuses (501/504) that the rest of the surface cannot
# return; attaching those surface-wide would make every already-shipped category
# advertise failures it cannot produce.
#
# Each is `{bot_id}`-first (`/openapi/v1/bots/{bot_id}/sessions` …) and their
# paths diverge at the segment after it, so they cannot shadow one another and
# their order relative to each other and to `_SUBGROUPS` is free. They are still
# mounted before the bots router for the one rule stated above, not because any
# of them needs it.
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

# The bot authorization for an application caller, for the groups that are
# wholly own-bot. A no-op for a caller that names an end user — their own
# operation's owner-scoped resolve already refuses a bot that is not theirs, and
# re-deciding it here would risk a second, different answer.
_GRANT_CHECKED_OWN_BOT = [Depends(require_granted_own_bot)]

# The same authorization for the groups that may address a *shared* bot: the
# grant is checked against the `owner_id` the request names (defaulting to the
# caller) rather than pinned to the caller. Which of the two a mount gets is the
# route's admission mode made visible — `admission.py` records the decision, and
# `test_admission_inventory.py` fails if a mount and a mode disagree.
_GRANT_CHECKED_ADDRESSED_BOT = [Depends(require_granted_addressed_bot)]


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
    for router in _GRANT_CHECKED_SUBGROUPS:
        public.include_router(
            router,
            responses=USER_SCOPED_ERROR_RESPONSES,
            dependencies=_PUBLIC_AUTH + _GRANT_CHECKED_OWN_BOT,
        )
    # The engine-runtime groups already run this exact check transitively —
    # their `OwnerIdDep` consumes the owner it returns — so declaring it at the
    # mount adds no second lookup (FastAPI caches a dependency per request).
    # It is declared anyway so the mount says what governs the group, the same
    # way the own-bot mounts above do, instead of the check being visible only
    # inside `engine_runtime/params.py`.
    for router in _ENGINE_RUNTIME_GROUPS:
        public.include_router(
            router,
            responses=ENGINE_RUNTIME_ERROR_RESPONSES,
            dependencies=_PUBLIC_AUTH + _GRANT_CHECKED_ADDRESSED_BOT,
        )
    # The addresses this surface used to have. Each legacy group is mounted the
    # way its replacement is, because the mount decides half of what a caller
    # experiences and parity means the old address answers as it always did.
    # The exception is `skills`, whose retiring item operations name no bot for
    # the grant dependencies to read and so check it themselves — see
    # `deprecated/__init__.py`.
    for router in _LEGACY_ENGINE_RUNTIME:
        public.include_router(
            router,
            responses=ENGINE_RUNTIME_ERROR_RESPONSES,
            dependencies=_PUBLIC_AUTH + _GRANT_CHECKED_ADDRESSED_BOT,
        )
    for router in _LEGACY_GRANT_CHECKED:
        public.include_router(
            router,
            responses=USER_SCOPED_ERROR_RESPONSES,
            dependencies=_PUBLIC_AUTH + _GRANT_CHECKED_OWN_BOT,
        )
    for router in _LEGACY_SELF_CHECKED:
        public.include_router(
            router, responses=USER_SCOPED_ERROR_RESPONSES, dependencies=_PUBLIC_AUTH
        )

    # `bots` is mixed too, but stays last for the wildcard-ordering rule above.
    public.include_router(
        bots_router, responses=ERROR_RESPONSES, dependencies=_PUBLIC_AUTH
    )
    return public


__all__ = ["build_public_router", "PUBLIC_API_PREFIX", "_ENGINE_RUNTIME_GROUPS"]
