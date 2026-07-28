"""Endpoint tests for the public ``/openapi/v1/bots`` API (Track B).

A minimal FastAPI app hosts the bots router with the caller principal overridden
and the bot services bound to mocks via the injector — mirroring the internal
router test harness. The real authenticator stays a stub; ``require_principal``
is overridden per test to supply (or withhold) a caller.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

import importlib

from agentclaw.community.adapters.http.openapi_v1.bots.router import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.core.services.engine_config import EngineConfigService
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin

# The bots package re-exports ``router`` (the APIRouter), which shadows the
# submodule attribute — so fetch the real module object to patch module globals.
bots_router = importlib.import_module(
    "agentclaw.community.adapters.http.openapi_v1.bots.router"
)

BOT = {
    "bot_id": "b1", "bot_name": "N", "bot_desc": "D", "active_engine": "teclaw",
    "bot_type": "personal", "status": "ACTIVE", "owner_id": "u1",
    "entity_id": "u1", "entity_type": "staff",
    "device_binding": {"device_id": "dev-9"},
}


@pytest.fixture
def svc():
    m = MagicMock()
    m.get_bot.return_value = BOT
    m.list_bots_by_conditions.return_value = {"total": 1, "items": [BOT]}
    m.check_bot_name_exists.return_value = True
    m.update_bot.return_value = {**BOT, "bot_name": "Renamed"}
    m.restart_bot.return_value = {**BOT, "status": "PENDING"}
    m.delete_bot.return_value = True
    m.check_create_bot_preflight.return_value = None
    m.create_bot.return_value = BOT
    return m


@pytest.fixture
def bot_repo():
    return MagicMock()


@pytest.fixture
def skill_set_factory():
    m = MagicMock()
    m.create.return_value.get_bot_mcp_codes.return_value = []
    return m


@pytest.fixture
def auth_rel():
    return MagicMock()


@pytest.fixture
def engine_config():
    m = AsyncMock()
    m.read_bot_config.return_value = {"k": "v"}
    m.write_bot_config.return_value = None
    return m


@pytest.fixture
def policy():
    m = MagicMock()
    m.get_bots_ceiling.return_value = 7
    return m


@pytest.fixture
def passport():
    m = MagicMock()
    m.query_agent_passport.return_value = {"agent_code": "ac-1"}
    return m


@pytest.fixture
def client(svc, policy, passport, engine_config, bot_repo, skill_set_factory, auth_rel):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=svc)
            binder.bind(PolicyServiceProtocol, to=policy)
            binder.bind(PassportPlugin, to=passport)
            binder.bind(EngineConfigService, to=engine_config)
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(SkillSetServiceFactoryProtocol, to=skill_set_factory)
            binder.bind(AuthRelationshipPlugin, to=auth_rel)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    return TestClient(app)


def _ok(resp, code=200000):
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["code"] == code, body
    assert "request_id" in body
    return body["data"]


def test_get_bot(client):
    data = _ok(client.get("/openapi/v1/bots/b1"))
    assert data["bot_id"] == "b1"
    assert data["engine"] == "teclaw"
    assert data["cluster_name"] == "ANDC"  # derived from engine
    assert data["owner_entity_id"] == "u1"


def test_list_bots(client):
    data = _ok(client.get("/openapi/v1/bots"))
    assert data["total"] == 1
    assert data["items"][0]["bot_id"] == "b1"


def test_list_bots_filters_reach_service(client, svc):
    client.get("/openapi/v1/bots?keyword=x&engine=teclaw&status=ACTIVE&page=2&page_size=5")
    kw = svc.list_bots_by_conditions.call_args.kwargs
    assert kw["owner_id"] == "u1"
    assert kw["bot_name"] == "x"
    assert kw["engine"] == "teclaw"
    assert kw["status"] == "ACTIVE"
    assert kw["page"] == 2 and kw["page_size"] == 5


def test_check_name(client):
    data = _ok(client.get("/openapi/v1/bots/check-name?name=Foo"))
    assert data == {"name": "Foo", "exists": True}


def test_ceiling(client):
    assert _ok(client.get("/openapi/v1/bots/ceiling"))["ceiling"] == 7


def test_status(client):
    data = _ok(client.get("/openapi/v1/bots/b1/status"))
    assert data["status"] == "ACTIVE"
    assert data["is_ready"] is True
    assert data["device_id"] == "dev-9"


def test_passport(client):
    data = _ok(client.get("/openapi/v1/bots/b1/passport"))
    assert data == {"bot_id": "b1", "passport_id": "ac-1"}


def test_passport_missing_is_404(client, passport):
    passport.query_agent_passport.return_value = None
    resp = client.get("/openapi/v1/bots/b1/passport")
    assert resp.status_code == 404


def test_missing_principal_is_401(client):
    client.app.dependency_overrides[require_principal] = lambda: None
    resp = client.get("/openapi/v1/bots/b1")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401000


def test_not_found_is_masked_404(client, svc):
    svc.get_bot.side_effect = BotNotFoundError("Bot not found: b1")
    resp = client.get("/openapi/v1/bots/b1")
    assert resp.status_code == 404
    # Fixed message — never the raw internal text.
    assert resp.json()["message"] == "Not found"


# ----- mutating endpoints (Task 7) -----------------------------------------


def test_update_bot(client, svc):
    data = _ok(client.put("/openapi/v1/bots/b1", json={"bot_name": "Renamed"}))
    assert data["bot_name"] == "Renamed"
    kw = svc.update_bot.call_args
    assert kw.args == ("b1", "u1")
    assert kw.kwargs["bot_name"] == "Renamed"


def test_delete_bot(client, svc):
    data = _ok(client.delete("/openapi/v1/bots/b1"))
    assert data == {"deleted": True}
    svc.delete_bot.assert_called_once_with("b1", "u1")


def test_restart_bot(client):
    data = _ok(client.post("/openapi/v1/bots/b1/restart"))
    assert data["status"] == "PENDING"


def test_get_engine_config(client, engine_config):
    data = _ok(client.get("/openapi/v1/bots/b1/engine-config"))
    assert data == {"k": "v"}
    kw = engine_config.read_bot_config.call_args.kwargs
    assert kw["bot_id"] == "b1" and kw["owner_id"] == "u1"
    assert kw["engine_type"] == "teclaw"  # from bot active_engine


def test_update_engine_config(client, engine_config):
    data = _ok(client.put("/openapi/v1/bots/b1/engine-config", json={"a": 1}))
    assert data == {"a": 1}  # echoes the written config
    kw = engine_config.write_bot_config.call_args.kwargs
    assert kw["config"] == {"a": 1} and kw["owner_id"] == "u1"


def test_mutating_not_found_masked(client, svc):
    svc.get_bot.side_effect = BotNotFoundError("x")
    # engine-config guards via get_bot → masked 404
    assert client.get("/openapi/v1/bots/b1/engine-config").status_code == 404
    svc.update_bot.side_effect = BotNotFoundError("x")
    assert client.put("/openapi/v1/bots/b1", json={"bot_name": "y"}).status_code == 404


# ----- create + auth-status (Task 8) ---------------------------------------

# openclaw is in the default SUPPORTED_ENGINE_TYPES registry; teclaw is NOT
# (it is only available where ENGINE_TYPES is configured to include it), so the
# create path's engine check would reject it here. Tests that specifically need
# the teclaw/ANDC pairing patch the registry.
_CREATE_BODY = {
    "bot_name": "NewBot", "bot_desc": "d", "engine": "openclaw",
    "cluster_name": "ACRA", "bot_type": "personal",
}


def test_create_bot_201(client, svc, passport):
    passport.apply_first_agent_passport.return_value = {"token": "tok", "agent_code": "ac"}
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        resp = client.post("/openapi/v1/bots", json=_CREATE_BODY)
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["code"] == 201000
    assert body["data"]["bot_id"] == "b1"
    svc.create_bot.assert_called_once()


def test_create_bot_202_pending(client, passport):
    passport.apply_first_agent_passport.return_value = {"token": None, "iframe_url": "http://auth"}
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        resp = client.post("/openapi/v1/bots", json=_CREATE_BODY)
    assert resp.status_code == 202, resp.json()
    body = resp.json()
    assert body["code"] == 202000
    assert body["data"]["iframe_url"] == "http://auth"


def test_create_bot_cluster_mismatch_400(client, svc):
    bad = {**_CREATE_BODY, "cluster_name": "ANDC"}  # openclaw must be ACRA
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        resp = client.post("/openapi/v1/bots", json=bad)
    assert resp.status_code == 400, resp.json()
    svc.create_bot.assert_not_called()


def test_create_bot_missing_principal_401(client):
    client.app.dependency_overrides[require_principal] = lambda: None
    resp = client.post("/openapi/v1/bots", json=_CREATE_BODY)
    assert resp.status_code == 401


def test_auth_status_pending(client, passport):
    passport.query_auth_status.return_value = {"status": "PENDING"}
    data = _ok(client.get("/openapi/v1/bots/b1/auth-status"))
    assert data["status"] == "PENDING"
    assert data["bot"] is None


def test_auth_status_issued(client, svc, passport):
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    data = _ok(client.get("/openapi/v1/bots/b1/auth-status"))
    assert data["status"] == "ISSUED"
    assert data["bot"]["bot_id"] == "b1"
    svc.create_bot.assert_called_once()


def test_auth_status_issued_preserves_create_attributes(client, svc, passport):
    """Re-supplied attributes reach completion so the bot isn't downgraded."""
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    with patch.object(
        bots_router, "_get_engine_types", return_value=["openclaw", "teclaw"]
    ):
        _ok(client.get(
            "/openapi/v1/bots/b1/auth-status"
            "?engine=teclaw&cluster_name=ANDC&bot_name=NewBot&bot_desc=d"
        ))
    kw = svc.create_bot.call_args.kwargs
    assert kw["engine_type"] == "teclaw"  # not defaulted to openclaw
    assert kw["bot_name"] == "NewBot"
    assert kw["bot_desc"] == "d"


