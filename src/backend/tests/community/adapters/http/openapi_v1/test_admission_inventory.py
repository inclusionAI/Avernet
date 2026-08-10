"""The surface and the admission table must describe the same thing.

The feature's fail-closed default is *structural*: an operation absent from
``ADMISSION`` refuses a caller with no end user, so a route added tomorrow is
refused by omission rather than by someone remembering not to opt in. That
default is only worth anything if the omission is **loud**, and this file is
what makes it loud.

Three properties, and each fails for a different mistake:

1. Every route on the surface is in the table, and every table entry is a route.
   Catches a route added without a decision, and a decision left behind after a
   route was renamed or removed.
2. Every operation whose mode says "grant-checked" actually performs the check —
   verified against the built router's *effective* dependencies, not against
   what a handler declares.
3. The five operations that check inside their handlers are named, and named
   only there. Catches the exception list growing by accident.

These are checked against the router the application really builds, so a change
that satisfies the table but not the wiring still fails.
"""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.admission import (
    ADMISSION,
    ADMITTING_MODES,
    BODY_BOT_ID_OPERATIONS,
    SKILL_SCOPED_OPERATIONS,
    AdmissionMode,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.principal import require_granted_bot

#: The modes whose contract is "a grant is checked for the addressed bot".
_GRANT_CHECKED_MODES = frozenset({AdmissionMode.A1, AdmissionMode.A2})


def _effective_routes():
    """Every operation as the application will really serve it.

    ``include_router`` stores a lazy wrapper rather than copying routes, and the
    *original* router does not carry the dependencies added at include time. So
    walking ``original_router`` — which the older helpers in this package do —
    would miss exactly the group-level declarations this feature relies on, and
    the test would pass while the wiring was absent. Reading the effective
    contexts is what makes this check about the assembled surface.
    """
    router = build_public_router()
    found = []
    for route in getattr(router, "routes", []):
        if hasattr(route, "effective_route_contexts"):
            found.extend(route.effective_route_contexts())
        elif hasattr(route, "dependant"):
            found.append(route)
    return found


def _path_of(ctx) -> str:
    """The route's path, for HTTP and WebSocket alike.

    A socket route's effective context carries an empty ``path`` — the merged
    view is built for HTTP — so its path has to come off the original route.
    Falling back rather than skipping is deliberate: the socket plane is part of
    this surface, and an operation this walker could not name would be an
    operation the inventory silently stopped covering.
    """
    return ctx.path or getattr(getattr(ctx, "original_route", None), "path", "")


def _operations():
    """``(method, path)`` for every effective operation, WebSockets included."""
    seen = []
    for ctx in _effective_routes():
        methods = set(getattr(ctx, "methods", None) or {"WEBSOCKET"})
        path = _path_of(ctx)
        assert path, f"could not determine a path for {ctx!r}"
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            seen.append(((method, path), ctx))
    return seen


def _dependant_of(ctx):
    """The dependency tree to inspect, for HTTP and WebSocket alike.

    A socket route has no merged context — FastAPI builds those for the HTTP
    plane — so its tree comes off the original route. Falling back rather than
    skipping keeps the socket plane inside the inventory: a route this walker
    could not read would be a route the checks below silently stopped covering,
    which is the failure mode this whole file exists to prevent.
    """
    return ctx.dependant or getattr(
        getattr(ctx, "original_route", None), "dependant", None
    )


def _depends_on(dependant, target) -> bool:
    if dependant is None:
        return False
    if dependant.call is target:
        return True
    return any(_depends_on(sub, target) for sub in dependant.dependencies)


def test_the_surface_and_the_table_agree_exactly():
    """No route without a decision, and no decision without a route."""
    live = {key for key, _ in _operations()}
    table = set(ADMISSION)

    undecided = sorted(f"{m} {p}" for m, p in live - table)
    stale = sorted(f"{m} {p}" for m, p in table - live)

    assert not undecided, (
        "public operations missing from ADMISSION — they refuse an app-only "
        "caller by default, which may be right, but it has to be *decided*. Add "
        "each to openapi_v1/admission.py with the mode its shape calls for:\n  "
        + "\n  ".join(undecided)
    )
    assert not stale, (
        "ADMISSION names operations that no longer exist. Remove them, or the "
        "table stops being a description of the surface:\n  " + "\n  ".join(stale)
    )


def test_every_public_operation_still_requires_a_principal():
    """The floor, unchanged: nothing is reachable unauthenticated.

    ``require_principal`` is where the end-user guard moved to, so a route that
    escaped it would not merely skip authentication — it would skip the
    admission decision entirely and be reachable by any verified credential.
    """
    missing = [
        f"{method} {path}"
        for (method, path), ctx in _operations()
        if not _depends_on(_dependant_of(ctx), require_principal)
    ]

    assert not missing, f"public operations not gated by require_principal: {missing}"


def test_every_grant_checked_operation_actually_checks():
    """Mode A1/A2 must mean the check runs, not that someone intended it to.

    Verified against the assembled router rather than the handler signature,
    because the check is declared three different ways — at ``include_router``
    for the wholly-A1 groups, per route in the mixed ``bots`` group, and
    transitively through ``OwnerIdDep`` on the engine-runtime groups. A test
    that looked for one spelling would pass while the other two rotted.
    """
    expected = {key for key, mode in ADMISSION.items() if mode in _GRANT_CHECKED_MODES}
    actual = {
        key
        for key, ctx in _operations()
        if _depends_on(_dependant_of(ctx), require_granted_bot)
    }

    unchecked = sorted(f"{m} {p}" for m, p in expected - actual)
    unexpected = sorted(f"{m} {p}" for m, p in actual - expected)

    assert not unchecked, (
        "operations marked grant-checked that do not run the check — an "
        "application would reach these with no authorization at all:\n  "
        + "\n  ".join(unchecked)
    )
    assert not unexpected, (
        "operations running the grant check without a mode that calls for it. "
        "On an operation that names no bot the check refuses an application "
        "outright, so this is a refusal nobody asked for:\n  "
        + "\n  ".join(unexpected)
    )


def test_the_deferring_operations_are_grant_checked_and_named_once():
    """The five exceptions are exceptions to *where*, never to *whether*."""
    deferring = BODY_BOT_ID_OPERATIONS | SKILL_SCOPED_OPERATIONS

    assert len(deferring) == 5, (
        "the deferring set changed size. Every entry is an operation the shared "
        "dependency waves through, so each one added is a promise that a "
        "handler checks instead — make that deliberate."
    )

    not_in_table = sorted(f"{m} {p}" for m, p in deferring - set(ADMISSION))
    assert not not_in_table, f"deferring operations absent from ADMISSION: {not_in_table}"

    wrong_mode = sorted(
        f"{m} {p}" for m, p in deferring if ADMISSION[(m, p)] not in _GRANT_CHECKED_MODES
    )
    assert not wrong_mode, (
        "deferring operations must still be grant-checked modes — deferring is "
        f"about where the check runs, not whether: {wrong_mode}"
    )

    overlap = BODY_BOT_ID_OPERATIONS & SKILL_SCOPED_OPERATIONS
    assert not overlap, f"an operation cannot defer for two reasons: {sorted(overlap)}"


@pytest.mark.parametrize("mode", sorted(AdmissionMode, key=lambda m: m.name))
def test_every_mode_is_used(mode: AdmissionMode):
    """A mode nothing uses is a rule nobody is following.

    Cheap, and it catches the specific rot where a mode is renamed or its
    entries are re-labelled, leaving the definition and its docstring behind as
    documentation of a policy that is no longer applied anywhere.
    """
    assert any(value is mode for value in ADMISSION.values()), (
        f"AdmissionMode.{mode.name} is defined but assigned to no operation"
    )


def test_admitting_modes_is_everything_but_refused():
    """The one place "which modes admit" is written down must stay total.

    A mode added later and left out of ``ADMITTING_MODES`` would silently refuse
    every operation carrying it — a fail-closed direction, but a confusing one
    to debug, and not what the author would have meant.
    """
    assert ADMITTING_MODES == frozenset(AdmissionMode) - {AdmissionMode.REFUSED}
