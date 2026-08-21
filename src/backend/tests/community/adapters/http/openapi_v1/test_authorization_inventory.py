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

from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.community.adapters.http.openapi_v1._route_walk import (
    depends_on,
    effective_routes,
    original_route_of,
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