def test_auth_status_engine_cluster_mismatch_400(client, svc):
    # teclaw is enabled here on purpose: the rejection must come from the
    # engine/cluster bijection, not incidentally from the registry check.
    with patch.object(
        bots_router, "_get_engine_types", return_value=["openclaw", "teclaw"]
    ):
        resp = client.get(
            "/openapi/v1/bots/b1/auth-status?engine=teclaw&cluster_name=ACRA"
        )
    assert resp.status_code == 400
    svc.create_bot.assert_not_called()


# ----- round-1 review regressions ------------------------------------------


def test_application_bot_not_ready_until_repos_cloned(client, svc):
    """R1/F1: ACTIVE alone must not report an application bot as ready."""
    svc.get_bot.return_value = {
        **BOT, "template_type": "applicationCoding", "active_engine": "aicoding",
        "ext": {"start_status": "STARTING"},
    }
    assert _ok(client.get("/openapi/v1/bots/b1/status"))["is_ready"] is False

    # ...and ready once the clone reports SUCCEEDED.
    svc.get_bot.return_value = {
        **BOT, "template_type": "applicationCoding", "active_engine": "aicoding",
        "ext": {"start_status": "SUCCEEDED"},
    }
    assert _ok(client.get("/openapi/v1/bots/b1/status"))["is_ready"] is True


