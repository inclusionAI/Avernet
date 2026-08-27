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
3. The operations that check their grant inside a handler are named, and named
   only there. Seven used to; bot-first addressing removed the reason for three,
   and the remaining four are the skill-addressed ones, whose owner arrives on
   the record rather than on the wire.

These are checked against the router the application really builds, so a change
that satisfies the table but not the wiring still fails.
"""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.deprecated import (
    LEGACY_ROUTES,
    SELF_CHECKED_ROUTES,
)
from agentclaw.community.adapters.http.openapi_v1.admission import (
    ADMISSION,
    ADMITTING_MODES,
    HARNESS_SCOPED_OPERATIONS,
    SKILL_SCOPED_OPERATIONS,
    AdmissionMode,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.principal import (
    refuse_app_only_caller,
    require_granted_addressed_bot,
    require_granted_own_bot,
)

#: The modes whose contract is "a grant is checked for the addressed bot",
#: each mapped to the one dependency that spells it. The declaration *is* the
#: mode — the own-bot dependency never reads an owner off the wire, the
#: addressed-bot one is the only thing entitled to — so a route carrying the
#: wrong one is not a naming slip, it is the wrong check.
_GRANT_DEPENDENCY_BY_MODE = {
    AdmissionMode.GRANT_CHECKED_OWN_BOT: require_granted_own_bot,
    AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT: require_granted_addressed_bot,
}

_GRANT_CHECKED_MODES = frozenset(_GRANT_DEPENDENCY_BY_MODE)


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


@pytest.mark.parametrize(
    "mode", sorted(_GRANT_DEPENDENCY_BY_MODE, key=lambda m: m.name)
)
def test_every_grant_checked_operation_declares_its_modes_dependency(mode):
    """A grant-checked mode must mean *its* check runs, not just *a* check.

    Verified against the assembled router rather than the handler signature,
    because the check is declared three different ways — at ``include_router``
    for the wholly own-bot groups, per route in the mixed ``bots`` and
    ``skills`` groups, and (alongside the mount) transitively through
    ``OwnerIdDep`` on the engine-runtime groups. A test that looked for one
    spelling would pass while the others rotted.

    Since the split there is one dependency per mode, and this asserts the
    pairing in both directions: an own-bot operation carrying the addressed-bot
    dependency would let an appended ``owner_id`` aim the check at a bot the
    handler is not acting on — the surface's oldest defect — and an
    addressed-bot operation carrying the own-bot dependency would refuse every
    valid grant on a shared bot. Neither is a smaller mistake than a missing
    check.

    Three named sets are excluded, for the same underlying reason and with
    different lifetimes. ``SKILL_SCOPED_OPERATIONS`` — the four current
    ``{skill_id}`` operations — resolve the bot's owner from the skill record,
    so there is nothing for a dependency to look a grant up against until the
    handler has read it. ``HARNESS_SCOPED_OPERATIONS`` keep the existing
    harness wire contract (bot id on the path, user id in the query) by
    resolving the bot owner from the repository record and checking the grant
    inside ``require_harness_bot_access``. The retiring addresses in
    ``SELF_CHECKED_ROUTES`` are the same problem in the old contract's shape:
    their bot is in a request body or behind a skill id, and mounting them
    under a dependency would refuse an application outright rather than defer,
    turning a working legacy call into a 404. All check it themselves, first,
    before acting; the third set is empty the day the deprecated package goes.
    ``test_only_the_named_operations_check_their_own_grant`` is what stops any
    of them from growing quietly.
    """
    dependency = _GRANT_DEPENDENCY_BY_MODE[mode]
    expected = {
        key
        for key, table_mode in ADMISSION.items()
        if table_mode is mode
        and key not in SELF_CHECKED_ROUTES
        and key not in SKILL_SCOPED_OPERATIONS
        and key not in HARNESS_SCOPED_OPERATIONS
    }
    actual = {
        key
        for key, ctx in _operations()
        if _depends_on(_dependant_of(ctx), dependency)
    }

    unchecked = sorted(f"{m} {p}" for m, p in expected - actual)
    unexpected = sorted(f"{m} {p}" for m, p in actual - expected)

    assert not unchecked, (
        f"operations whose mode is {mode.name} that do not declare "
        f"{dependency.__name__} — an application would reach these with the "
        "wrong authorization, or none at all:\n  " + "\n  ".join(unchecked)
    )
    assert not unexpected, (
        f"operations declaring {dependency.__name__} whose mode is not "
        f"{mode.name}. The declaration and the table must say the same thing — "
        "fix whichever one is wrong:\n  " + "\n  ".join(unexpected)
    )


def test_only_the_named_operations_check_their_own_grant():
    """``TODO(#960)`` shrank from seven to four, and this is what holds the line.

    Seven operations used to have their bot somewhere the shared dependency
    could not see it — one in a request body, four behind a skill id, two under
    an owner parameter the dependency did not know. Two mechanisms doing one
    job, and it had already cost one real defect.

    Bot-first addressing removed the reason for three: routines' create takes
    its bot on the path, and the two skills collection operations name their
    owner in the query, where ``require_granted_addressed_bot`` reads it.

    The four ``{skill_id}`` operations are not a leftover. They resolve by
    ``(skill, actor)``, so the bot's owner is an *output* of the read — a
    collaborator routinely reaches a skill on someone else's bot — and there is
    nothing for the dependency to look a grant up against until the record is in
    hand. Mounting them under it refused a valid grant on a shared bot with a
    404, which is the failure ``test_skills_shared_bot_grant.py`` now pins.

    What is asserted here is that the set is exactly those four: still in a
    grant-checked mode, still absent from the shared dependency, and not grown
    by one more operation that merely found the check inconvenient.
    """
    self_checking = {
        key
        for key, ctx in _operations()
        if ADMISSION.get(key) in _GRANT_CHECKED_MODES
        and not _depends_on(_dependant_of(ctx), require_granted_own_bot)
        and not _depends_on(_dependant_of(ctx), require_granted_addressed_bot)
        and key not in LEGACY_ROUTES
    }
    named = SKILL_SCOPED_OPERATIONS | HARNESS_SCOPED_OPERATIONS
    assert self_checking == named, (
        "the set of operations checking their grant in a handler has changed. "
        "Adding one is an edit to admission.SKILL_SCOPED_OPERATIONS and "
        "needs the same justification the named ones have — that the addressed "
        "bot's owner cannot be known "
        "before the handler runs.\n"
        f"  unexpected: {sorted(self_checking - named)}\n"
        f"  no longer:  {sorted(named - self_checking)}"
    )


def test_the_self_checking_operations_are_still_grant_checked():
    """Self-checking is about *where* the check runs, never *whether*."""
    wrong_mode = sorted(
        f"{m} {p}"
        for m, p in SKILL_SCOPED_OPERATIONS | HARNESS_SCOPED_OPERATIONS
        if ADMISSION.get((m, p)) not in _GRANT_CHECKED_MODES
    )
    assert not wrong_mode, wrong_mode

    not_in_table = sorted(
        f"{m} {p}" for m, p in SELF_CHECKED_ROUTES if (m, p) not in ADMISSION
    )
    assert not not_in_table, (
        f"self-checking legacy operations absent from ADMISSION: {not_in_table}"
    )


def test_every_refused_operation_declares_its_refusal():
    """A ``REFUSED`` entry and a ``refuse_app_only_caller`` declaration are one
    decision written twice, and they must not drift.

    The refusal a machine caller actually receives comes centrally, from
    ``require_principal`` reading the table — that stays, and it is also the
    only thing covering an operation *absent* from the table, which has no
    route to declare anything on. What the declaration adds is that the
    decision is visible on the route that carries it and holds even if the
    table entry were mislabelled to an admitting mode.

    Both directions matter: a ``REFUSED`` operation without the declaration is
    a decision readable only in the table, and an operation declaring it under
    an admitting mode would refuse callers its mode says to admit. The legacy
    addresses are exempt in the first direction only — they inherit their
    replacements' modes and are retiring, so nothing new is declared on them —
    and today none of them is ``REFUSED`` anyway.
    """
    expected = {
        key
        for key, mode in ADMISSION.items()
        if mode is AdmissionMode.REFUSED and key not in LEGACY_ROUTES
    }
    actual = {
        key
        for key, ctx in _operations()
        if _depends_on(_dependant_of(ctx), refuse_app_only_caller)
    }

    undeclared = sorted(f"{m} {p}" for m, p in expected - actual)
    unexpected = sorted(f"{m} {p}" for m, p in actual - expected - set(LEGACY_ROUTES))

    assert not undeclared, (
        "REFUSED operations that do not declare refuse_app_only_caller — the "
        "central check still refuses them, but the decision is invisible at "
        "the route and unprotected against a mislabelled table entry:\n  "
        + "\n  ".join(undeclared)
    )
    assert not unexpected, (
        "operations declaring refuse_app_only_caller whose mode is not "
        "REFUSED — they would refuse callers their mode says to admit:\n  "
        + "\n  ".join(unexpected)
    )


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
