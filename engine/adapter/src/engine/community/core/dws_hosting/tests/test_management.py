"""Owner management conformance: real persistence, offline provider boundaries."""

import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.dws_hosting.router import router
from engine.community.api.dws_hosting import router as router_module
from engine.community.core.dws_hosting.management import HostingManagement
from engine.community.di.dws_management import ManagementEndpoint
from engine.community.kernel.dws_connection import (
    AuthorizationChallenge,
    AuthorizationStatus,
)
from engine.community.kernel.dws_management import (
    HostingSettings,
    SettingsUpdate,
    snapshot_suffix,
)
from engine.community.plugins.dws_hosting.management import SqliteHostingManagementStore
from .test_runtime import runtime_case
from unittest.mock import AsyncMock
from engine.community.di.dws_management import build_management_endpoint


class Authorization:
    def __init__(self, binding):
        self.binding = binding
        self.authorized = True
        self.logins = 0
        self.finish = asyncio.Event()

    async def status(self, actor):
        return AuthorizationStatus(
            authenticated=self.authorized,
            organization_id=self.binding.organization_id,
            user_id=self.binding.account_id,
        )

    async def authorize(self, actor, callback):
        self.logins += 1
        await callback(
            AuthorizationChallenge(
                verification_url="https://example.com/authorize", user_code="ABCD"
            )
        )
        await self.finish.wait()
        return await self.status(actor)


@pytest.fixture
def case(tmp_path):
    runtime, state, _, _, directory, _, _, intake, clock = runtime_case(tmp_path)
    state.set_enabled(runtime.binding.binding_id, False, now_ms=clock())
    store = SqliteHostingManagementStore(directory)
    auth = Authorization(runtime.binding)
    management = HostingManagement(runtime, auth, store, clock_ms=clock)
    return management, runtime, store, auth, intake


async def call(case, operation, data=None, actor=None):
    manager = case[0]
    return await manager.command(
        actor or manager.binding.owner_id, operation, data or {}
    )


async def save(case):
    settings = await call(case, "settings.get")
    return await call(
        case,
        "settings.save",
        {
            "expected_version": settings["version"],
            "settings": {
                "rules": "Only reply to work questions",
                "label_suffix": " test",
            },
        },
    )


@pytest.mark.parametrize(
    "op",
    [
        "status",
        "settings.get",
        "settings.save",
        "enabled",
        "authorization.start",
        "authorization.check",
        "conversations",
        "takeover",
        "issues",
        "resolve",
    ],
)
async def test_nonowner_rejected_before_any_provider(case, op):
    with pytest.raises(PermissionError):
        await call(case, op, actor="someone-else")
    assert case[3].logins == 0


async def test_rule_save_atomic_conflict_and_suffix_snapshot(case):
    saved = await save(case)
    assert saved["settings"]["rules"]
    with pytest.raises(ValueError, match="version_conflict"):
        await call(
            case,
            "settings.save",
            {"expected_version": 0, "settings": {"rules": "stale"}},
        )
    assert await call(case, "settings.get") == saved
    with case[2].db.transaction() as db:
        text = db.execute("SELECT rule_text FROM bindings").fetchone()[0]
    assert snapshot_suffix(text) == " test"
    assert snapshot_suffix("legacy rule") == ""
    assert snapshot_suffix("{}") == ""


async def test_enable_auth_rules_disable_and_recovery(case):
    manager, runtime, _, auth, intake = case
    saved = await save(case)
    auth.authorized = False
    with pytest.raises(RuntimeError, match="authorization_required"):
        await call(
            case, "enabled", {"enabled": True, "expected_version": saved["version"]}
        )
    assert not (await call(case, "status"))["enabled"]
    auth.authorized = True
    try:
        await call(
            case, "enabled", {"enabled": True, "expected_version": saved["version"]}
        )
        await asyncio.wait_for(intake.entered.wait(), 1)
        status = await call(case, "status")
        assert status["running"] and not status["intake_ready"]
        with pytest.raises(RuntimeError):
            await call(case, "authorization.start")
        with pytest.raises(RuntimeError):
            await call(case, "authorization.check")
        with pytest.raises(RuntimeError):
            await call(case, "resolve", {})
        await call(case, "enabled", {"enabled": False, "expected_version": 0})
        assert not (await call(case, "status"))["running"]
        assert not (await runtime.status(manager.binding.owner_id)).enabled
    finally:
        await manager.close()


