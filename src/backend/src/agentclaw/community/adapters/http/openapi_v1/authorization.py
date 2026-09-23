"""Who may do what to a bot, for every operation on this surface.

One table, and it is the **only** place an operation's collaborator
authorization is declared. A handler declares nothing; the route class below
reads this table and attaches what the row calls for. That is a deliberate
reversal of the convention its neighbour ``admission.py`` follows, where each
route also names its own dependency and a test holds the two in step. The
reversal buys one thing: a single source. There is no second declaration to
drift, and no way for a route to disagree with the table because a route has
nothing to say.

**Omission is not survivable**: ``PublicAPIRoute`` rejects an absent operation
while its module imports, so a missing row never becomes "no check needed".
A CI assertion catches the same mistake one step later.

Two modes are permanent
-----------------------

- :class:`Check` — verify the caller's level on the bot the operation
  addresses. The level is a parameter rather than a further mode because the
  bars genuinely differ per operation: MEMBER to drive a bot's sessions,
  ADMIN to write a channel, OWNER to restart a container's instance or
  delete the bot.
- :class:`NoCheck` — nothing to verify, and the reason says which kind of
  nothing. Either the operation addresses no bot (a name check, the
  marketplace, the caller's own identity), or it is bot-scoped and
  intentionally unguarded. The second really exists — render-screen reads serve
  share viewers who hold no Editor relation — and without a written reason a
  reviewer cannot tell it from an oversight.

Three modes are scaffolding
---------------------------

They exist only while operations are on their way to one of the two above, and
each is deleted when its last row leaves it:

- :class:`ServiceChecked` — a service still enforces this, somewhere else.
  → becomes ``Check(level)`` when that group migrates.
- :data:`OWNER_SCOPED` — no collaborator dimension has been decided; the
  operation resolves the bot as ``(bot_id, caller)``, so only the owner reaches
  it. → becomes ``Check(level)`` when #906 / #907 decide the bar.
- :data:`INHERITED` — a retiring address under ``deprecated/``, which is the
  replacement's own endpoint function re-registered at the path it used to
  have. It holds no decision: whatever governs the address that replaced it
  governs this one. → disappears with that package.

:func:`scaffolding_row_count` reports how many rows are still in them, so the
migration's remaining distance is a number rather than an impression.

**The levels on ``ServiceChecked`` rows are recorded, not enforced.** They were
read off the modules they cite, and nothing here can prove them: the inventory
test checks that the citation resolves to a module performing a permission
check, which is not the same as checking the number. Re-read the cited module
when you migrate a row; do not trust this column to be the whole truth.

Two things stay out of this table on purpose. Bot-*type* gating
(``SUPPORTED_BOT_TYPES``, answered 501) is a capability question, not an
authorization one. And whether a *machine* caller is admitted at all is
``admission.py``'s question, with its own seam and its own dependencies.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, get_args, get_origin, get_type_hints

from agentclaw.community.core.bot_collaborator.models import (
    PermissionLevel as PermissionLevel,
)
from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute

from .authorization_rules import (
    AUTHORIZATION,
    EDIT_LOCK,
    INHERITED,
    OWNER_SCOPED,
    SCAFFOLDING_MODES,
    Authorization,
    Check,
    NoCheck,
    ServiceChecked,
)

#: Operations whose router exists but which ``build_public_router`` does not
#: mount, so they are in the table without being on the surface.
#:
#: Empty today. The ``openapi_v1/task`` surface used to live here: its twin
#: ``/api/v1`` router answered under ``/api/v1/collaboration/tasks`` in
#: ``adapters/http/task``, while the ``/openapi/v1`` twin stayed unmounted until
#: the gateway's configuration declared the collaboration-tasks domain. That
#: domain is now declared (gateway ``collaboration-tasks`` → backend) and the
#: ``/openapi/v1`` twin is mounted in ``build_public_router``, so the three
#: task operations became live and their rows are real decisions, not
#: placeholders.
#:
#: They carry rows anyway, and that is the point. The router is built with
#: ``PublicAPIRoute`` like every other, so **whoever mounts it later cannot do
#: so unguarded** — they will have to replace these placeholder rows with a
#: real decision. Leaving the router without a route class would have been the
#: easy fix and the wrong one: it would mount silently unchecked.
#:
#: :func:`assert_every_route_authorized` subtracts these before reporting
#: orphans, so an unmounted row is not mistaken for a decision left behind by a
#: rename. Delete an entry the moment its operation is mounted.
UNMOUNTED_OPERATIONS = frozenset()


def scaffolding_row_count() -> int:
    """How many operations are still in a mode that must eventually be empty.

    The migration's burn-down. When this reaches zero every operation is either
    ``Check`` or ``NoCheck`` and the three scaffolding modes can be deleted
    along with this function.
    """
    return sum(
        1 for rule in AUTHORIZATION.values() if isinstance(rule, SCAFFOLDING_MODES)
    )


__all__ = [
    "AUTHORIZATION",
    "Authorization",
    "Check",
    "EDIT_LOCK",
    "INHERITED",
    "NoCheck",
    "OWNER_SCOPED",
    "SCAFFOLDING_MODES",
    "UNMOUNTED_OPERATIONS",
    "ServiceChecked",
    "scaffolding_row_count",
]


class PublicRouteNotAuthorized(RuntimeError):
    """A public operation was constructed without a row in :data:`AUTHORIZATION`.

    Raised while the route's own module is importing, so the application never
    starts. That is the whole fail-closed property: a new operation is refused
    until someone decides what governs it, rather than served because nobody
    noticed. Nothing catches it — there is no "continue without a decision".
    """


class PublicAPIRoute(APIRoute):
    """The route type every ``/openapi/v1`` router is built with.

    Reads the operation's row and attaches what it calls for, so a handler
    never declares its own authorization and cannot opt out of it. A row that
    is absent raises rather than defaulting to anything.

    This runs at *decoration* time — when ``@router.get(...)`` constructs the
    route — which is earlier than assembly and earlier than the first request.
    """

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        rule = _rule_for(path, kwargs.get("methods"))
        if isinstance(rule, Check):
            # Imported here, not at module scope: ``bot_access`` reads ``Check``
            # off this module, so a top-level import would be a cycle. The cost
            # is one lookup per adjudicated route at import time, the cheapest
            # place to pay it.
            from agentclaw.community.adapters.http.openapi_v1.bot_access import (
                require_check,
            )

            kwargs["dependencies"] = [
                *(kwargs.get("dependencies") or []),
                Depends(require_check(rule)),
            ]
        if isinstance(rule, Check) and rule.edit_lock is EDIT_LOCK:
            from agentclaw.community.adapters.http.openapi_v1.contracts import (
                ErrorEnvelope,
                error_example,
            )

            responses = dict(kwargs.get("responses") or {})
            responses.setdefault(
                423,
                {
                    "model": ErrorEnvelope,
                    "description": (
                        "A Bot with collaborators requires the caller to "
                        "hold its edit lock."
                    ),
                    **error_example(423, "Edit lock required"),
                },
            )
            kwargs["responses"] = responses
        super().__init__(path, endpoint, **kwargs)


def _rule_for(path: str, methods: Any) -> Authorization:
    """The row for this operation, or raise naming what is missing.

    Every method a route declares must resolve to the same rule; a route
    serving two methods with different bars would have to pick one, and
    silently picking is how a surface acquires a hole. ``HEAD`` and ``OPTIONS``
    are excluded for the reason the inventory excludes them: FastAPI adds them
    alongside ``GET`` and they are not separate decisions.
    """
    wanted = sorted(set(methods or ["GET"]) - {"HEAD", "OPTIONS"})
    rules = []
    for method in wanted:
        rule = AUTHORIZATION.get((method, path))
        if rule is None:
            raise PublicRouteNotAuthorized(
                f"{method} {path} has no row in AUTHORIZATION. Every public "
                f"operation must declare what governs it; add a row in "
                f"openapi_v1/authorization.py."
            )
        rules.append(rule)
    if len({repr(rule) for rule in rules}) > 1:
        raise PublicRouteNotAuthorized(
            f"{path} declares methods {wanted} with differing authorization "
            f"rules {rules}; split the route or give them the same rule."
        )
    return rules[0]


def assert_every_route_authorized(router: APIRouter) -> None:
    """Fail assembly on the mistakes :class:`PublicAPIRoute` cannot see itself.

    It catches a *missing row* at construction. Four things it cannot catch are
    checked here, at the end of assembly, so the application refuses to start
    rather than serving an operation nothing governs:

    1. a router built without ``route_class=PublicAPIRoute`` — its routes never
       ran that ``__init__``;
    2. a row matching no operation, left behind by a rename;
    3. a WebSocket operation with no row — it never runs the route class
       either, so nothing else would notice;
    4. a ``Check`` row the seam could not honour, in any of three shapes — a
       WebSocket operation, a route whose handler does not consume the owner the
       gate adjudicates, and a route carrying no ``{bot_id}`` on its path for
       the gate to read. Each would leave the table promising enforcement that
       never happens; see ``_assert_check_rows_are_enforceable``.
    """
    seen: set[tuple[str, str]] = set()
    sockets: set[tuple[str, str]] = set()
    checked_handlers: list[tuple[tuple[str, str], object]] = []
    unguarded: list[str] = []
    for route in _walk(router):
        original = getattr(route, "original_route", None) or route
        path = getattr(route, "path", "") or getattr(original, "path", "")
        methods = set(getattr(route, "methods", None) or {"WEBSOCKET"})
        is_socket = _is_websocket(original)
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            seen.add((method, path))
            if is_socket:
                sockets.add((method, path))
            elif isinstance(AUTHORIZATION.get((method, path)), Check):
                checked_handlers.append(((method, path), original.endpoint))
        if not isinstance(original, PublicAPIRoute) and not is_socket:
            unguarded.append(f"{sorted(methods)} {path}")
    if unguarded:
        raise PublicRouteNotAuthorized(
            "these routes were not built with route_class=PublicAPIRoute, so "
            "their AUTHORIZATION row was never read: " + ", ".join(sorted(unguarded))
        )
    # The reverse direction, which matters only for the socket plane. An HTTP
    # route with no row cannot exist — ``PublicAPIRoute`` refused to build it —
    # so this is guaranteed empty there. A WebSocket route never runs that
    # ``__init__`` at all, so without this check one could be served with no
    # declared authorization whatsoever, which is the single gap the route
    # class cannot close on its own.
    missing = seen - set(AUTHORIZATION) - UNMOUNTED_OPERATIONS
    if missing:
        raise PublicRouteNotAuthorized(
            "these live operations have no row in AUTHORIZATION: "
            + ", ".join(sorted(f"{method} {path}" for method, path in missing))
        )
    orphans = set(AUTHORIZATION) - seen - UNMOUNTED_OPERATIONS
    if orphans:
        raise PublicRouteNotAuthorized(
            "these AUTHORIZATION rows match no live operation (renamed or "
            f"removed?): {sorted(orphans)}"
        )
    _assert_check_rows_are_enforceable(sockets, checked_handlers)


def _assert_check_rows_are_enforceable(
    sockets: set[tuple[str, str]],
    checked_handlers: list[tuple[tuple[str, str], object]],
) -> None:
    """Refuse a ``Check`` row the seam could not actually enforce.
    A row that declares enforcement the mechanism cannot deliver is worse than
    no row: the table reads as covered, and the inventory agrees, while the
    operation is served unguarded. Three shapes of that.

    The third is the seam's permanent limit rather than a gap to close. The gate
    runs *before* the handler, so the only bot it can adjudicate is one the
    request itself carries on the path — that is what ``BotIdPath`` reads. An
    operation whose bot arrives any other way cannot be keyed on the same value
    the handler acts on, and a ``Check`` row for it would adjudicate something
    the handler never saw.

    The **retiring skills addresses** are the live example: two carry the bot in
    the query string, and four name no bot at all — the skill id resolves its
    own bot, inside the handler, after this check would have had to answer. They
    keep the checks they already have; what this refuses is the table claiming
    the seam covers them.

    **This does not catch harness**, and it is worth saying so where someone
    would otherwise assume it. Those six routes are mounted under
    ``/openapi/v1/bots/{bot_id}/harness`` and do declare ``bot_id`` on the path,
    so they pass this refusal. What stops them today is the *second* one — no
    harness handler consumes ``OwnerIdDep`` — and what should stop them after
    that is judgement: they pass ``entity_id=body.entity_id`` to the service
    beside ``bot_id``, so the gate would adjudicate one thing while the
    operation acted on another. Adding ``OwnerIdDep`` there would satisfy all
    three refusals and still be wrong. That is a defect to fix (#1323 filed it),
    not a limit to encode.
    """
    socket_checks = sorted(
        f"{method} {path}"
        for (method, path) in sockets
        if isinstance(AUTHORIZATION.get((method, path)), Check)
    )
    if socket_checks:
        raise PublicRouteNotAuthorized(
            "these WebSocket operations declare Check, but FastAPI builds them "
            "as APIWebSocketRoute so the route class never attaches the gate — "
            "the declaration would be unenforced: " + ", ".join(socket_checks)
        )

    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
        resolve_owner_id,
    )

    divergent = sorted(
        f"{method} {path}"
        for (method, path), endpoint in checked_handlers
        if not _consumes(endpoint, resolve_owner_id)
    )
    if divergent:
        raise PublicRouteNotAuthorized(
            "these operations declare Check but their handler does not take "
            "OwnerIdDep, so the gate would adjudicate the addressed owner while "
            "the handler acted on a different one (see bot_access's contract): "
            + ", ".join(divergent)
        )

    # Read off the route's own path template rather than its resolved
    # parameters: ``BotIdPath`` is what the gate declares, and FastAPI fills a
    # path parameter only when the template names it. A route whose template
    # has no ``{bot_id}`` cannot supply one whatever its handler does.
    unkeyable = sorted(
        f"{method} {path}"
        for (method, path), _endpoint in checked_handlers
        if "{bot_id}" not in path
    )
    if unkeyable:
        raise PublicRouteNotAuthorized(
            "these operations declare Check but do not carry {bot_id} on their "
            "path, so the gate has no bot to resolve and the row cannot be "
            "enforced as written. Refused here rather than left to fail per "
            "request: a table that claims enforcement the seam cannot deliver "
            "is the thing this module exists to prevent. The gate runs before "
            "the handler, so this is a permanent limit rather than a gap — an "
            "operation addressing its bot any other way keeps whatever check it "
            "already has and must not claim Check. Offending rows: "
            + ", ".join(unkeyable)
        )


def _consumes(endpoint: object, dependency: object) -> bool:
    """Whether ``endpoint``'s own signature declares ``Depends(dependency)``.
    Its *own* signature, deliberately: the gate itself takes ``OwnerIdDep``, so
    walking the route's whole dependency tree would find it every time and the
    check would pass vacuously. ``get_type_hints`` follows ``__wrapped__``, so a
    handler behind ``@envelope_errors`` reports its real parameters.
    """
    # ``get_type_hints`` rather than ``signature().parameters[...].annotation``:
    # every router in this package declares ``from __future__ import
    # annotations``, so the raw annotations are *strings* and no amount of
    # ``get_origin`` on them finds anything. Reading them unresolved would make
    # this check answer "no" for every real handler — a false refusal on the
    # first migration, which is exactly when it must be trustworthy.
    # ``include_extras`` keeps the ``Annotated`` metadata the dependency lives in.
    try:
        hints = get_type_hints(endpoint, include_extras=True)
    except Exception:  # pragma: no cover - unresolvable forward reference
        return False
    for annotation in hints.values():
        if get_origin(annotation) is not Annotated:
            continue
        for meta in get_args(annotation)[1:]:
            if getattr(meta, "dependency", None) is dependency:
                return True
    return False


def _walk(router: APIRouter):
    """Every operation as the application will really serve it.
    ``include_router`` stores a lazy wrapper rather than copying routes, so the
    effective contexts — not ``router.routes`` — are what the surface serves.
    """
    for route in getattr(router, "routes", []):
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        elif hasattr(route, "dependant"):
            yield route


def _is_websocket(route: Any) -> bool:
    """WebSocket routes are ``APIWebSocketRoute``, which takes no route class.
    FastAPI offers no per-router class for the socket plane, so a socket route
    cannot carry :class:`PublicAPIRoute` and ``_rule_for`` never runs for it.
    It is covered by the *missing* check above rather than by this exemption —
    which is the whole reason that check exists, since the orphan check looks
    the other way and would let a row-less socket route through.
    """
    return not hasattr(route, "methods")
