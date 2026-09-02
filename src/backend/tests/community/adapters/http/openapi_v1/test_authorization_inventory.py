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
    EDIT_LOCK,
    INHERITED,
    OWNER_SCOPED,
    SCAFFOLDING_MODES,
    Check,
    NoCheck,
    PublicAPIRoute,
    PublicRouteNotAuthorized,
    ServiceChecked,
    UNMOUNTED_OPERATIONS,
    scaffolding_row_count,
)
from agentclaw.community.adapters.http.openapi_v1.authorization_inventory import (
    assert_every_route_authorized,
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


def test_scaffolding_burn_down_is_reported():
    """The migration's remaining distance is a number, and it only goes down.

    It also fails if a *new* row is added in a scaffolding mode, which is the
    quiet way the final shape becomes unreachable.
    """
    counted = sum(
        1 for rule in AUTHORIZATION.values() if isinstance(rule, SCAFFOLDING_MODES)
    )

    assert scaffolding_row_count() == counted
    # Every row is scaffolding, or one of the two settled modes. ``Check`` was
    # absent from this sum until the render-screens group became the seam's
    # first adopter; before that, "not NoCheck" and "scaffolding" were the same
    # set, and the identity held by accident of there being nothing else.
    settled = sum(
        1
        for rule in AUTHORIZATION.values()
        if isinstance(rule, (NoCheck, Check))
    )
    assert counted == len(AUTHORIZATION) - settled


# ── burn-down ────────────────────────────────────────────────────────────────


#: The exact operations that may still be ``ServiceChecked`` once this feature
#: lands — twenty-seven of them, deferred for seven reasons recorded in
#: ``spec.md``'s *Out of Scope*. It was ten when the plan was written; the
#: engine-runtime and bot-chat traces added the rest, each for a reason the
#: table could not have shown. That is what this set is for.
#:
#: Keyed on the **operation**, not on the module its row cites. Citing a module
#: is not a commitment: Task 9 rewrites three of these rows to name a different
#: module, and a set of module names could not tell that correction apart from a
#: row dodging migration by re-citing itself to something already deferred. The
#: operation cannot be renamed to escape.
_DEFERRED_OPERATIONS = frozenset(
    {
        # Not a structural limit: these routes do carry {bot_id} on the path.
        # Each also carries an entity_id the gate would not adjudicate — from
        # the body for apply/diagnose/preview/rollback, from the query string
        # for dim-history and dim-report — so a gate would adjudicate one thing
        # while the operation acted on another; and
        # require_harness_bot_access resolves ownership with a repository method
        # documented as performing no owner check, skipping the check entirely
        # for bot_id == "default". #1323 filed all three as a defect for that
        # group's owner. Deferred until fixed, not deferred forever.
        ("POST", "/openapi/v1/bots/{bot_id}/harness/apply"),
        ("POST", "/openapi/v1/bots/{bot_id}/harness/diagnose"),
        ("GET", "/openapi/v1/bots/{bot_id}/harness/dim-history"),
        ("GET", "/openapi/v1/bots/{bot_id}/harness/dim-report"),
        ("POST", "/openapi/v1/bots/{bot_id}/harness/preview"),
        ("POST", "/openapi/v1/bots/{bot_id}/harness/rollback"),
        # The read remains checked in skill_query_service, which also keeps the
        # retiring skills addresses checked.
        ("GET", "/openapi/v1/bots/{bot_id}/skills"),
        # Upload authorization remains owned by local_skill_upload_service.
        # This change only adds lock declarations to settled ``Check`` rows;
        # migrating ``ServiceChecked`` handlers is a separate authorization
        # change and stays out of scope here.
        ("POST", "/openapi/v1/bots/{bot_id}/skills"),
        ("POST", "/openapi/v1/bots/{bot_id}/skills/upload-folder"),
        # Its check guards what may be *composed* — a credential granting
        # operator.admin over every session on the device — rather than how the
        # route is served. Whether a route-level gate replaces that is not a
        # question this feature settles.
        ("GET", "/openapi/v1/bots/{bot_id}/connection"),
        # The ten session operations whose bar is not one level. Their handlers
        # run ``_resolve_session_backend``: owner-or-collaborator-at-MEMBER
        # first, and **on refusal**, at draft stage, a BCN-verified friendship
        # — a friend who is no collaborator reaches the bot's sessions through
        # ExpertChat. A route-level ``Check(MEMBER)`` refuses before the
        # handler runs, so it would close that path outright rather than
        # relocate it. The seam adjudicates one level; this is a disjunction,
        # and expressing it is not a question this feature settles.
        #
        # The six *file* operations under the same prefix are not here: they
        # call ``resolve_operable_bot`` directly, with no fallback, and did
        # migrate.
        ("GET", "/openapi/v1/bots/{bot_id}/sessions"),
        ("POST", "/openapi/v1/bots/{bot_id}/sessions"),
        ("GET", "/openapi/v1/bots/{bot_id}/sessions/favorites"),
        ("GET", "/openapi/v1/bots/{bot_id}/sessions/{session_id}"),
        ("PATCH", "/openapi/v1/bots/{bot_id}/sessions/{session_id}"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/sessions/{session_id}"),
        ("PUT", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite"),
        ("GET", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/messages"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/sessions/{session_id}/messages"),
        # The two product-chat reads. Their handlers ``del owner_id`` — the
        # addressed owner is consumed by the grant dependency and then
        # deliberately discarded, and the service is called with the *acting
        # user* as the scope. So a collaborator calling without naming an owner
        # is served today, and the seam would refuse them: it resolves the bot
        # through ``get_by_id_and_owner(bot_id, owner_id)``, and ``owner_id``
        # defaults to the caller. Adjudicating them means first making them
        # address an owner, which is a wire change for every existing caller.
        #
        # The underlying check differs in a second way worth recording: the
        # service asks ``has_bot_access``, which matches **any** collaborator
        # row on ``bot_id`` alone, while the seam resolves an *operable* level
        # under a named owner — stricter, and for a removed team editor it
        # answers differently.
        ("GET", "/openapi/v1/bots/{bot_id}/chats"),
        ("GET", "/openapi/v1/bots/{bot_id}/chats/{trace_id}"),
        # The three authorized-app operations. Blocked on a fork rather than a
        # trace: their admission mode is ``REFUSED`` — no application caller may
        # reach them at all — while ``OwnerIdDep``, which the gate requires,
        # transitively declares ``require_granted_addressed_bot``. Attaching the
        # seam therefore publishes a grant dependency on three operations that
        # refuse grant-holders by construction, and the admission inventory says
        # so. Resolving it means either a human-only owner resolver or
        # revisiting whether a ``REFUSED`` operation may publish ``owner_id`` —
        # a seam/admission decision, not this group's to make.
        ("GET", "/openapi/v1/bots/{bot_id}/authorized-apps"),
        ("POST", "/openapi/v1/bots/{bot_id}/authorized-apps"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/authorized-apps/{app_id}"),
    }
)


def test_no_deferred_operation_migrates_early():
    """The half of the burn-down that must hold *throughout*, not just at the end.

    Its sibling is ``xfail`` while the migration runs, which makes any failure
    there the expected one — including a deferred row being flipped by mistake.
    That row would then sit wrong for the whole feature and surface only when the
    marker came off. This half passes today and fails the moment one of the ten
    stops being ``ServiceChecked``, so a premature flip is loud immediately.
    """
    migrated_early = sorted(
        f"{method} {path}"
        for method, path in _DEFERRED_OPERATIONS
        if not isinstance(AUTHORIZATION.get((method, path)), ServiceChecked)
    )

    assert not migrated_early, (
        "these operations are deferred by spec.md's Out of Scope and must stay "
        "ServiceChecked until their blocker is addressed: "
        + ", ".join(migrated_early)
    )


def test_only_the_deferred_operations_remain_service_checked():
    """The burn-down, asserted rather than described.

    Replaces ``test_no_live_operation_carries_the_gate``, which encoded "the
    seam has no adopter" — true when #1323 shipped the mechanism and adopted it
    nowhere, and false from this feature's first migrating group onward. Deleted
    rather than weakened: a test asserting "no adopter" cannot be made to mean
    "the right adopters" by loosening it.

    Carried an ``xfail(strict=True)`` for the length of the migration, so that
    finishing it would be reported rather than assumed: the day the last
    migrating row left ``ServiceChecked``, the marker failed as an XPASS and had
    to be removed deliberately. This is that removal.

    Equality, not containment. A subset check would pass while rows that should
    have migrated sat waiting, which is the failure this is here to catch.
    """
    # Deliberately not subtracting UNMOUNTED_OPERATIONS, unlike the assertions
    # that compare the table against live routes. This one compares the table
    # against itself, where an unmounted row is still a row — and exempting one
    # would let it sit ServiceChecked forever without ever failing the burn-down.
    remaining = {
        key
        for key, rule in AUTHORIZATION.items()
        if isinstance(rule, ServiceChecked)
    }

    assert remaining == _DEFERRED_OPERATIONS, (
        "still ServiceChecked but not deferred: "
        f"{sorted(remaining - _DEFERRED_OPERATIONS)}; "
        "deferred but no longer ServiceChecked: "
        f"{sorted(_DEFERRED_OPERATIONS - remaining)}"
    )


def test_existing_service_level_edit_lock_defences_are_preserved():
    """The declarative seam supplements the established service defences."""
    channels = importlib.import_module(
        "agentclaw.community.adapters.http.openapi_v1.channels.router"
    )
    publications = importlib.import_module(
        "agentclaw.community.core.service_bot.services.service_publication_facade"
    )

    assert hasattr(channels, "_require_edit_lock")
    assert hasattr(publications.ServicePublicationFacade, "_require_draft_lock")
    assert (
        AUTHORIZATION[("POST", "/openapi/v1/bots/{bot_id}/channels")].edit_lock
        is EDIT_LOCK
    )
    assert (
        AUTHORIZATION[("POST", "/openapi/v1/bots/{bot_id}/lifecycle/advance")].edit_lock
        is EDIT_LOCK
    )


def test_caller_identity_keeps_legacy_read_and_write_permission_levels():
    read = AUTHORIZATION[("GET", "/openapi/v1/bots/{bot_id}/caller-context")]
    write = AUTHORIZATION[
        ("PATCH", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type")
    ]
    cli_write = AUTHORIZATION[
        ("PATCH", "/openapi/v1/bots/{bot_id}/clis/{cli_code}/call-type")
    ]

    assert read.level is PermissionLevel.MEMBER
    assert read.edit_lock is None
    assert write.level is PermissionLevel.OWNER
    assert write.edit_lock is EDIT_LOCK
    assert cli_write.level is PermissionLevel.OWNER
    assert cli_write.edit_lock is EDIT_LOCK


def test_edit_lock_operations_exactly_match_the_migrated_check_surface():
    expected = {
        ("POST", "/openapi/v1/bots/{bot_id}/channels"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"),
        ("PATCH", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"),
        ("PUT", "/openapi/v1/bots/{bot_id}/channels/{channel_id}/status"),
        ("POST", "/openapi/v1/bots/{bot_id}/config-manifest/apply"),
        ("POST", "/openapi/v1/bots/{bot_id}/diagnostics/health-check"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/lifecycle"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/advance"),
        ("PUT", "/openapi/v1/bots/{bot_id}/lifecycle/approval"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/cancel-staging"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/offline"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/restart"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/retry"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/upgrade"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/{publication_id}/upgrade"),
        ("PATCH", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type"),
        ("PATCH", "/openapi/v1/bots/{bot_id}/clis/{cli_code}/call-type"),
        ("POST", "/openapi/v1/bots/{bot_id}/skill-sets"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}"),
        ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/activate"),
        ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/deactivate"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}"),
        ("PUT", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}"),
        ("PUT", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}"),
        ("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/skills/{skill_id}"),
        ("PUT", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters"),
    }
    actual = {
        key
        for key, rule in AUTHORIZATION.items()
        if isinstance(rule, Check) and rule.edit_lock is EDIT_LOCK
    }

    assert actual == expected


def test_every_edit_lock_operation_declares_423_in_openapi():
    routes = {
        key: original_route_of(ctx)
        for key, ctx in operations(build_public_router())
    }
    edit_locked = {
        key
        for key, rule in AUTHORIZATION.items()
        if isinstance(rule, Check) and rule.edit_lock is EDIT_LOCK
    }

    missing = sorted(key for key in edit_locked if 423 not in routes[key].responses)

    assert missing == []


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

    The retiring skills addresses are the real set this excludes: two carry the
    bot in the query string, four name no bot at all — the skill id resolves its
    own bot inside the handler, after this check would have had to answer. They
    keep the checks they already have; what is refused is the table claiming the
    seam covers them.

    Not harness, despite the temptation to assume so: those routes are mounted
    under ``/openapi/v1/bots/{bot_id}/harness`` and pass this refusal. See
    ``_assert_check_rows_are_enforceable`` for what actually stops them.
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

    Not hypothetical tidiness, though the case is still ahead: the fifteen
    retiring ``deprecated/`` addresses this feature adjudicates carry
    ``{bot_id}`` at a *different* position than their replacements —
    ``/bots/sessions/{bot_id}`` against ``/bots/{bot_id}/sessions`` — because the
    bot moved within the path rather than out of it. They are ``INHERITED``
    today, so they do not reach this refusal yet; the moment Task 11 flips them
    to ``Check`` they do, and a refusal keyed on position would reject every one.
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


#: Retiring addresses that keep a check of their own, so their replacement may
#: migrate without them.
#:
#: Every entry here must *also* be one the seam could not adjudicate anyway —
#: asserted below, so this cannot become a place to park a twin that simply was
#: not migrated. These four reach ``_bot_behind``, which calls
#: ``skill_query_service.get_local_skill`` and therefore
#: ``_require_view_access`` — a MEMBER check on the bot the skill record names.
#: This feature does not touch that module; deferring the three current skills
#: rows that share it is precisely what keeps it in place.
_TWINS_CHECKED_INDEPENDENTLY = frozenset(
    {
        ("GET", "/openapi/v1/bots/skills/{skill_id}"),
        ("DELETE", "/openapi/v1/bots/skills/{skill_id}"),
        ("POST", "/openapi/v1/bots/skills/{skill_id}/activate"),
        ("POST", "/openapi/v1/bots/skills/{skill_id}/deactivate"),
    }
)


def _replacement_rule(method: str, legacy_path: str, replacement_path: str):
    """The rule governing the address that replaced this one, or raise.

    Usually the same method at a new path. One pair (auth-status) kept its path
    and changed method, so resolve by path while excluding the legacy row
    itself. Anything that resolves to neither is raised rather than skipped: a
    silent ``None`` here would quietly excuse the very twin this exists to
    catch.
    """
    candidates = {
        key: rule
        for key, rule in AUTHORIZATION.items()
        if key[1] == replacement_path and key != (method, legacy_path)
    }
    if (method, replacement_path) in candidates:
        return candidates[(method, replacement_path)]
    # Only where the address kept its path and changed method — auth-status, the
    # single such pair. Allowing it generally would bind a twin to an unrelated
    # operation's rule whenever a path happened to be down to one row.
    if legacy_path == replacement_path and len(candidates) == 1:
        return next(iter(candidates.values()))
    raise AssertionError(
        f"cannot tell which rule replaced {method} {legacy_path}: "
        f"{replacement_path} carries {sorted(candidates)}. Resolve it here "
        "rather than letting the twin check skip this address."
    )


def test_a_retiring_twin_migrates_with_its_replacement():
    """A twin left behind when its replacement gains ``Check`` is a hole.

    The retiring addresses under ``deprecated/`` re-register the *replacement's*
    endpoint under their own key. That is why they are checked at all today: the
    service-level permission call sits inside the shared handler, so both
    addresses reach it. The seam does not work that way — ``PublicAPIRoute``
    attaches the gate per route, and a twin whose row says ``INHERITED`` gets
    nothing.

    So when a group flips its rows to ``Check`` and deletes the service call the
    twin was relying on, that twin serves unguarded unless something else covers
    it. Nothing else in this file would notice: the burn-down counts
    ``ServiceChecked`` rows, and an abandoned twin is ``INHERITED``.

    Not hypothetical for this package. ``_collection_shim`` in
    ``deprecated/skills.py`` records that the legacy skills addresses shipped
    without their grant check for exactly one commit, for exactly this reason —
    ``legacy_route`` registers an endpoint, not a route, so route-level
    dependencies do not carry across. ``admission.py`` answered it by deriving
    each legacy address's mode from its replacement; ``authorization.py``
    deliberately does not derive, so this asserts what derivation would have
    guaranteed.

    Two remedies, and the second is not a loophole: a twin either carries
    ``Check`` itself, or keeps a check of its own and is recorded above. The
    second exists because the first is *impossible* for some twins — a legacy
    address with no ``{bot_id}`` on its path cannot carry ``Check`` at all, since
    ``_assert_check_rows_are_enforceable`` refuses exactly that. Without it the
    two guards would deadlock the moment Task 9 migrates the skills
    ``{skill_id}`` operations: this test would demand ``Check`` on their twins
    and assembly would refuse it.
    """
    from agentclaw.community.adapters.http.openapi_v1.deprecated import LEGACY_ROUTES

    abandoned = []
    mismatched = []
    for (method, legacy_path), replacement_path in LEGACY_ROUTES.items():
        replacement = _replacement_rule(method, legacy_path, replacement_path)
        if not isinstance(replacement, Check):
            continue
        if (method, legacy_path) in _TWINS_CHECKED_INDEPENDENTLY:
            continue
        twin = AUTHORIZATION.get((method, legacy_path))
        if not isinstance(twin, Check):
            abandoned.append(f"{method} {legacy_path} -> {replacement_path}")
        elif (
            twin.level is not replacement.level
            or twin.edit_lock is not replacement.edit_lock
        ):
            # Matching bars, not merely both being Check. Tasks 12 and 13 bring
            # ADMIN and OWNER rows, and a MEMBER twin of an ADMIN replacement is
            # a way around the bar rather than a copy of it.
            mismatched.append(
                f"{method} {legacy_path} at {twin!r} "
                f"but {replacement_path} at {replacement!r}"
            )

    assert not abandoned, (
        "these retiring addresses share a handler with a replacement that now "
        "carries Check, but did not migrate with it — the gate attaches per "
        "route, so they are served with whatever the deleted service call used "
        "to provide, which is nothing: " + ", ".join(sorted(abandoned))
    )
    assert not mismatched, (
        "these retiring addresses migrated with their replacement but at a "
        "different bar or lock requirement, so the old address is a way around "
        "the new one: " + ", ".join(sorted(mismatched))
    )


def test_independently_checked_twins_could_not_have_migrated_anyway():
    """The exemption above must be forced, never chosen.

    A twin that *could* carry ``Check`` and simply was not migrated is the exact
    hole the sibling test exists to catch, so exempting one would defeat it. Each
    entry must therefore be an address the seam cannot adjudicate at all: no
    ``{bot_id}`` on its path, which ``_assert_check_rows_are_enforceable``
    refuses outright.
    """
    avoidable = sorted(
        f"{method} {path}"
        for method, path in _TWINS_CHECKED_INDEPENDENTLY
        if "{bot_id}" in path
    )

    assert not avoidable, (
        "these twins carry {bot_id} on their path, so they could migrate with "
        "their replacement and must not be exempted from doing so: "
        + ", ".join(avoidable)
    )


def test_the_check_the_exempted_twins_rely_on_still_exists():
    """The exemption is only true while that check is there.

    Being unadjudicable is what makes an exemption *permissible*; it is not what
    makes it *safe*. The four exempted twins are covered because ``_bot_behind``
    calls ``get_local_skill``, which calls ``_require_view_access``, which
    refuses a caller below MEMBER on the bot the skill record names. Delete that
    and the exemption silently becomes a hole — the sibling test would keep
    passing, since it only asks whether the twin could have migrated.

    Pinned the way ``test_service_level_edit_locks_are_untouched`` pins the
    locks: name the function, and fail if it stops doing the thing.
    """
    query_service = importlib.import_module(
        "agentclaw.community.core.skill_center.services.skill_query_service"
    )
    legacy_skills = importlib.import_module(
        "agentclaw.community.adapters.http.openapi_v1.deprecated.skills"
    )

    assert hasattr(legacy_skills, "_bot_behind"), (
        "the exempted twins resolve their bot through _bot_behind; without it "
        "they no longer reach the check they are exempted on account of"
    )
    get_source = inspect.getsource(query_service.SkillQueryService.get_local_skill)
    assert "_require_view_access" in get_source, (
        "get_local_skill no longer calls _require_view_access, so _bot_behind "
        "reaches no collaborator check and the four exempted legacy skills "
        "twins are unguarded — the ends of the chain being intact is not enough"
    )

    source = inspect.getsource(query_service.SkillQueryService._require_view_access)
    assert "check_collaborator_permission" in source, (
        "_require_view_access no longer performs a collaborator check, so the "
        "four legacy skills twins exempted in _TWINS_CHECKED_INDEPENDENTLY are "
        "now unguarded — either restore it or migrate them"
    )
    assert "PermissionLevel.MEMBER" in source, (
        "_require_view_access no longer checks at MEMBER; the exemption records "
        "that bar, so re-derive it before changing this"
    )


def test_the_asset_service_check_the_unseamed_surfaces_rely_on_still_exists():
    """The seven ``{skill_id}`` rows moved; their service check deliberately did not.

    Task 8 deleted ``can_manage_bot`` outright, because the hook had exactly one
    caller family and the seam replaced all of it. ``skill_query_service``
    is not that: its two permission sites are reached from three surfaces and
    the seam covers only one of them.

    * ``/openapi/v1/bots/{bot_id}/skills/{skill_id}/…`` — now ``Check(MEMBER)``,
      adjudicated by the seam before the handler runs.
    * The retiring twins in ``deprecated/skills.py``. Their router *is* built
      with ``route_class=PublicAPIRoute``, so a twin does read its own row —
      but its address is ``/openapi/v1/bots/skills/{skill_id}``, with no
      ``{bot_id}`` on the path, so ``Check`` is unassignable there and the
      ``unkeyable`` refusal says so at assembly time. They are the four already
      exempted in ``_TWINS_CHECKED_INDEPENDENTLY``.
    * ``adapters/http/skill_center/skills.py``, mounted at **``/api/skills``** —
      outside ``/openapi/v1`` entirely, governed by no row in this table, and it
      calls ``get_skill`` and ``set_active`` straight through.

    Deleting the check to "finish" the migration would strip authorization from
    the last two. So it stays, and this pins it: the row says the seam is the
    authority for the seven, and this says the other two surfaces still have one
    at the same bar.
    """
    asset_service = importlib.import_module(
        "agentclaw.community.core.skill_center.services.skill_query_service"
    )

    guarded = {
        "_resolve_local": inspect.getsource(
            asset_service.SkillQueryService._resolve_local
        ),
        "_RepoAssetAdapter.resolve": inspect.getsource(
            asset_service._RepoAssetAdapter.resolve
        ),
    }
    for name, source in guarded.items():
        assert "check_collaborator_permission" in source, (
            f"{name} no longer performs a collaborator check. The seven "
            "{skill_id} rows are fine — the seam adjudicates them — but the "
            "retiring twins cannot carry a Check at all and /api/skills is not "
            "in this table, so both now reach this code unguarded"
        )
        assert "PermissionLevel.MEMBER" in source, (
            f"{name} no longer checks at MEMBER, which is the bar the seven "
            "migrated rows were derived from; re-derive before changing it"
        )

    legacy_skills = importlib.import_module(
        "agentclaw.community.adapters.http.skill_center.skills"
    )
    assert legacy_skills.router.prefix == "/api/skills", (
        "the legacy skills router moved; if it now lives under /openapi/v1 it "
        "is governed by AUTHORIZATION and this reasoning has to be redone"
    )


def test_the_twin_guard_catches_an_abandoned_twin():
    """Prove the guard can still fire, on a pair that has already migrated.

    A guard is only as good as its ability to fail, and the way this one stops
    being able to is by having nothing left to run over. So both halves are
    staged here rather than assumed: the twin is put *back* to ``INHERITED``
    before the abandoned case is driven, because the real table now has it at
    ``Check`` and reading the guard as firing when it had nothing to catch is
    precisely the false pass this test exists to rule out.

    Then the same pair at a different bar, and finally the state the table
    really holds — which must pass.
    """
    replacement = ("GET", "/openapi/v1/bots/{bot_id}/sessions")
    twin = ("GET", "/openapi/v1/bots/sessions/{bot_id}")
    saved = (AUTHORIZATION[replacement], AUTHORIZATION[twin])
    try:
        AUTHORIZATION[replacement] = Check(PermissionLevel.MEMBER)
        AUTHORIZATION[twin] = INHERITED

        with pytest.raises(AssertionError) as abandoned:
            test_a_retiring_twin_migrates_with_its_replacement()
        assert twin[1] in str(abandoned.value)

        AUTHORIZATION[twin] = Check(PermissionLevel.ADMIN)
        with pytest.raises(AssertionError) as mismatched:
            test_a_retiring_twin_migrates_with_its_replacement()
        assert "different bar" in str(mismatched.value)

        AUTHORIZATION[twin] = Check(PermissionLevel.MEMBER)
        test_a_retiring_twin_migrates_with_its_replacement()
    finally:
        AUTHORIZATION[replacement], AUTHORIZATION[twin] = saved
