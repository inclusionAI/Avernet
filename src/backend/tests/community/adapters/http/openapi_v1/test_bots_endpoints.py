"""Endpoint tests for the public ``/openapi/v1/bots`` API (Track B).

A minimal FastAPI app hosts the bots router with the caller principal overridden
and the bot services bound to mocks via the injector — mirroring the internal
router test harness. The real authenticator stays a stub; ``require_principal``
is overridden per test to supply (or withhold) a caller.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.bots.router import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.plugin_api.passport import PassportPlugin

BOT = {
    "bot_id": "b1", "bot_name": "N", "bot_desc": "D", "active_engine": "teclaw",
    "bot_type": "personal", "status": "ACTIVE", "owner_id": "u1",
    "device_binding": {"device_id": "dev-9"},
}


@pytest.fixture
def svc():
    m = MagicMock()
    m.get_bot.return_value = BOT
    m.list_bots_by_conditions.return_value = {"total": 1, "items": [BOT]}
    m.check_bot_name_exists.return_value = True
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
def client(svc, policy, passport):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=svc)
            binder.bind(PolicyServiceProtocol, to=policy)
            binder.bind(PassportPlugin, to=passport)

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
