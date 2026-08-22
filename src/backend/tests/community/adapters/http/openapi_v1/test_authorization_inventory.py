"""The surface and the authorization table must describe the same thing.

The fail-closed default is *structural*: an operation absent from
``AUTHORIZATION`` cannot be constructed at all, because ``PublicAPIRoute``
refuses it. That default is only worth anything if it really holds end to end,
and this file is what holds it there.

The mechanism test comes first and is not ceremony. The whole design rests on
one claim about FastAPI — that a router's ``route_class`` survives
``include_router`` and that a dependency the class appends really runs and
really reaches the published schema. This version defers ``include_router``
into a lazy wrapper, so that claim was worth proving rather than assuming; if a
FastAPI upgrade ever breaks it, this is the test that says so, instead of the
surface silently serving unchecked operations.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.admission import ADMISSION
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import OwnerIdDep
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import envelope_errors
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.adapters.http.openapi_v1.authorization import (
    AUTHORIZATION,
    OWNER_SCOPED,
    SCAFFOLDING_MODES,
    Check,
    NoCheck,
    PublicAPIRoute,
    PublicRouteNotAuthorized,
    ServiceChecked,
    UNMOUNTED_OPERATIONS,
    assert_every_route_authorized,
    scaffolding_row_count,
)
from tests.community.adapters.http.openapi_v1._route_walk import (
    depends_on,
    effective_routes,
    operations,
    original_route_of,
    path_of,
)


_RAN: list[str | None] = []


async def _probe_dep(owner_id: Annotated[str | None, Query()] = None) -> None:
    _RAN.append(owner_id)


class _ProbeRoute(APIRoute):
    """Stand-in for ``PublicAPIRoute``: appends a dependency at construction."""

    def __init__(self, path, endpoint, **kw):
        kw["dependencies"] = [*(kw.get("dependencies") or []), Depends(_probe_dep)]
        super().__init__(path, endpoint, **kw)


@pytest.fixture
def probe_surface():
    """A child router with a custom route class, assembled like the real one."""
    child = APIRouter(prefix="/openapi/v1/probe", route_class=_ProbeRoute)

    @child.get("/thing")
    async def thing() -> dict:  # pragma: no cover - exercised through the client
        return {"ok": True}

    parent = APIRouter()
    parent.include_router(child)
    app = FastAPI()
    app.include_router(parent)
    return parent, app


def test_route_class_survives_include_router(probe_surface):
    """The class a router was built with is still the class after assembly.

    If this fails, ``PublicAPIRoute`` never reaches the assembled surface and
    every operation is served unchecked — so the fallback in the plan (a
    post-build pass over the routes) has to be adopted instead.
    """
    parent, _ = probe_surface
    originals = [original_route_of(ctx) for ctx in effective_routes(parent)]

    assert originals, "the probe router produced no effective routes"
    assert all(isinstance(route, _ProbeRoute) for route in originals)


def test_attached_dependency_is_in_the_effective_dependant(probe_surface):
    """Present in the tree FastAPI actually solves, not merely on the route.

    ``route.dependencies`` is the declaration; ``route.dependant`` is what runs.
    A dependency that reached only the first would look attached and never
    execute.
    """
    parent, _ = probe_surface
    contexts = effective_routes(parent)

    assert contexts
    for ctx in contexts:
        assert depends_on(ctx.dependant, _probe_dep)


def test_attached_dependency_runs_on_a_real_request(probe_surface):
    """The end-to-end claim: it executes, and it sees the request's own values."""
    _, app = probe_surface
    _RAN.clear()

    response = TestClient(app).get("/openapi/v1/probe/thing?owner_id=u-9")

    assert response.status_code == 200
    assert _RAN == ["u-9"]


def test_attached_dependency_publishes_its_parameters(probe_surface):
    """A parameter the seam declares reaches the document without a handler.

    This is what lets an operation gain ``owner_id`` when its row migrates,
    without editing the handler that serves it.
    """
    _, app = probe_surface
    operation = app.openapi()["paths"]["/openapi/v1/probe/thing"]["get"]

    published = {(p["name"], p["in"]) for p in operation.get("parameters", [])}

    assert ("owner_id", "query") in published


# ── the inventory ────────────────────────────────────────────────────────────


def _live_operations():
    return {key for key, _ in operations(build_public_router())}