async def test_login_is_pollable_singleflight_and_never_enables(case):
    try:
        assert await call(case, "authorization.check") == {"authenticated": True}
        await call(case, "authorization.start")
        await call(case, "authorization.start")
        await asyncio.sleep(0)
        status = await call(case, "status")
        assert status["challenge"]["user_code"] == "ABCD"
        assert not status["enabled"] and case[3].logins == 1
        case[3].finish.set()
        await asyncio.sleep(0)
        assert (await call(case, "status"))["authorization"] == "authorized"
        assert (await call(case, "status"))["challenge"] is None
    finally:
        await case[0].close()


async def test_takeover_list_and_invalid_commands(case):
    await call(
        case, "takeover", {"conversation_id": "peer-conversation", "taken_over": True}
    )
    result = await call(case, "conversations")
    assert any(
        x["taken_over"]
        for x in result["items"]
        if x["conversation_id"] == "peer-conversation"
    )
    with pytest.raises(ValueError):
        await call(case, "conversations", {"limit": 101})
    with pytest.raises(ValueError):
        await call(case, "whatever")
    with pytest.raises(ValueError):
        await call(case, "status", {"owner_id": "someone"})


@pytest.mark.parametrize(
    "values",
    [{"timezone": "not/a/zone"}, {"window": "custom"}, {"label_suffix": "\x00"}],
)
def test_settings_validation(values):
    with pytest.raises((ValueError, KeyError)):
        HostingSettings(**values)


def test_signed_endpoint_tamper_replay_wrong_scope_expiry(case):
    manager, _, store, _, _ = case
    key = b"offline-test-management-key-32-bytes"
    verifier = AsyncMock()
    verifier.verify.side_effect = lambda payload, signature: hmac.compare_digest(
        hmac.new(key, payload.encode(), hashlib.sha256).hexdigest(), signature
    )
    app = FastAPI()
    app.state.dws_management = ManagementEndpoint(
        manager, store, manager.binding.bot_id, manager.binding.owner_id, verifier
    )
    app.include_router(router)
    client = TestClient(app)

    def signed(**changes):
        value = {
            "bot_id": manager.binding.bot_id,
            "actor_id": manager.binding.owner_id,
            "operation": "settings.get",
            "data": {},
            "timestamp_ms": int(time.time() * 1000),
            "nonce": uuid4().hex,
            **changes,
        }
        payload = json.dumps(value)
        return {
            "payload": payload,
            "signature": hmac.new(key, payload.encode(), hashlib.sha256).hexdigest(),
        }

    body = signed()
    assert client.post("/api/dws-hosting/command", json=body).status_code == 200
    assert client.post("/api/dws-hosting/command", json=body).status_code == 403
    for bad in [
        signed(actor_id="editor"),
        signed(bot_id="another"),
        signed(timestamp_ms=0),
        {**signed(), "signature": "0" * 64},
    ]:
        assert client.post("/api/dws-hosting/command", json=bad).status_code == 403
    assert client.post(
        "/api/dws-hosting/command", json=signed(operation="unknown")
    ).json()["data"]["management_error"]
    verifier.verify.return_value = False
    verifier.verify.side_effect = None
    assert client.post("/api/dws-hosting/command", json=signed()).status_code == 403
    app.state.dws_management = None
    assert client.post("/api/dws-hosting/command", json=signed()).status_code == 503


