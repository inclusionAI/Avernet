"""Endpoint tests for ``GET /openapi/v1/bots/hello``.

The route is a smoke test for integrators, so the properties worth pinning are
the ones an integrator would rely on: a fixed payload in the standard envelope,
no request input of any kind, the literal path resolving ahead of the
``{bot_id}`` wildcard, and a caller-less request answering 401 rather than a
greeting.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.contracts import CODE_OK
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.hello.router import (
    HELLO_MESSAGE,
    router,
)

PATH = "/openapi/v1/bots/hello"


@pytest.fixture
def client():
    """The hello router alone, with a verified caller supplied."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    return TestClient(app)


def test_hello_returns_the_fixed_greeting(client):
    response = client.get(PATH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == CODE_OK
    assert body["message"] == "OK"
    assert body["data"] == {"message": HELLO_MESSAGE}
    assert "request_id" in body


def test_hello_takes_no_input(client):
    """No path, query or body parameters — the contract, not just the handler."""
    operation = client.app.openapi()["paths"][PATH]["get"]

    assert operation.get("parameters", []) == []
    assert "requestBody" not in operation


def test_hello_ignores_query_and_body(client):
    """Extra input changes nothing; the answer is the same fixed payload."""
    response = client.request("GET", PATH, params={"name": "x"}, json={"name": "x"})

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"message": HELLO_MESSAGE}


def test_hello_resolves_ahead_of_the_bot_id_wildcard():
    """``/bots/hello`` must not be read as a bot whose id is ``hello``.

    ``build_public_router`` mounts the literal sub-groups before the bots group;
    this asserts the resulting order rather than trusting the list stayed sorted.
    """
    paths = [route.path for route in _api_routes(build_public_router())]

    assert PATH in paths
    assert paths.index(PATH) < paths.index("/openapi/v1/bots/{bot_id}")


def test_hello_requires_a_verified_caller():
    """No principal, no greeting — the surface-wide auth covers this route too.

    Uses the production catch-all from ``app.py`` (as the seam tests do) because
    ``require_principal`` raises in a *dependency*: it is that handler, not
    ``@envelope_errors``, that turns the failure into an enveloped 401.
    """
    from agentclaw.community.adapters.http.app import _unhandled_exception_handler

    app = FastAPI()
    app.include_router(build_public_router())
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    response = TestClient(app, raise_server_exceptions=False).get(PATH)

    assert response.status_code == 401
    body = response.json()
    assert body["message"] == "Unauthorized"
    assert body["data"] is None


def _api_routes(router) -> list:
    """Every real route under ``router``, in mount order.

    ``include_router`` stores a lazy wrapper rather than copying routes, so the
    nesting ``build_public_router`` creates has to be walked (same helper as
    ``test_principal_seam``) — reading ``app.routes`` finds only the docs routes.
    """
    found = []
    for route in getattr(router, "routes", []):
        if hasattr(route, "dependant"):
            found.append(route)
        elif hasattr(route, "original_router"):
            found.extend(_api_routes(route.original_router))
        else:
            found.extend(_api_routes(route))
    return found