def test_the_surface_and_the_table_agree_exactly():
    """No operation without a decision, and no decision without an operation.

    The first half is also enforced structurally — an operation with no row
    cannot be constructed — so this failing means something got past that,
    which is worth knowing loudly.

    ``UNMOUNTED_OPERATIONS`` is subtracted rather than ignored: those rows are
    real decisions about routers that exist but are not mounted, and the set
    naming them is what keeps "not on the surface yet" distinguishable from
    "left behind by a rename".
    """
    live, table = _live_operations(), set(AUTHORIZATION) - UNMOUNTED_OPERATIONS

    assert live - table == set(), "operations with no AUTHORIZATION row"
    assert table - live == set(), "AUTHORIZATION rows matching no operation"


def test_every_unmounted_row_is_really_unmounted():
    """The exemption must stay honest as routers get mounted.

    An entry that stops being unmounted turns into a permanent hole in the
    orphan check — the one place a stale row could hide — so mounting an
    operation has to delete its entry here.
    """
    live = _live_operations()

    assert UNMOUNTED_OPERATIONS & live == set(), (
        "these operations are mounted now; remove them from UNMOUNTED_OPERATIONS"
    )


def test_authorization_and_admission_cover_the_same_operations():
    """Two tables, two questions, one surface.

    ``ADMISSION`` decides whether a *machine* caller is admitted;
    ``AUTHORIZATION`` decides what a *person* must be to the bot. They are
    deliberately separate, but they describe the same set of operations, and a
    row added to one and forgotten in the other is the drift this catches.
    """
    assert set(AUTHORIZATION) - UNMOUNTED_OPERATIONS == set(ADMISSION)


def test_every_route_is_a_public_api_route():
    """Every operation was built through the class that reads the table.

    A router constructed without ``route_class=PublicAPIRoute`` would serve
    operations whose row was never read — the one way to be absent from the
    fail-closed default while still appearing in the table.
    """
    offenders = [
        f"{sorted(getattr(ctx, 'methods', None) or {'WEBSOCKET'})} {path_of(ctx)}"
        for ctx in effective_routes(build_public_router())
        if not isinstance(original_route_of(ctx), PublicAPIRoute)
        and hasattr(original_route_of(ctx), "methods")
    ]

    assert offenders == []


def test_an_unlisted_operation_cannot_be_constructed():
    """The fail-closed default, exercised rather than described.

    This is the property the whole design rests on: a new operation is refused
    until someone decides what governs it. It fails at *decoration*, so the
    module never finishes importing and the application never starts.
    """
    router = APIRouter(route_class=PublicAPIRoute)

    with pytest.raises(PublicRouteNotAuthorized) as refusal:

        @router.get("/openapi/v1/bots/{bot_id}/an-operation-nobody-decided-about")
        async def unlisted(bot_id: str) -> dict:  # pragma: no cover - never built
            return {}

    assert "an-operation-nobody-decided-about" in str(refusal.value)
    assert "AUTHORIZATION" in str(refusal.value)


def test_assembly_refuses_a_row_that_matches_no_operation():
    """A decision left behind by a rename stops the application too.

    Not pedantry: a stale row is how the table stops describing the surface,
    and a table that no longer describes the surface cannot be trusted to say
    an operation is covered.
    """
    router = build_public_router()
    AUTHORIZATION[("GET", "/openapi/v1/bots/{bot_id}/renamed-away")] = OWNER_SCOPED
    try:
        with pytest.raises(PublicRouteNotAuthorized) as refusal:
            assert_every_route_authorized(router)
        assert "renamed-away" in str(refusal.value)
    finally:
        AUTHORIZATION.pop(("GET", "/openapi/v1/bots/{bot_id}/renamed-away"), None)


def test_every_nocheck_row_carries_a_reason():
    """An empty reason turns a decision into an oversight that reads like one."""
    missing = [
        key for key, rule in AUTHORIZATION.items()
        if isinstance(rule, NoCheck) and not rule.reason.strip()
    ]

    assert missing == []


