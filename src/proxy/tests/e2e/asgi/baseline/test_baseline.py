"""E2E baseline test — in-process ASGI smoke of app lifecycle + health."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import pytest

from sandboxproxy.community.api.identity import resolve_instance_id

_SECRET = "e2e-baseline-secret"


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
def app():
    os.environ["SANDBOXPROXY_JWT_SECRET"] = _SECRET
    from sandboxproxy.community.adapters.web import build_app
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
            "plugins": {"resolver": "stub", "relay_client": "stub"},
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


@pytest.mark.e2e_asgi
class TestBaselineLifecycle:
    def test_health_after_startup(self, client) -> None:
        assert client.get("/health").status_code == 200

    def test_hello_after_startup(self, client) -> None:
        assert client.get("/hello").status_code == 200

    def test_proxypass_requires_auth(self, client) -> None:
        assert client.get("/proxypass/ARCA_1").status_code == 401

    def test_proxypass_accepts_valid_token(self, client) -> None:
        token = _sign(_SECRET, {"sub": "u1", "exp": time.time() + 3600})
        resp = client.get(
            "/proxypass/ARCA_1", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code != 401
