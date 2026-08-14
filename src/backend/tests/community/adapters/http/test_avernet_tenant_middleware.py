"""AvernetTenantMiddleware — per-request tenant, reset on the way out.

Proves the spec's non-leakage criteria at the ASGI boundary: a request's tenant
is established for its lifetime and never leaks into the next request, including
after a request fails with an error.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentclaw.community.adapters.http.openapi_v1.dependencies as deps
from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

pytestmark = pytest.mark.integration


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)

    @app.get("/openapi/v1/bots/whoami")
    def public_whoami():
        return {"tenant": get_current_avernet_tenant()}

    @app.get("/internal/whoami")
    def internal_whoami():
        return {"tenant": get_current_avernet_tenant()}

    @app.get("/openapi/v1/bots/boom")
    def boom():
        raise RuntimeError("handler blew up")

    return app


def test_default_tenant_for_every_path():
    # Stage 1: the seam resolves to the default tenant, and non-public paths use
    # it too — so every current response is unchanged.
    client = TestClient(_app())
    assert client.get("/openapi/v1/bots/whoami").json()["tenant"] == "teamclaw"
    assert client.get("/internal/whoami").json()["tenant"] == "teamclaw"


def test_public_request_uses_resolved_tenant(monkeypatch):
    # Simulate the future verifier: resolve the tenant from a forwarded header.
    monkeypatch.setattr(
        deps, "resolve_avernet_tenant", lambda req: req.headers.get("x-tenant", "teamclaw")
    )
    client = TestClient(_app())
    r = client.get("/openapi/v1/bots/whoami", headers={"x-tenant": "acme"})
    assert r.json()["tenant"] == "acme"


def test_tenant_does_not_leak_between_requests(monkeypatch):
    monkeypatch.setattr(
        deps, "resolve_avernet_tenant", lambda req: req.headers.get("x-tenant", "teamclaw")
    )
    client = TestClient(_app())
    # Request 1 runs under "acme"...
    assert (
        client.get("/openapi/v1/bots/whoami", headers={"x-tenant": "acme"}).json()[
            "tenant"
        ]
        == "acme"
    )
    # ...request 2 (no header, internal path) must see the default, not "acme".
    assert client.get("/internal/whoami").json()["tenant"] == "teamclaw"


def test_tenant_does_not_leak_after_error(monkeypatch):
    monkeypatch.setattr(
        deps, "resolve_avernet_tenant", lambda req: req.headers.get("x-tenant", "teamclaw")
    )
    client = TestClient(_app(), raise_server_exceptions=False)
    # A request that fails mid-handler under "acme"...
    assert (
        client.get("/openapi/v1/bots/boom", headers={"x-tenant": "acme"}).status_code
        == 500
    )
    # ...must still reset — the next request sees the default tenant.
    assert client.get("/internal/whoami").json()["tenant"] == "teamclaw"
