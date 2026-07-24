"""Namespace invariant: the ``/openapi/v1`` surface is the bots domain only.

The gateway forwards ``/openapi/v1/*`` transparently, so nothing internal may
live under that prefix — every public route must sit under ``/openapi/v1/bots``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

from agentclaw.community.adapters.http.openapi_v1 import build_public_router


def test_public_router_is_populated_and_under_bots() -> None:
    # Assert against the router directly so the check does not depend on which
    # routers a given deploy profile mounts on the app.
    routes = [r for r in build_public_router().routes if isinstance(r, APIRoute)]
    assert len(routes) > 50
    offenders = [r.path for r in routes if not r.path.startswith("/openapi/v1/bots")]
    assert not offenders, f"re-homed routes escaped the bots prefix: {offenders}"


def test_no_internal_routes_leak_into_public_namespace(
    app_with_testing_modules: FastAPI,
) -> None:
    offenders = [
        route.path
        for route in app_with_testing_modules.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/openapi/v1")
        and not route.path.startswith("/openapi/v1/bots")
    ]
    assert not offenders, (
        f"non-bots routes leaked into the public namespace: {offenders}"
    )