def test_proof_expiring_during_verification_never_executes(case, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(router_module, "time", SimpleNamespace(time=lambda: now[0]))

    async def verify(_payload, _signature):
        now[0] += 31
        return True

    manager, _, store, _, _ = case
    service = SimpleNamespace(command=AsyncMock())
    app = FastAPI()
    app.state.dws_management = ManagementEndpoint(
        service,
        store,
        manager.binding.bot_id,
        manager.binding.owner_id,
        SimpleNamespace(verify=verify),
    )
    app.include_router(router)
    payload = json.dumps(
        {
            "bot_id": manager.binding.bot_id,
            "actor_id": manager.binding.owner_id,
            "operation": "settings.get",
            "data": {},
            "timestamp_ms": 1000000,
            "nonce": "b" * 32,
        }
    )
    response = TestClient(app).post(
        "/api/dws-hosting/command", json={"payload": payload, "signature": "0" * 64}
    )
    assert response.status_code == 403
    service.command.assert_not_awaited()


async def test_login_failure_cancel_and_enable_pending(case):
    await save(case)
    try:
        await call(case, "authorization.start")
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="authorization_pending"):
            await call(case, "enabled", {"enabled": True, "expected_version": 1})
        await case[0].close()
        assert (await call(case, "status"))["authorization"] == "cancelled"
        case[3].authorize = AsyncMock(side_effect=RuntimeError("private-token-detail"))
        await call(case, "authorization.start")
        await asyncio.sleep(0)
        assert (await call(case, "status"))["authorization"] == "failed"
        assert "private-token" not in str(await call(case, "status"))
    finally:
        await case[0].close()


async def test_restore_runs_only_saved_consent_and_preserves_failure(case, caplog):
    manager, runtime, _, _, _ = case
    runtime.run = AsyncMock(side_effect=RuntimeError("private-details"))
    await manager.restore()
    runtime.run.assert_not_awaited()
    await save(case)
    runtime.state.set_enabled(
        runtime.binding.binding_id, True, now_ms=runtime.clock_ms()
    )
    await manager.restore()
    await asyncio.sleep(0)
    assert (await call(case, "status"))["error"] == "runtime_stopped"
    assert "RuntimeError" in caplog.text and "frames=" in caplog.text
    assert "private-details" not in caplog.text
    runtime.run.assert_awaited_once()
    await manager.close()


async def test_enable_requires_current_saved_nonempty_rules(case):
    with pytest.raises(ValueError, match="version_conflict"):
        await call(case, "enabled", {"enabled": True, "expected_version": 0})
    await save(case)
    with pytest.raises(ValueError, match="version_conflict"):
        await call(case, "enabled", {"enabled": True, "expected_version": 0})


async def test_operator_projection_and_resolution_use_runtime_contract(case):
    assert await call(case, "issues") == {"items": []}
    case[1].resolve_manual = AsyncMock()
    # Validation remains owned by the existing phase-6 resolution value.
    from engine.community.kernel.dws_runtime import Resolution

    value = Resolution(
        kind="delivery",
        target_id="outbox-1",
        evidence="Owner checked the exact conversation",
    )
    assert await call(case, "resolve", value.model_dump()) == {"resolved": True}
    case[1].resolve_manual.assert_awaited_once_with(case[0].binding.owner_id, value)


def test_management_factory_and_store_defenses(case, tmp_path):
    runtime = case[1]
    verifier = AsyncMock()
    endpoint = build_management_endpoint(
        runtime, None, case[2].db.path.parent, verifier
    )
    assert endpoint.owner_id == runtime.binding.owner_id
    assert endpoint.verifier is verifier
    with pytest.raises(PermissionError):
        case[2].get(runtime.binding.model_copy(update={"owner_id": "other"}))
    with pytest.raises(ValueError, match="rules_required"):
        case[2].save(
            runtime.binding,
            SettingsUpdate(expected_version=0, settings=HostingSettings()),
        )
    with case[2].db.transaction() as db:
        db.executemany(
            "INSERT INTO hosting_request_nonces VALUES (?,?)",
            ((str(i), 1000) for i in range(10000)),
        )
    with pytest.raises(ValueError, match="request_capacity"):
        case[2].consume_nonce("new", 1001)
    case[2].consume_nonce("new", 200000)
