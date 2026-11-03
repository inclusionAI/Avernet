"""Proof introspection is read-only, bounded and independent of DWS-only secrets."""

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.plugin_api.dws_identity import DwsRolloutIdentity

from agentclaw.community.adapters.http.dws_hosting.router import router
from agentclaw.community.di import dws_management_config as config

KEY = "existing-principal-signing-key-for-offline-tests"


def test_verifier_and_runtime_share_identity_policy_without_rewriting_proof(client):
    class AliasIdentity(DwsRolloutIdentity):
        def key(self, value):
            return "owner" if value in {"owner", "alias"} else value

    client.app.state.injector.binder.bind(DwsRolloutIdentity, to=AliasIdentity())
    client.app.state.injector.binder.bind(SystemConfigServiceProtocol, to=InstanceProvider(
        SimpleNamespace(get_config=lambda **_: {"mode": "allowlist", "staff_ids": ["alias"]})
    ))
    body = proof(operation="enabled", data={"enabled": True})
    assert client.post("/internal/dws-hosting/verify-command", json=body).json() == {"verified": True}
    assert client.post("/internal/dws-hosting/runtime-eligibility", json=body).json() == {"allowed": True}
    tampered = dict(body, payload=body["payload"].replace('"owner"', '"alias"'))
    assert client.post("/internal/dws-hosting/verify-command", json=tampered).status_code == 403


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DWS_MANAGEMENT_MASTER_KEY", raising=False)
    monkeypatch.setattr(
        config,
        "get_principal_verifier_config",
        lambda: SimpleNamespace(signing_key=KEY),
    )
    app = FastAPI()
    app.include_router(router)
    injector = Injector()
    injector.binder.bind(
        SystemConfigServiceProtocol,
        to=InstanceProvider(SimpleNamespace(get_config=lambda **_: {"mode": "all"})),
    )
    attach_injector(app, injector)
    return TestClient(app)


@pytest.mark.parametrize(
    "mode,allowed", [("all", True), ("disabled", False), ("allowlist", False)]
)
def test_runtime_proof_rechecks_current_policy(client, mode, allowed):
    client.app.state.injector.binder.bind(
        SystemConfigServiceProtocol,
        to=InstanceProvider(SimpleNamespace(get_config=lambda **_: {"mode": mode})),
    )
    body = proof(operation="enabled", data={"enabled": True}, timestamp_ms=0)
    assert client.post(
        "/internal/dws-hosting/runtime-eligibility", json=body
    ).json() == {"allowed": allowed}
    assert (
        client.post("/internal/dws-hosting/verify-command", json=body).status_code
        == 403
    )


@pytest.mark.parametrize(
    "changes", [{}, {"operation": "enabled", "data": {"enabled": False}}]
)
def test_runtime_proof_requires_enable(client, changes):
    assert (
        client.post(
            "/internal/dws-hosting/runtime-eligibility", json=proof(**changes)
        ).status_code
        == 403
    )


def proof(**changes):
    value = {
        "bot_id": "bot",
        "actor_id": "owner",
        "operation": "status",
        "data": {},
        "timestamp_ms": int(time.time() * 1000),
        "nonce": "a" * 32,
        **changes,
    }
    payload = json.dumps(value)
    signature = hmac.new(
        config.DwsManagementConfig(KEY.encode()).bot_key(
            value["bot_id"], value["actor_id"]
        ),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"payload": payload, "signature": signature}


def test_existing_secret_without_dws_env(client, monkeypatch):
    assert config.get_dws_management_config().key == KEY.encode()
    # An obsolete DWS-specific variable must not override the platform secret.
    monkeypatch.setenv("DWS_MANAGEMENT_MASTER_KEY", "ignored-old-setting")
    result = client.post("/internal/dws-hosting/verify-command", json=proof())
    assert result.status_code == 200
    assert result.json() == {"verified": True}
    assert config.get_dws_management_config().key == KEY.encode()


@pytest.mark.parametrize(
    "field,value",
    [
        ("bot_id", "other"),
        ("actor_id", "editor"),
        ("operation", "enabled"),
        ("data", {"enabled": True}),
        ("nonce", "b" * 32),
        ("timestamp_ms", 0),
    ],
)
def test_tampering_any_command_field_is_refused(client, field, value):
    body = proof()
    payload = json.loads(body["payload"])
    payload[field] = value
    body["payload"] = json.dumps(payload)
    assert (
        client.post("/internal/dws-hosting/verify-command", json=body).status_code
        == 403
    )


@pytest.mark.parametrize("age", [-31000, 31000])
def test_even_valid_signatures_expire(client, age):
    body = proof(timestamp_ms=int(time.time() * 1000) + age)
    assert (
        client.post("/internal/dws-hosting/verify-command", json=body).status_code
        == 403
    )


@pytest.mark.parametrize("payload", ["not-json", "{}", "[]"])
def test_malformed_payload_is_refused(client, payload):
    body = {"payload": payload, "signature": "0" * 64}
    result = client.post("/internal/dws-hosting/verify-command", json=body)
    assert result.status_code == 403
    assert payload not in result.json().get("detail", "")


def test_missing_platform_key_fails_closed(client, monkeypatch):
    monkeypatch.setattr(
        config,
        "get_principal_verifier_config",
        lambda: SimpleNamespace(signing_key=""),
    )
    assert (
        client.post("/internal/dws-hosting/verify-command", json=proof()).status_code
        == 503
    )


@pytest.mark.parametrize(
    "change",
    [
        {"payload": "x" * 24001},
        {"signature": "bad"},
        {"redirect_url": "https://other"},
    ],
)
def test_wire_limits_and_unknown_fields(client, change):
    assert (
        client.post(
            "/internal/dws-hosting/verify-command", json={**proof(), **change}
        ).status_code
        == 422
    )
