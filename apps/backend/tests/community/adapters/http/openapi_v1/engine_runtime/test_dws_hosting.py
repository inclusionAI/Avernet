"""DWS commands must remain owner-only and never enter interactive chat."""

import hashlib
import hmac
import json
from types import SimpleNamespace
from injector import InstanceProvider
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.plugin_api.dws_identity import DwsRolloutIdentity

import pytest

from agentclaw.community.adapters.http.openapi_v1.dws_hosting.router import router
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult
from agentclaw.community.di.dws_management_config import (
    DwsManagementConfig,
    get_dws_management_config,
)
from .conftest import BOT, OWNER, fails, ok

KEY = b"offline-test-management-key-32-bytes"


def test_eligibility_uses_composed_identity_policy(client, relay):
    class AliasIdentity(DwsRolloutIdentity):
        def key(self, value):
            return OWNER if value == "alias" else value

    client.app.state.injector.binder.bind(DwsRolloutIdentity, to=AliasIdentity())
    client.app.state.injector.binder.bind(SystemConfigServiceProtocol, to=InstanceProvider(
        SimpleNamespace(get_config=lambda **_: {"mode": "allowlist", "staff_ids": ["alias"]})
    ))
    assert ok(client.get(f"/openapi/v1/bots/{BOT}/dws-hosting/eligibility")) == {"available": True}
    assert not relay.attempts


@pytest.fixture
def client(make_client):
    client = make_client(router)
    client.app.state.injector.binder.bind(
        SystemConfigServiceProtocol,
        to=InstanceProvider(SimpleNamespace(get_config=lambda **_: {"mode": "all"})),
    )
    client.app.dependency_overrides[get_dws_management_config] = lambda: (
        DwsManagementConfig(KEY)
    )
    return client


@pytest.mark.parametrize(
    "policy,allowed",
    [
        (None, False),
        ({"mode": "disabled"}, False),
        ({"mode": "all"}, True),
        ({"mode": "allowlist", "staff_ids": [OWNER]}, True),
    ],
)
def test_eligibility_does_not_contact_adapter(client, relay, policy, allowed):
    client.app.state.injector.binder.bind(
        SystemConfigServiceProtocol,
        to=InstanceProvider(SimpleNamespace(get_config=lambda **_: policy)),
    )
    assert ok(client.get(f"/openapi/v1/bots/{BOT}/dws-hosting/eligibility")) == {
        "available": allowed
    }
    assert not relay.attempts
    if not allowed:
        fails(client.get(f"/openapi/v1/bots/{BOT}/dws-hosting"), 403)
        assert not relay.attempts
        assert (
            client.put(
                f"/openapi/v1/bots/{BOT}/dws-hosting/enabled",
                json={"expected_version": 1, "enabled": False},
            ).status_code
            == 200
        )


@pytest.mark.parametrize(
    "method,path,body,operation",
    [
        ("GET", "", None, "status"),
        ("GET", "/settings", None, "settings.get"),
        (
            "PUT",
            "/settings",
            {"expected_version": 1, "settings": {"rules": "reply to work questions"}},
            "settings.save",
        ),
        ("PUT", "/enabled", {"expected_version": 1, "enabled": True}, "enabled"),
        ("POST", "/authorization", None, "authorization.start"),
        ("POST", "/authorization/check", None, "authorization.check"),
        ("GET", "/conversations", None, "conversations"),
        ("PUT", "/takeover", {"conversation_id": "c1", "taken_over": True}, "takeover"),
    ],
)
def test_owner_commands_signed_with_resolved_identity(
    client, relay, method, path, body, operation
):
    relay.results = [EngineResult(data={"ok": True})]
    response = client.request(
        method,
        f"/openapi/v1/bots/{BOT}/dws-hosting{path}",
        json=body,
        params={"user_id": OWNER, "owner_id": "forged", "entity_id": "forged"},
    )
    assert ok(response) == {"ok": True}
    call = relay.calls[0]
    assert call["path"] == "/api/dws-hosting/command"
    assert call["stage"] == "draft" and call["method"] == "POST"
    payload = json.loads(call["body"]["payload"])
    assert payload["actor_id"] == OWNER and payload["bot_id"] == BOT
    assert payload["operation"] == operation
    assert hmac.compare_digest(
        call["body"]["signature"],
        hmac.new(
            DwsManagementConfig(KEY).bot_key(BOT, OWNER),
            call["body"]["payload"].encode(),
            hashlib.sha256,
        ).hexdigest(),
    )


