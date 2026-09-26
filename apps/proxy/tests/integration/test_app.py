"""Integration tests — app boots and endpoints behave under a live ASGI client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest


def _sign(secret: str, payload: dict) -> str:
    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header_b64 = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_b64 = _b64(json.dumps(payload).encode())
    sig = hmac.new(
        secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256
    ).digest()
    return f"{header_b64}.{payload_b64}.{_b64(sig)}"


@pytest.fixture
def app(jwt_secret: str):
    from sandboxproxy.community.adapters.web import build_app
    from sandboxproxy.community.api.identity import resolve_instance_id
    from sandboxproxy.community.bootstrap import (
        ApplicationContainer,
        initialize_services,
    )
    from sandboxproxy.community.config import ConfigLoader

    loaded = ConfigLoader.load()
    container = ApplicationContainer()
    container.config.from_dict(
        {
            "user_config": loaded.user_config.model_dump(),
            "plugins": {
                "resolver": "stub",
                "relay_client": "stub",
            },
            "instance": resolve_instance_id(),
        }
    )
    initialize_services(container)
    return build_app(container, loaded)


@pytest.fixture
def client(app):
    from starlette.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.mark.integration
class TestHealthEndpoints:
    def test_health(self, client) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_hello(self, client) -> None:
        resp = client.get("/hello")
        assert resp.status_code == 200


@pytest.mark.integration
class TestProxypassAuth:
    def test_missing_token(self, client) -> None:
        resp = client.get("/proxypass/ARCA_123")
        assert resp.status_code == 401

    def test_invalid_token(self, client) -> None:
        resp = client.get("/proxypass/ARCA_123", headers={"X-PROXYPASS-TOKEN": "bad"})
        assert resp.status_code == 401

    def test_valid_token(self, client, jwt_secret: str) -> None:
        token = _sign(jwt_secret, {"sub": "u1", "exp": time.time() + 3600})
        # stub resolver returns a fixed local destination; forward will fail
        # to reach it, but the request must NOT be rejected as unauthorized.
        resp = client.get(
            "/proxypass/ARCA_123",
            headers={"X-PROXYPASS-TOKEN": token},
        )
        assert resp.status_code != 401

    def test_valid_token_via_query_param(self, client, jwt_secret: str) -> None:
        token = _sign(jwt_secret, {"sub": "u1", "exp": time.time() + 3600})
        resp = client.get(f"/proxypass/ARCA_123?x-proxypass-token={token}")
        assert resp.status_code != 401

    def test_valid_token_header_case_insensitive(self, client, jwt_secret: str) -> None:
        token = _sign(jwt_secret, {"sub": "u1", "exp": time.time() + 3600})
        resp = client.get(
            "/proxypass/ARCA_123",
            headers={"x-proxypass-token": token},
        )
        assert resp.status_code != 401
