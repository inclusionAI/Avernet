"""Integration: /admin endpoints issue usable credentials end-to-end."""

from __future__ import annotations

import jwt
from httpx import ASGITransport, AsyncClient

from gateway.community.adapters.web.app import create_app
from gateway.community.bootstrap._authn import build_database
from gateway.community.core.access_key import AccessKeyRepository
from gateway.community.core.app import AppRepository
from gateway.community.plugins.principal_signer.bare._plugin import _DEV_FALLBACK_KEY


async def test_issue_access_key_via_http() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/access-keys",
            json={
                "access_key": "ak-http",
                "tenant": "t",
                "expire_at": "2027-01-01T00:00:00",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["access_key"] == "ak-http"
    assert body["tenant"] == "t"
    token = body["token"]

    decoded = jwt.decode(token, _DEV_FALLBACK_KEY, algorithms=["HS256"])
    assert decoded["typ"] == "access_key"
    assert decoded["sub"] == "ak-http"

    rec = await AccessKeyRepository(build_database()).find_access_key_by_token(token)
    assert rec is not None
    assert rec.access_key == "ak-http"


async def test_register_app_via_http() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/apps",
            json={
                "app_id": "app-http",
                "app_name": "Http App",
                "owners": "org-1",
                "app_type": "assistant",
                "tenant": "t",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["app_id"] == "app-http"
    token = body["token"]

    decoded = jwt.decode(token, _DEV_FALLBACK_KEY, algorithms=["HS256"])
    assert decoded["typ"] == "app"
    assert decoded["sub"] == "app-http"
    assert "exp" not in decoded

    rec = await AppRepository(build_database()).find_app_by_token(token)
    assert rec is not None
    assert rec.app_id == "app-http"


async def test_missing_field_returns_422() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/access-keys",
            json={"access_key": "x"},  # missing tenant + expire_at
        )
    assert resp.status_code == 422


async def test_bad_expire_at_returns_422() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/access-keys",
            json={
                "access_key": "x",
                "tenant": "t",
                "expire_at": "not-a-date",
            },
        )
    assert resp.status_code == 422


class _BoomIssuer:
    async def issue(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")


async def test_issuance_failure_returns_500() -> None:
    app = create_app()
    app.state.access_key_issuer = _BoomIssuer()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/access-keys",
            json={
                "access_key": "x",
                "tenant": "t",
                "expire_at": "2027-01-01T00:00:00",
            },
        )
    assert resp.status_code == 500