def test_every_service_checked_row_cites_a_real_enforcer():
    """The citation must resolve, and that module must really check something.

    This is the *most* this file can prove about a ``ServiceChecked`` row. It
    cannot prove the level is right — that is read by hand at migration, and
    the table's own docstring says so. Proving the module exists and performs a
    permission check at least stops a row citing something that was deleted or
    renamed out from under it.
    """
    unverifiable = []
    for key, rule in AUTHORIZATION.items():
        if not isinstance(rule, ServiceChecked):
            continue
        module_path = rule.where.replace(
            "…openapi_v1", "agentclaw.community.adapters.http.openapi_v1"
        ).replace("…core", "agentclaw.community.core")
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            unverifiable.append(f"{key}: cannot import {module_path}")
            continue
        source = inspect.getsource(module)
        if not any(
            marker in source
            for marker in (
                "permission",
                "PermissionLevel",
                "has_bot_access",
                "resolve_operable_bot",
                "can_manage_bot",
                "CollaboratorService",
            )
        ):
            unverifiable.append(f"{key}: {module_path} performs no visible check")

    assert unverifiable == []


def test_no_row_is_check_yet():
    """This change builds the seam; adopting it is per-group follow-up work.

    When the first group migrates this test is what says so, and it should be
    deleted then rather than weakened.
    """
    adopted = [key for key, rule in AUTHORIZATION.items() if isinstance(rule, Check)]

    assert adopted == [], "a group adopted the seam — see spec.md Decisions 4"


def test_scaffolding_burn_down_is_reported():
    """The migration's remaining distance is a number, and it only goes down.

    It also fails if a *new* row is added in a scaffolding mode, which is the
    quiet way the final shape becomes unreachable.
    """
    counted = sum(
        1 for rule in AUTHORIZATION.values() if isinstance(rule, SCAFFOLDING_MODES)
    )

    assert scaffolding_row_count() == counted
    assert counted == len(AUTHORIZATION) - sum(
        1 for rule in AUTHORIZATION.values() if isinstance(rule, NoCheck)
    )


# ── burn-down ────────────────────────────────────────────────────────────────


#: The modules a ``ServiceChecked`` row may still cite once this feature lands.
#:
#: Four operations are deferred, each for a reason recorded in ``spec.md``'s
#: *Out of Scope*, and nothing else may join them.
#:
#: **Two of these strings are forward references.** The table does not cite
#: ``local_skill_query_service`` or ``local_skill_upload_service`` yet — Task 9
#: writes them when it corrects the three skills rows that today cite
#: ``bot_skill_asset_service`` and are not checked there. Task 9 must use these
#: spellings exactly; a different one leaves this set unsatisfiable and is
#: caught when Task 15 removes the ``xfail``. Harness cannot be adjudicated
#: as it stands — its handlers act on a bot from the request body. The three
#: skills rows share their checks with six retiring addresses the seam cannot
#: reach, so migrating them would uncover those. Connection guards what may be
#: *composed* rather than how it is served, which is a trade-off this feature
#: does not settle.
_DEFERRED_CITATIONS = frozenset(
    {
        "…openapi_v1.harness.router",
        "…core.skill_center.services.local_skill_query_service",
        "…core.skill_center.services.local_skill_upload_service",
        "…core.engine_runtime.connection",
    }
)


@pytest.mark.xfail(
    reason=(
        "Adopting the seam is in progress — see "
        "specs/2026-08-22-openapi-v1-adopt-collaborator-seam. Flips to a real "
        "assertion in that feature's last group, once every migrating row has "
        "left ServiceChecked."
    ),
    strict=True,
)
def test_only_the_deferred_operations_remain_service_checked():
    """The burn-down, asserted rather than described.

    Replaces ``test_no_live_operation_carries_the_gate``, which encoded "the
    seam has no adopter" — true when #1323 shipped the mechanism and adopted it
    nowhere, and false from this feature's first migrating group onward. What
    matters now is not that the gate is unused but that everything still
    claiming a service check is one of the four deliberate exceptions.

    Deleted rather than weakened: a test asserting "no adopter" cannot be made
    to mean "the right adopters" by loosening it.
    """
    cited = {
        rule.where
        for rule in AUTHORIZATION.values()
        if isinstance(rule, ServiceChecked)
    }

    assert cited <= _DEFERRED_CITATIONS, (
        "these modules still hold a ServiceChecked row and are not among the "
        f"deferred four: {sorted(cited - _DEFERRED_CITATIONS)}"
    )


def test_service_level_edit_locks_are_untouched():
    """The seam carries no lock; that must not read as "the surface lost locks".

    Channels and service publications enforce one today and keep doing so
    (``spec.md`` *Decisions* 1). If these helpers are ever removed, the seam's
    "no lock" decision silently becomes "no lock anywhere".
    """
    channels = importlib.import_module(
        "agentclaw.community.adapters.http.openapi_v1.channels.router"
    )
    publications = importlib.import_module(
        "agentclaw.community.core.service_bot.services.service_publication_facade"
    )

    assert hasattr(channels, "_require_edit_lock")
    assert hasattr(publications.ServicePublicationFacade, "_require_draft_lock")


