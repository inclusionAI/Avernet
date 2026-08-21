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
from agentclaw.community.adapters.http.openapi_v1.authorization import (
    AUTHORIZATION,
    OWNER_SCOPED,
    SCAFFOLDING_MODES,
    Check,
    NoCheck,
    PublicAPIRoute,
    PublicRouteNotAuthorized,
    ServiceChecked,
    assert_every_route_authorized,
    scaffolding_row_count,
)
from tests.community.adapters.http.openapi_v1._route_walk import (
    dependant_of,
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
    """
    live, table = _live_operations(), set(AUTHORIZATION)

    assert live - table == set(), "operations with no AUTHORIZATION row"
    assert table - live == set(), "AUTHORIZATION rows matching no operation"


def test_authorization_and_admission_cover_the_same_operations():
    """Two tables, two questions, one surface.

    ``ADMISSION`` decides whether a *machine* caller is admitted;
    ``AUTHORIZATION`` decides what a *person* must be to the bot. They are
    deliberately separate, but they describe the same set of operations, and a
    row added to one and forgotten in the other is the drift this catches.
    """
    assert set(AUTHORIZATION) == set(ADMISSION)


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


# ── inertness ────────────────────────────────────────────────────────────────


def test_no_live_operation_carries_the_gate():
    """Why this change cannot have altered any answer, proved structurally.

    A status-for-status sweep would only sample the behaviour; this shows the
    seam is attached to nothing at all, so there is no request it could be
    reached on. When the first group migrates, this test changes to name the
    operations that legitimately carry it.
    """
    from agentclaw.community.adapters.http.openapi_v1.bot_access import require_check

    gated = [
        path_of(ctx)
        for ctx in effective_routes(build_public_router())
        if depends_on(dependant_of(ctx), require_check)
    ]

    assert gated == []


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
