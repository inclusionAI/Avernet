"""Assembly-time checks that the surface and :data:`AUTHORIZATION` agree.

Split out of ``authorization.py`` so that module holds one concern — the table
and the route class that reads it — and this one holds the other: what
``build_public_router`` asserts once the whole surface is assembled, for the
mistakes :class:`~.authorization.PublicAPIRoute` cannot see itself.
"""

from __future__ import annotations

from typing import Annotated, Any, get_args, get_origin, get_type_hints

from fastapi import APIRouter

from agentclaw.community.adapters.http.openapi_v1.authorization import (
    AUTHORIZATION,
    Check,
    PublicAPIRoute,
    PublicRouteNotAuthorized,
    UNMOUNTED_OPERATIONS,
    _rule_for,
)


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
    sockets: set[tuple[str, str]], checked_handlers: list[tuple[tuple[str, str], object]]
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
