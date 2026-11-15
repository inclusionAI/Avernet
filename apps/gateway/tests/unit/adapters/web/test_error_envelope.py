"""Failures the gateway answers itself use the standard response envelope.

The forwarding path builds its envelope by hand; these are the failures the
framework raises on the gateway's own routes — a refused credential raised from
a dependency, routing 404/405, request validation, an escaped exception — which
FastAPI would otherwise answer with ``{"detail": ...}`` or plain text.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from gateway.community.adapters.web import require_principal
from gateway.community.adapters.web._cors import install_cors
from gateway.community.adapters.web._envelope import install_error_envelope
from gateway.community.adapters.web.app import create_app
from gateway.community.config import CorsConfig
from gateway.community.spi.auth import AuthError
from gateway.community.tracer import get_tracer_plugin

_ENVELOPE_KEYS = {"code", "message", "data", "request_id"}
_ID = re.compile(r"[0-9a-f]{32}")


class _Refusing:
    async def authenticate(self, *_: Any) -> Any:
        raise AuthError("missing credential")


class _Body(BaseModel):
    api_key: str
    count: int


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.state.authenticator = _Refusing()

    @app.get("/guarded")
    async def _guarded(_: Any = Depends(require_principal)) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/teapot")
    async def _teapot() -> None:
        raise HTTPException(status_code=418, detail="short and stout")

    @app.post("/body")
    async def _body(body: _Body) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/boom")
    async def _boom() -> None:
        raise RuntimeError("exploded")

    install_error_envelope(app)
    install_cors(app, CorsConfig(allow_origins=[], allow_origin_regex=[]))
    return TestClient(app, raise_server_exceptions=False)


def _envelope(resp: Any) -> dict[str, Any]:
    body = resp.json()
    assert set(body) == _ENVELOPE_KEYS
    assert body["data"] is None
    # Never empty and never the tracer's "-" placeholder, even with no span
    # recording (this fixture app installs no tracing middleware at all).
    assert _ID.fullmatch(body["request_id"])
    return body


def test_refused_credential_in_a_dependency_is_an_envelope(client: TestClient) -> None:
    resp = client.get("/guarded")
    assert resp.status_code == 401
    body = _envelope(resp)
    # Same code the forwarding path answers a refused credential with.
    assert body["code"] == 401001
    assert body["message"] == "missing credential"


def test_unknown_route_is_an_envelope(client: TestClient) -> None:
    resp = client.get("/no-such-route")
    assert resp.status_code == 404
    body = _envelope(resp)
    assert body == {**body, "code": 404000, "message": "Not Found"}


def test_wrong_method_is_an_envelope_and_keeps_allow(client: TestClient) -> None:
    resp = client.delete("/guarded")
    assert resp.status_code == 405
    assert resp.headers["allow"] == "GET"
    body = _envelope(resp)
    assert body["code"] == 405000
    assert body["message"] == "Method Not Allowed"


def test_http_exception_keeps_its_own_message(client: TestClient) -> None:
    resp = client.get("/teapot")
    assert resp.status_code == 418
    body = _envelope(resp)
    assert body["code"] == 418000
    assert body["message"] == "short and stout"


def test_validation_failure_is_an_envelope_without_echoing_input(
    client: TestClient,
) -> None:
    resp = client.post("/body", json={"api_key": "sk-plaintext-secret"})
    assert resp.status_code == 422
    body = _envelope(resp)
    assert body["code"] == 422000
    assert body["message"] == "Invalid request"
    assert "sk-plaintext-secret" not in resp.text


def test_escaped_exception_is_an_envelope(client: TestClient) -> None:
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = _envelope(resp)
    assert body["code"] == 500000
    assert body["message"] == "Internal Server Error"
    assert "exploded" not in resp.text


def test_gateway_app_installs_the_envelope() -> None:
    client = TestClient(create_app())
    resp = client.post("/admin/access-keys", json={})
    assert resp.status_code == 422
    assert _envelope(resp)["code"] == 422000


def test_gateway_app_500_carries_the_requests_trace_id() -> None:
    """The outermost 500 handler runs after the tracing span has closed; the id
    must still be the one the request was traced under, not a placeholder."""
    app = create_app()
    seen: list[str] = []

    @app.get("/admin/boom")
    async def _boom() -> None:
        seen.append(get_tracer_plugin().get_trace_id())
        raise RuntimeError("exploded")

    # Ahead of the catch-all forward route, which would otherwise claim the path.
    app.router.routes.insert(0, app.router.routes.pop())
    resp = TestClient(app, raise_server_exceptions=False).get("/admin/boom")

    assert resp.status_code == 500
    assert _ID.fullmatch(seen[0])
    assert _envelope(resp)["request_id"] == seen[0]


def test_each_request_gets_its_own_id(client: TestClient) -> None:
    first = _envelope(client.get("/no-such-route"))["request_id"]
    second = _envelope(client.get("/no-such-route"))["request_id"]
    assert first != second