def test_container_keys_cannot_authorize_another_bot_or_owner():
    config = DwsManagementConfig(KEY)
    assert config.bot_key(BOT, OWNER) != KEY
    assert config.bot_key(BOT, OWNER) != config.bot_key("other", OWNER)
    assert config.bot_key(BOT, OWNER) != config.bot_key(BOT, "other")


def test_explicit_user_must_match_authenticated_caller(client, relay):
    fails(
        client.get(f"/openapi/v1/bots/{BOT}/dws-hosting?user_id=other"),
        403,
    )
    assert not relay.attempts


def test_app_only_principal_refused(make_client, relay):
    client = make_client(router, principal={"app_id": "app"})
    fails(client.get(f"/openapi/v1/bots/{BOT}/dws-hosting"), 401)
    assert not relay.attempts


def test_collaborator_cannot_manage_even_with_owner_hint(make_client, relay):
    relay.add_operator("editor")
    client = make_client(router, caller="editor")
    client.app.state.injector.binder.bind(
        SystemConfigServiceProtocol,
        to=InstanceProvider(SimpleNamespace(get_config=lambda **_: {"mode": "all"})),
    )
    fails(
        client.get(f"/openapi/v1/bots/{BOT}/dws-hosting", params={"owner_id": OWNER}),
        404,
    )
    assert not relay.attempts


@pytest.mark.parametrize(
    "bot_type,engine",
    [
        ("service", "openclaw"),
        ("personal", "hermes"),
        ("personal", "deepseek_harness"),
        ("personal", "aicoding"),
    ],
)
def test_unrelated_engines_never_forward(client, relay, bot_type, engine):
    relay.bots[(BOT, OWNER)] = BotFacts(
        bot_id=BOT, owner_id=OWNER, bot_type=bot_type, active_engine=engine
    )
    fails(client.get(f"/openapi/v1/bots/{BOT}/dws-hosting"), 409)
    assert not relay.attempts


def test_defense_in_depth_checks_resolved_owner(client, relay):
    relay.bots[(BOT, OWNER)] = BotFacts(
        bot_id=BOT, owner_id="other", bot_type="personal", active_engine="openclaw"
    )
    relay.operators.add((BOT, OWNER, OWNER))
    fails(client.get(f"/openapi/v1/bots/{BOT}/dws-hosting"), 404)
    assert not relay.attempts


def test_missing_deployment_key_is_unavailable(client, relay):
    client.app.dependency_overrides[get_dws_management_config] = lambda: (
        DwsManagementConfig(b"")
    )
    fails(client.get(f"/openapi/v1/bots/{BOT}/dws-hosting"), 503)
    assert not relay.attempts


def test_refusal_is_not_reported_as_success(client, relay):
    relay.results = [EngineResult(data={"management_error": "operation_refused"})]
    fails(client.get(f"/openapi/v1/bots/{BOT}/dws-hosting"), 409)


@pytest.mark.parametrize(
    "path,body",
    [
        ("/enabled", {"expected_version": 0, "enabled": "yes"}),
        (
            "/settings",
            {"expected_version": 0, "settings": {"rules": "x", "command": "arbitrary"}},
        ),
        (
            "/takeover",
            {"conversation_id": "c", "taken_over": True, "owner_id": "other"},
        ),
    ],
)
def test_untrusted_fields_rejected_before_transport(client, relay, path, body):
    assert (
        client.put(f"/openapi/v1/bots/{BOT}/dws-hosting{path}", json=body).status_code
        == 422
    )
    assert not relay.attempts


def test_invalid_page_rejected(client, relay):
    assert (
        client.get(
            f"/openapi/v1/bots/{BOT}/dws-hosting/conversations?limit=101"
        ).status_code
        == 422
    )
    assert not relay.attempts
