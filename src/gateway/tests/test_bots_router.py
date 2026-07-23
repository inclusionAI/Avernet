"""Contract + auth-integration tests for the bots group."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from gateway.community.adapters.web.app import create_app


def _openapi() -> dict[str, Any]:
    return TestClient(create_app()).get("/openapi.json").json()


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
    assert operations  # sanity: there are v1 operations
    for path, method, op in operations:
        assert "x-avernet-security" in op, f"{method} {path} missing x-avernet-security"


def test_responses_use_envelope() -> None:
    schemas = _openapi()["components"]["schemas"]
    assert any(name.startswith("Envelope") for name in schemas)


def test_requires_authentication_without_session() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert client.get("/openapi/v1/bots").status_code == 401


def test_auth_passes_with_session_cookie() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    # A session cookie clears auth; the stub handler then raises (500), which
    # proves the request got past authentication (auth would 401 otherwise).
    resp = client.get("/openapi/v1/bots", headers={"cookie": "SSO_TOKEN=x"})
    assert resp.status_code == 500
