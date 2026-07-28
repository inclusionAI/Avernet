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

_CREATE_BODY = {
    "bot_name": "NewBot", "bot_desc": "d", "engine": "teclaw",
    "cluster_name": "ANDC", "bot_type": "personal",
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
    bad = {**_CREATE_BODY, "cluster_name": "ACRA"}  # teclaw must be ANDC
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
    _ok(client.get(
        "/openapi/v1/bots/b1/auth-status"
        "?engine=teclaw&cluster_name=ANDC&bot_name=NewBot&bot_desc=d"
    ))
    kw = svc.create_bot.call_args.kwargs
    assert kw["engine_type"] == "teclaw"  # not defaulted to openclaw
    assert kw["bot_name"] == "NewBot"
    assert kw["bot_desc"] == "d"


def test_auth_status_engine_cluster_mismatch_400(client, svc):
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


def test_rejected_authorization_is_not_reported_as_success(client, passport):
    """R1/F8: a terminal auth state must not come back as 200/200000 OK."""
    passport.query_auth_status.return_value = {"status": "REJECTED"}
    resp = client.get("/openapi/v1/bots/b1/auth-status")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400000
    assert body["data"]["status"] == "REJECTED"  # caller can still see why
