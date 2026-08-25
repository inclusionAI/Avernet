"""Integration tests for proxypass route error/edge branches (bare resolver)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
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
def app(tmp_path, monkeypatch):
    secret = "route-secret"
    monkeypatch.setenv("SANDBOXPROXY_JWT_SECRET", secret)
    cfg = tmp_path / "application.yaml"
    cfg.write_text(
        "app_name: sandboxproxy\n"
        "user_config:\n"
        "  plugins:\n"
        "    resolver: prefix\n"
        "    relay_client: stub\n"
        "  jwt:\n"
        f"    secret: {secret}\n"
        "  aliyun_ack_cluster:\n"
        "    api_server: https://ack.internal.example\n"
        "  baas:\n"
        "    host: http://baas.internal.example\n"
        "  teclaw:\n"
        "    host: http://teclaw.internal.example\n"
    )
    monkeypatch.setenv("SANDBOXPROXY_CONFIG_PATH", str(cfg))

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
            "plugins": {"resolver": "prefix", "relay_client": "stub"},
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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
class TestProxypassBranches:
    def test_options_preflight(self, client) -> None:
        token = _sign("route-secret", {"exp": time.time() + 3600})
        resp = client.options("/proxypass/ARCA_1", headers=_auth(token))
        assert resp.status_code == 204

    def test_unrouted_path_404(self, client) -> None:
        resp = client.get("/nonexistent")
        assert resp.status_code == 404

    def test_unsupported_target_400(self, client) -> None:
        token = _sign("route-secret", {"exp": time.time() + 3600})
        resp = client.get("/proxypass/FOO_123", headers=_auth(token))
        assert resp.status_code == 400

    def test_bad_arca_target_400(self, client) -> None:
        token = _sign("route-secret", {"exp": time.time() + 3600})
        resp = client.get("/proxypass/ARCA_", headers=_auth(token))
        assert resp.status_code == 400