def test_non_application_bot_ignores_start_status(client, svc):
    """R1/F1 guard: the extra gate must not regress ordinary bots."""
    svc.get_bot.return_value = {**BOT, "ext": {"start_status": "STARTING"}}
    assert _ok(client.get("/openapi/v1/bots/b1/status"))["is_ready"] is True


def test_base_service_error_is_enveloped(client, svc):
    """R1/F2: a bare BotServiceError must not escape as {"detail": ...}."""
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotServiceError,
    )

    svc.get_bot.side_effect = BotServiceError("device blew up")
    resp = client.get("/openapi/v1/bots/b1")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == 500000
    assert body["data"] is None
    assert "request_id" in body
    assert body["message"] == "Internal error"  # no internal text leaked


def test_update_rejects_invalid_bot_name(client, svc):
    """R1/F4: the public update path enforces the same name rule as create."""
    resp = client.put("/openapi/v1/bots/b1", json={"bot_name": "bad@name"})
    assert resp.status_code == 400
    svc.update_bot.assert_not_called()


def test_update_syncs_passport_identity(client, passport):
    """R1/F5: renaming must not leave the Passport carrying stale metadata."""
    client.put("/openapi/v1/bots/b1", json={"bot_name": "Renamed"})
    kw = passport.update_passport.call_args.kwargs
    assert kw["bot_id"] == "b1"
    assert kw["user_id"] == "u1"
    assert kw["bot_name"] == "Renamed"