def test_a_router_cannot_opt_out_of_the_route_class():
    """Skipping ``route_class`` is the one way to be in the table but unchecked.

    ``PublicAPIRoute`` cannot catch this itself — a route that never runs its
    ``__init__`` never consults the table — so the assembly check is the only
    thing standing between a group that quietly builds its own routes and a
    surface that serves them unguarded. Distinct from
    ``test_every_route_is_a_public_api_route``, which asserts today's surface is
    clean: this asserts the mechanism would notice if it stopped being.
    """
    public = build_public_router()
    opted_out = APIRouter()  # no route_class

    @opted_out.get("/openapi/v1/bots/{bot_id}/built-its-own-way")
    async def sneaky(bot_id: str) -> dict:  # pragma: no cover - never served
        return {}

    public.include_router(opted_out)
    AUTHORIZATION[("GET", "/openapi/v1/bots/{bot_id}/built-its-own-way")] = OWNER_SCOPED
    try:
        with pytest.raises(PublicRouteNotAuthorized) as refusal:
            assert_every_route_authorized(public)
        assert "route_class=PublicAPIRoute" in str(refusal.value)
        assert "built-its-own-way" in str(refusal.value)
    finally:
        AUTHORIZATION.pop(("GET", "/openapi/v1/bots/{bot_id}/built-its-own-way"), None)


def test_a_websocket_route_without_a_row_fails_assembly():
    """The one operation kind the route class can never see.

    ``APIWebSocketRoute`` is built directly by FastAPI, so ``_rule_for`` never
    runs for it and the unguarded check exempts it. Without the reverse
    direction in ``assert_every_route_authorized`` such a route would be served
    with no declared authorization at all — the orphan check looks the other
    way and finds nothing to complain about.
    """
    public = build_public_router()
    sockets = APIRouter()

    @sockets.websocket("/openapi/v1/bots/undeclared/ws")
    async def undeclared(websocket) -> None:  # pragma: no cover - never served
        ...

    public.include_router(sockets)

    with pytest.raises(PublicRouteNotAuthorized) as refusal:
        assert_every_route_authorized(public)

    assert "no row in AUTHORIZATION" in str(refusal.value)
    assert "/openapi/v1/bots/undeclared/ws" in str(refusal.value)


def test_none_is_not_a_declarable_bar():
    """``Check(NONE)`` would be a gate that never refuses.

    ``bot_access._level`` returns ``NONE`` for every unresolvable case, and the
    gate compares ``level < rule.level`` — so with ``NONE`` as the bar that
    comparison is false for exactly those cases, and a one-word table typo
    would admit precisely the callers the gate exists to stop. Rejected at
    construction, so it raises while the table's module imports rather than
    waiting for a test to notice.
    """
    with pytest.raises(ValueError, match="not a bar"):
        Check(PermissionLevel.NONE)


def test_a_websocket_check_row_fails_assembly():
    """A declaration the seam cannot honour is worse than no declaration.

    FastAPI builds sockets as ``APIWebSocketRoute``, which never runs the route
    class, so ``require_check`` is never attached. The row would read as
    covered — and the inventory would agree — while the socket was served
    unguarded.
    """
    public = build_public_router()
    sockets = APIRouter()

    @sockets.websocket("/openapi/v1/bots/declared/ws")
    async def declared(websocket) -> None:  # pragma: no cover - never served
        ...

    public.include_router(sockets)
    AUTHORIZATION[("WEBSOCKET", "/openapi/v1/bots/declared/ws")] = Check(
        PermissionLevel.MEMBER
    )
    try:
        with pytest.raises(PublicRouteNotAuthorized) as refusal:
            assert_every_route_authorized(public)
        assert "unenforced" in str(refusal.value)
    finally:
        AUTHORIZATION.pop(("WEBSOCKET", "/openapi/v1/bots/declared/ws"), None)


