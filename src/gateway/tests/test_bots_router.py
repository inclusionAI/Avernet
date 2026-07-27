"""Contract + auth-integration tests for the bots group."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient

from gateway.community.adapters.web.app import create_app

_TOKEN_HEADER = "x-user-token"


def _google_transport() -> httpx.MockTransport:
    """A mock Google userinfo transport for integration tests (no network)."""

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        token = auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""
        if token == "good":
            return httpx.Response(200, json={"sub": "u", "email": "u@example.com"})
        return httpx.Response(401, text="invalid token")

    return httpx.MockTransport(handler)


def _openapi() -> dict[str, Any]:
    return TestClient(create_app()).get("/openapi.json").json()


def _client(*, google: bool = False) -> TestClient:
    transport = _google_transport() if google else None
    return TestClient(
        create_app(google_transport=transport), raise_server_exceptions=False
    )


def test_bots_routes_present() -> None:
    paths = _openapi()["paths"]
    assert "/openapi/v1/bots" in paths
    assert "/openapi/v1/bots/{bot_id}" in paths
    assert "/openapi/v1/bots/check-name" in paths


def test_every_v1_operation_declares_security() -> None:
    paths = _openapi()["paths"]
    operations = [
        (path, method, op)
        for path, item in paths.items()
        if path.startswith("/openapi/v1/")
        for method, op in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert operations
    for path, method, op in operations:
        marker = op.get("x-avernet-security")
        assert marker, f"{method} {path} missing x-avernet-security"
        assert isinstance(marker, list) and marker, f"{method} {path} empty marker"


def test_responses_use_envelope() -> None:
    schemas = _openapi()["components"]["schemas"]
    assert any(name.startswith("Envelope") for name in schemas)


def test_requires_authentication_without_token() -> None:
    # No presented Google access token → 401 (no cookie fallback).
    assert _client().get("/openapi/v1/bots").status_code == 401


def test_auth_passes_with_verified_google_token() -> None:
    # A verified Google access token clears auth; the stub handler then raises
    # (500), proving the request got past auth (auth would 401 otherwise).
    resp = _client(google=True).get("/openapi/v1/bots", headers={_TOKEN_HEADER: "good"})
    assert resp.status_code == 500


def test_unverified_google_token_is_rejected() -> None:
    # A present-but-unverifiable token → 401 (terminal, no fallback).
    resp = _client(google=True).get("/openapi/v1/bots", headers={_TOKEN_HEADER: "bad"})
    assert resp.status_code == 401
