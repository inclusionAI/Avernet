"""Integration: ``POST /admin/apps/migrate-from-baas`` end to end over HTTP.

Drives the real ASGI app against a freshly provisioned in-memory schema — the
same route table, request model and composition root a deployment gets — and
closes the loop the migration exists to close: a caller's *existing* secbaas key
authenticating against the gateway registry afterwards, with the bot grants it
carried.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from gateway.community.adapters.web.app import create_app
from gateway.community.bootstrap import get_container
from gateway.community.core.app import APIKeyGenerator, AppRepository, AppRow
from gateway.community.core.baas_migration import (
    BaasApiKeyRow,
    BotAppGrantLogRow,
    BotAppGrantRow,
)


@pytest.fixture(autouse=True)
def _schema_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provision a fresh in-memory schema per test before ``create_app()``.

    The community default leaves ``create_schema`` / ``seed_data`` false
    (matching BaaS); the ``e2e-sqlite`` overlay flips them on. The
    process-global container is reset so each test re-bootstraps — these tests
    write, so a shared database would leak state between them.
    """
    import gateway.community.bootstrap as bootstrap_mod

    bootstrap_mod._container = None
    monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "e2e-sqlite")


def _seed_baas_key(
    *,
    app_type: str = "app",
    app_id: str = "third-party-app",
    policy: str | None = None,
    env: str = "prod",
    status: str = "ACTIVE",
) -> str:
    """Insert a ``baas_api_key`` row; return the plaintext key its holder has."""
    api_key = APIKeyGenerator.generate()
    db = get_container().plugins().database()
    with db.orm_session() as session:
        session.add(
            BaasApiKeyRow(
                api_key_hash=APIKeyGenerator.hash_key(api_key),
                api_key_prefix=api_key[:8],
                app_id=app_id,
                app_type=app_type,
                status=status,
                owner="u1",
                tenant="team_claw",
                env=env,
                creator="creator-1",
                policy=policy,
            )
        )
    return api_key


async def _post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/apps/migrate-from-baas", json=body)
    return resp.status_code, resp.json()


async def test_migrated_key_keeps_working_and_carries_its_grants() -> None:
    app = create_app()  # bootstraps the container the seed below writes into
    transport = ASGITransport(app=app)
    api_key = _seed_baas_key(
        policy=json.dumps({"allowed_bots": ["bot-1:u1", "bot-2:u2"]})
    )

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/apps/migrate-from-baas",
            json={"api_key": api_key, "app_name": "Migrated App"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["app_name"] == "Migrated App"
    assert body["app_type"] == "app"
    assert body["env"] == "prod"
    assert body["tenant"] == "teamclaw"
    assert body["api_key_prefix"] == api_key[:8]
    assert body["grants_created"] == 2
    assert body["grants"] == [
        {"bot_id": "bot-1", "user_id": "u1", "owner_id": "u1", "env": "prod"},
        {"bot_id": "bot-2", "user_id": "u2", "owner_id": "u2", "env": "prod"},
    ]

    # The endpoint mints nothing: the caller's own key is the credential.
    assert "api_key" not in body

    db = get_container().plugins().database()
    with db.orm_session() as session:
        row = session.get(AppRow, body["id"])
        assert row is not None
        assert row.token is None
        assert api_key not in (row.api_key_hash or "")
        grants = list(session.scalars(select(BotAppGrantRow)))
        logged = list(session.scalars(select(BotAppGrantLogRow)))
    assert {g.app_id for g in grants} == {body["id"]}
    assert {g.avernet_tenant for g in grants} == {"teamclaw"}
    assert [ln.action for ln in logged] == ["granted", "granted"]

    # The loop this whole endpoint exists to close: the key the caller already
    # had now resolves against the gateway's own registry.
    record = await AppRepository(db).find_app_by_credential(api_key)
    assert record is not None
    assert record.id == body["id"]
    assert record.app_name == "Migrated App"


async def test_taken_app_name_returns_409_naming_both_halves() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    first = _seed_baas_key(policy=json.dumps({"allowed_bots": ["bot-1:u1"]}))
    second = _seed_baas_key(policy=json.dumps({"allowed_bots": ["bot-2:u2"]}))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.post(
            "/admin/apps/migrate-from-baas",
            json={"api_key": first, "app_name": "Duplicate"},
        )
        clash = await client.post(
            "/admin/apps/migrate-from-baas",
            json={"api_key": second, "app_name": "Duplicate"},
        )

    assert ok.status_code == 201
    assert clash.status_code == 409
    body = clash.json()
    assert body["code"] == 409002
    assert "different app_name" in body["message"]
    assert body["data"] == {
        "outcome": "app_name_taken",
        "app_name": "Duplicate",
        "env": "prod",
    }

    # Nothing was written for the refused attempt — the caller's secbaas key is
    # still their only working credential, which is what makes a retry safe.
    db = get_container().plugins().database()
    assert await AppRepository(db).find_app_by_credential(second) is None
    with db.orm_session() as session:
        assert len(list(session.scalars(select(BotAppGrantRow)))) == 1


async def test_repeating_a_migration_returns_409_already_migrated() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    api_key = _seed_baas_key(policy=json.dumps({"allowed_bots": ["bot-1:u1"]}))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/admin/apps/migrate-from-baas",
            json={"api_key": api_key, "app_name": "Once"},
        )
        again = await client.post(
            "/admin/apps/migrate-from-baas",
            json={"api_key": api_key, "app_name": "Twice"},
        )

    assert first.status_code == 201
    assert again.status_code == 409
    body = again.json()
    # A different subcode from a name clash: the caller's next move is to stop,
    # not to retry with another name.
    assert body["code"] == 409001
    assert body["data"]["outcome"] == "already_migrated"
    assert body["data"]["app_id"] == first.json()["id"]
    assert body["data"]["app_name"] == "Once"