def test_update_without_identity_change_skips_passport(client, passport):
    """No name/desc change → no passport call (mirrors the internal route)."""
    client.put("/openapi/v1/bots/b1", json={})
    passport.update_passport.assert_not_called()


def test_update_rejects_unknown_and_immutable_fields(client, svc):
    """R2/F9: sending `engine` must fail, not be silently ignored."""
    resp = client.put("/openapi/v1/bots/b1", json={"engine": "teclaw"})
    assert resp.status_code == 422
    svc.update_bot.assert_not_called()


def test_clearing_description_still_syncs_passport(client, passport):
    """R2/F10: `bot_desc=""` is a real change and must reach the Passport."""
    client.put("/openapi/v1/bots/b1", json={"bot_desc": ""})
    kw = passport.update_passport.call_args.kwargs
    assert kw["bot_desc"] == ""


def test_engine_config_device_not_bound_is_enveloped(client, engine_config):
    """R2/F11: engine-config failures must not escape as raw {"detail": ...}."""
    from agentclaw.community.core.devices.services.device_context import (
        DeviceNotBoundError,
    )

    engine_config.read_bot_config.side_effect = DeviceNotBoundError("no binding")
    resp = client.get("/openapi/v1/bots/b1/engine-config")
    assert resp.status_code == 409
    assert resp.json()["code"] == 409000


def test_engine_config_malformed_json_is_enveloped(client, engine_config):
    """R2/F11: a malformed stored config is enveloped, not a bare 500 detail."""
    import json

    engine_config.read_bot_config.side_effect = json.JSONDecodeError("bad", "{", 0)
    resp = client.get("/openapi/v1/bots/b1/engine-config")
    assert resp.status_code == 500
    assert resp.json()["code"] == 500000
    assert resp.json()["data"] is None


def test_pending_auth_forwards_redirect_url(client, passport, svc):
    """R2/F12: a redirect-only Passport response must not lose the handle."""
    passport.apply_first_agent_passport.return_value = {
        "token": None, "redirect_url": "http://redirect", "iframe_url": None,
    }
    with patch.object(bots_router, "generate_bot_id", return_value="default"):
        resp = client.post("/openapi/v1/bots", json=_CREATE_BODY)
    assert resp.status_code == 202
    assert resp.json()["data"]["redirect_url"] == "http://redirect"


def test_deleting_default_bot_is_client_error(client, svc):
    """R2/F13: an unsupported operation is 4xx, not a retryable 500."""
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotOperationNotAllowedError,
    )

    svc.delete_bot.side_effect = BotOperationNotAllowedError("default 不允许删除")
    resp = client.delete("/openapi/v1/bots/default")
    assert resp.status_code == 409
    assert resp.json()["code"] == 409000


def test_unsupported_engine_rejected_before_side_effects(client, svc, passport, bot_repo):
    """R3/F16: an unknown engine must not allocate an id or apply for a Passport."""
    bad = {**_CREATE_BODY, "engine": "not-a-real-engine", "cluster_name": "ACRA"}
    with patch.object(bots_router, "generate_bot_id", return_value="default") as gen:
        resp = client.post("/openapi/v1/bots", json=bad)
    assert resp.status_code == 400
    assert resp.json()["code"] == 400000
    # Rejected up front — no id allocated, no Passport applied, nothing created.
    gen.assert_not_called()
    passport.apply_first_agent_passport.assert_not_called()
    svc.create_bot.assert_not_called()


def test_teclaw_andc_create_allowed_when_engine_configured(client, svc, passport):
    """The ANDC cluster is reachable wherever teclaw is a configured engine.

    teclaw is absent from the default registry, so the engine check rejects it
    unless the deployment enables it via ENGINE_TYPES — this pins that the
    teclaw/ANDC pairing itself is valid, not accidentally unreachable.
    """
    passport.apply_first_agent_passport.return_value = {"token": "tok", "agent_code": "ac"}
    body = {**_CREATE_BODY, "engine": "teclaw", "cluster_name": "ANDC"}
    with patch.object(
        bots_router, "_get_engine_types", return_value=["openclaw", "teclaw"]
    ), patch.object(bots_router, "generate_bot_id", return_value="default"):
        resp = client.post("/openapi/v1/bots", json=body)
    assert resp.status_code == 201, resp.json()
    svc.create_bot.assert_called_once()


