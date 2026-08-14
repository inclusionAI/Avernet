"""Integration: /admin endpoints issue usable credentials end-to-end."""

from __future__ import annotations

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from gateway.community.adapters.web.app import create_app
from gateway.community.bootstrap import get_container
from gateway.community.core.access_key import AccessKeyRepository
from gateway.community.core.app import AppRepository

# Issued credentials are signed with the gateway's principal signing key, so
# these tests provision one the way a deployment does. They previously decoded
# with the plugin's committed dev fallback; that fallback is gone, because
# minting access-key and app tokens under a key published in the source tree
# means anyone holding the source can mint them too.
_TEST_KEY = "integration-test-shared-secret-32b!!"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provision the key before ``create_app()``; the community resolver reads
    ``{env_prefix}{NAME}_VALUE`` (``configs/application.yaml``)."""
    monkeypatch.setenv("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE", _TEST_KEY)


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
                "creator": "admin",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["access_key"] == "ak-http"
    assert body["tenant"] == "t"
    token = body["token"]

    decoded = jwt.decode(token, _TEST_KEY, algorithms=["HS256"])
    assert decoded["typ"] == "access_key"
    assert decoded["sub"] == "ak-http"

    rec = await AccessKeyRepository(
        get_container().plugins().database()
    ).find_access_key_by_token(token)
    assert rec is not None
    assert rec.access_key == "ak-http"


async def test_register_app_via_http() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/apps",
            json={
                "app_name": "Http App",
                "owners": "org-1",
                "app_type": "assistant",
                "tenant": "t",
                "creator": "admin",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert isinstance(body["id"], int)
    assert body["app_name"] == "Http App"
    assert body["tenant"] == "t"
    assert body["status"] == "ACTIVE"
    token = body["token"]

    decoded = jwt.decode(token, _TEST_KEY, algorithms=["HS256"])
    assert decoded["typ"] == "app"
    assert decoded["sub"] == "Http App"
    assert "exp" not in decoded

    rec = await AppRepository(get_container().plugins().database()).find_app_by_token(
        token
    )
    assert rec is not None
    assert rec.app_name == "Http App"
    assert rec.id == body["id"]


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
                "creator": "admin",
            },
        )
    assert resp.status_code == 500