async def test_unknown_key_returns_404() -> None:
    create_app()
    status, body = await _post(
        {"api_key": APIKeyGenerator.generate(), "app_name": "Nobody"}
    )
    assert status == 404
    assert body["code"] == 404001
    assert body["data"]["outcome"] == "key_not_found"


async def test_wildcard_policy_returns_422_and_writes_nothing() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    api_key = _seed_baas_key(policy=json.dumps({"allowed_bots": ["*"]}))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/apps/migrate-from-baas",
            json={"api_key": api_key, "app_name": "Everything"},
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422001
    assert body["data"]["outcome"] == "wildcard_policy"

    db = get_container().plugins().database()
    with db.orm_session() as session:
        assert list(session.scalars(select(AppRow))) == []


async def test_bot_type_key_grants_the_bot_named_in_app_id() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    api_key = _seed_baas_key(app_type="bot", app_id="bot-9:u9", policy=None)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/apps/migrate-from-baas",
            json={"api_key": api_key, "app_name": "My Bot Key"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["app_type"] == "bot"
    assert body["grants"] == [
        {"bot_id": "bot-9", "user_id": "u9", "owner_id": "u9", "env": "prod"}
    ]


async def test_inactive_source_key_is_not_migrated() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    api_key = _seed_baas_key(
        status="INACTIVE", policy=json.dumps({"allowed_bots": ["bot-1:u1"]})
    )

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/apps/migrate-from-baas",
            json={"api_key": api_key, "app_name": "Retired"},
        )

    assert resp.status_code == 404


async def test_missing_fields_are_named_without_quoting_the_body() -> None:
    """A validation failure must not hand the caller's key back to them.

    FastAPI's own "field required" error renders ``input`` as the entire request
    body, which on this endpoint holds the plaintext key. The request model
    defaults both fields so that error cannot arise, and emptiness is reported
    through the admin envelope instead.
    """
    create_app()

    status, body = await _post({"api_key": "SUPER-SECRET-PLAINTEXT"})
    assert status == 400
    assert body["data"]["missing"] == ["app_name"]
    assert "SUPER-SECRET-PLAINTEXT" not in json.dumps(body)

    status, body = await _post({"app_name": "No Key"})
    assert status == 400
    assert body["data"]["missing"] == ["api_key"]

    status, body = await _post({"api_key": "   ", "app_name": "  "})
    assert status == 400
    assert body["data"]["missing"] == ["api_key", "app_name"]


async def test_surrounding_whitespace_is_tolerated() -> None:
    """Both values are routinely pasted; padding must not break the lookup."""
    app = create_app()
    transport = ASGITransport(app=app)
    api_key = _seed_baas_key(policy=json.dumps({"allowed_bots": ["bot-1:u1"]}))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/apps/migrate-from-baas",
            json={"api_key": f"  {api_key}\n", "app_name": "  Padded  "},
        )

    assert resp.status_code == 201
    assert resp.json()["app_name"] == "Padded"


async def test_registering_a_taken_app_name_returns_409_not_500() -> None:
    """``POST /admin/apps`` shares the ``(app_name, env)`` key this change added.

    Left unmapped, a name clash there would surface as a 500 — a fault the
    caller cannot act on, for a condition they fix by renaming.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    payload = {
        "app_name": "Same Name",
        "owners": "org-1",
        "app_type": "assistant",
        "tenant": "t",
        "creator": "admin",
        "env": "prod",
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/admin/apps", json=payload)
        clash = await client.post("/admin/apps", json=payload)

    assert first.status_code == 201
    assert clash.status_code == 409
    assert clash.json()["data"] == {"app_name": "Same Name", "env": "prod"}