def test_desktop_bot_type_rejected(client, svc):
    """R3/F17: desktop bots have their own flow; 201-ing a PENDING shell is wrong."""
    resp = client.post("/openapi/v1/bots", json={**_CREATE_BODY, "bot_type": "desktop"})
    assert resp.status_code == 422
    svc.create_bot.assert_not_called()


def test_missing_auth_status_is_enveloped(client, passport):
    """R3/F18: a null query_auth_status must not escape as a raw 500 detail."""
    passport.query_auth_status.return_value = None
    resp = client.get("/openapi/v1/bots/b1/auth-status")
    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == 502000
    assert body["data"] is None


def test_rejected_authorization_is_not_reported_as_success(client, passport):
    """R1/F8: a terminal auth state must not come back as 200/200000 OK."""
    passport.query_auth_status.return_value = {"status": "REJECTED"}
    resp = client.get("/openapi/v1/bots/b1/auth-status")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400000
    assert body["data"]["status"] == "REJECTED"  # caller can still see why


# ----- round-4 review regressions ------------------------------------------


def test_auth_status_rejects_unknown_engine(client, svc, passport):
    """R4/F19: the completion path must apply POST's engine registry check.

    The bot row is actually inserted here, so an engine ``POST`` rejects must
    not become creatable by echoing it back on the poll.
    """
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    resp = client.get(
        "/openapi/v1/bots/b1/auth-status?engine=not-a-real-engine&cluster_name=ACRA"
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 400000
    svc.create_bot.assert_not_called()


def test_auth_status_rejects_desktop_bot_type(client, svc, passport):
    """R4/F19: ``bot_type`` is restricted on completion exactly as on create."""
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    resp = client.get("/openapi/v1/bots/b1/auth-status?bot_type=desktop")
    assert resp.status_code == 422
    svc.create_bot.assert_not_called()


def test_auth_status_accepts_supported_bot_type(client, svc, passport):
    """The restriction rejects only what create rejects — service still passes."""
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    _ok(client.get("/openapi/v1/bots/b1/auth-status?bot_type=service"))
    assert svc.create_bot.call_args.kwargs["bot_type"] == "service"


def test_engine_config_unknown_provider_is_enveloped(client, engine_config):
    """R4/F21: a documented resolver failure must not escape the envelope.

    ``UnknownProviderError`` is a sibling of ``DeviceNotBoundError``, not a
    subclass, so mapping the latter does not cover it.
    """
    from agentclaw.community.core.devices.services.device_context import (
        UnknownProviderError,
    )

    engine_config.read_bot_config.side_effect = UnknownProviderError("bad provider")
    resp = client.get("/openapi/v1/bots/b1/engine-config")
    assert resp.status_code == 500
    assert resp.json()["code"] == 500000
    assert resp.json()["data"] is None


def test_engine_config_conn_info_failure_is_enveloped(client, engine_config):
    """R4/F21: the underlying device-service call failing is a 502, enveloped."""
    from agentclaw.community.core.devices.services.device_context import (
        ConnInfoBuildError,
    )

    engine_config.read_bot_config.side_effect = ConnInfoBuildError("baas down")
    resp = client.get("/openapi/v1/bots/b1/engine-config")
    assert resp.status_code == 502
    assert resp.json()["code"] == 502000
    assert resp.json()["data"] is None


# ----- round-5 review regressions ------------------------------------------


def test_create_rejects_unappliable_engine_options(client, svc, passport, bot_repo):
    """R5/F22: nothing reads extra_properties, so 201 would discard the input."""
    body = {**_CREATE_BODY, "engine_options": {"model": "x"}}
    with patch.object(bots_router, "generate_bot_id", return_value="default") as gen:
        resp = client.post("/openapi/v1/bots", json=body)
    assert resp.status_code == 400
    assert resp.json()["code"] == 400000
    # Rejected up front — no id allocated, no Passport applied, nothing created.
    gen.assert_not_called()
    passport.apply_first_agent_passport.assert_not_called()
    svc.create_bot.assert_not_called()


def test_create_still_accepts_empty_engine_options(client, svc, passport):
    """The guard rejects only values it would drop; the field itself stays valid."""
    passport.apply_first_agent_passport.return_value = {"token": "t", "agent_code": "a"}
    resp = client.post("/openapi/v1/bots", json={**_CREATE_BODY, "engine_options": {}})
    assert resp.status_code == 201, resp.json()
    svc.create_bot.assert_called_once()


def test_auth_status_validates_cluster_against_default_engine(client, svc, passport):
    """R5/F23: omitting ``engine`` means the default engine, not "no engine".

    Without this the ANDC cluster passed unchecked and completion provisioned
    the ACRA default — a success response contradicting the request.
    """
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    resp = client.get("/openapi/v1/bots/b1/auth-status?cluster_name=ANDC")
    assert resp.status_code == 400
    assert resp.json()["code"] == 400000
    svc.create_bot.assert_not_called()


def test_auth_status_default_engine_accepts_matching_cluster(client, svc, passport):
    """…and the cluster the default engine *does* belong to still passes."""
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_agent_passport.return_value = {"agent_code": "ac"}
    _ok(client.get("/openapi/v1/bots/b1/auth-status?cluster_name=ACRA"))
    svc.create_bot.assert_called_once()


def test_delete_desktop_bot_is_rejected(client, svc):
    """R5/F26: generic delete leaves the BaaS container running."""
    svc.get_bot.return_value = {**BOT, "bot_type": "desktop"}
    resp = client.delete("/openapi/v1/bots/b1")
    assert resp.status_code == 409
    assert resp.json()["code"] == 409000
    svc.delete_bot.assert_not_called()


def test_restart_desktop_bot_is_rejected(client, svc):
    """Same policy for restart — desktop re-provisioning has its own path."""
    svc.get_bot.return_value = {**BOT, "bot_type": "desktop"}
    resp = client.post("/openapi/v1/bots/b1/restart")
    assert resp.status_code == 409
    svc.restart_bot.assert_not_called()


def test_non_desktop_lifecycle_operations_still_work(client, svc):
    """The guard must not block the bot types this surface does manage."""
    _ok(client.delete("/openapi/v1/bots/b1"))
    svc.delete_bot.assert_called_once_with("b1", "u1")
    _ok(client.post("/openapi/v1/bots/b1/restart"))
    svc.restart_bot.assert_called_once()


# ----- round-6 review regressions ------------------------------------------


def test_update_rejects_fields_it_cannot_apply(client, svc):
    """R6/F29: 200 for a request that changed nothing is a lie.

    ``cluster_name`` is engine-derived and the engine is immutable;
    ``engine_options`` belongs to the engine-config endpoints. Neither can be
    applied here, so neither is accepted — same treatment as ``engine`` (F9).
    """
    for field, value in [("cluster_name", "ACRA"), ("engine_options", {"model": "x"})]:
        resp = client.put("/openapi/v1/bots/b1", json={field: value})
        assert resp.status_code == 422, f"{field} was accepted: {resp.json()}"
    svc.update_bot.assert_not_called()


def test_passport_accepts_the_local_plugin_identifier(client, passport):
    """R6/F32: the local plugin issues ``agent_id`` and leaves ``agent_code`` null."""
    passport.query_agent_passport.return_value = {
        "agent_id": "b1", "agent_code": None, "mcps": [],
    }
    data = _ok(client.get("/openapi/v1/bots/b1/passport"))
    assert data == {"bot_id": "b1", "passport_id": "b1"}


def test_passport_prefers_agent_code_when_both_present(client, passport):
    """``agent_code`` stays the primary identifier where the provider issues one."""
    passport.query_agent_passport.return_value = {"agent_id": "b1", "agent_code": "ac-1"}
    data = _ok(client.get("/openapi/v1/bots/b1/passport"))
    assert data["passport_id"] == "ac-1"


def test_passport_absent_is_still_404(client, passport):
    """Neither identifier present still means no passport."""
    passport.query_agent_passport.return_value = {"agent_id": None, "agent_code": None}
    assert client.get("/openapi/v1/bots/b1/passport").status_code == 404