def test_a_check_handler_must_consume_the_owner_the_gate_checks():
    """The seam's guarantee stops at the seam unless the handler joins it.

    ``bot_access`` reads ``OwnerIdDep``; a handler that takes ``UserIdDep`` as
    its owner — as all 34 of today's ``OWNER_SCOPED`` handlers do — reads a
    *different* dependency, and FastAPI's per-request cache does not unify two
    distinct callables. Flipping such a row to ``Check`` without also changing
    the handler would adjudicate one bot and act on another, which for a
    duplicated legacy id like ``default`` is two genuinely different bots.

    So the migration cannot be half-done: the row and the handler move
    together, or assembly refuses.
    """
    public = build_public_router()
    diverging = APIRouter(route_class=PublicAPIRoute)
    key = ("GET", "/openapi/v1/bots/{bot_id}/diverging")
    AUTHORIZATION[key] = Check(PermissionLevel.MEMBER)
    try:

        @diverging.get(key[1])
        async def handler(bot_id: BotIdPath, owner_id: UserIdDep) -> dict:
            return {}  # pragma: no cover - never served

        public.include_router(diverging)

        with pytest.raises(PublicRouteNotAuthorized) as refusal:
            assert_every_route_authorized(public)
        assert "does not take" in str(refusal.value)
    finally:
        AUTHORIZATION.pop(key, None)


def test_a_check_handler_taking_owner_id_dep_is_accepted():
    """The counterpart: the correct shape must not be rejected.

    Also exercises ``@envelope_errors``, since every real handler wears it and
    the check has to see through ``__wrapped__`` to the true signature.
    """
    public = build_public_router()
    correct = APIRouter(route_class=PublicAPIRoute)
    key = ("GET", "/openapi/v1/bots/{bot_id}/correct")
    AUTHORIZATION[key] = Check(PermissionLevel.MEMBER)
    try:

        @correct.get(key[1])
        @envelope_errors
        async def handler(
            bot_id: BotIdPath, caller: UserIdDep, owner_id: OwnerIdDep
        ) -> dict:
            return {}  # pragma: no cover - never served

        public.include_router(correct)

        assert_every_route_authorized(public)
    finally:
        AUTHORIZATION.pop(key, None)


def test_a_check_row_without_bot_id_on_the_path_fails_assembly():
    """The seam's permanent limit, enforced rather than documented.

    ``require_check`` builds a gate declaring ``BotIdPath``, and FastAPI fills a
    path parameter only when the route's template names it. A route whose
    template carries no ``{bot_id}`` therefore hands the gate nothing, and the
    row would adjudicate a bot the handler never saw.

    Two real sets of operations sit here and are excluded by this rather than by
    convention: harness, whose handlers act on a bot from the request *body*,
    and the retiring skills addresses, where the skill id resolves its own bot
    inside the handler — after this check would have had to answer. Both keep
    the checks they already have; what is refused is the table claiming the seam
    covers them.
    """
    public = build_public_router()
    pathless = APIRouter(route_class=PublicAPIRoute)
    key = ("GET", "/openapi/v1/bots/pathless-thing")
    AUTHORIZATION[key] = Check(PermissionLevel.MEMBER)
    try:

        @pathless.get(key[1])
        @envelope_errors
        async def handler(caller: UserIdDep, owner_id: OwnerIdDep) -> dict:
            return {}  # pragma: no cover - never served

        public.include_router(pathless)

        with pytest.raises(PublicRouteNotAuthorized) as refusal:
            assert_every_route_authorized(public)
        message = str(refusal.value)
        assert "{bot_id}" in message
        assert key[1] in message
    finally:
        AUTHORIZATION.pop(key, None)


def test_bot_id_anywhere_on_the_path_is_accepted():
    """Position on the path is not the requirement — presence is.

    This is not hypothetical tidiness: the fifteen retiring ``deprecated/``
    addresses this feature adjudicates carry ``{bot_id}`` at a *different*
    position than their replacements — ``/bots/sessions/{bot_id}`` against
    ``/bots/{bot_id}/sessions`` — because the bot moved within the path rather
    than out of it. A refusal keyed on position would reject every one of them.
    """
    public = build_public_router()
    relocated = APIRouter(route_class=PublicAPIRoute)
    key = ("GET", "/openapi/v1/bots/relocated/{bot_id}/thing")
    AUTHORIZATION[key] = Check(PermissionLevel.MEMBER)
    try:

        @relocated.get(key[1])
        @envelope_errors
        async def handler(
            bot_id: BotIdPath, caller: UserIdDep, owner_id: OwnerIdDep
        ) -> dict:
            return {}  # pragma: no cover - never served

        public.include_router(relocated)

        assert_every_route_authorized(public)
    finally:
        AUTHORIZATION.pop(key, None)
