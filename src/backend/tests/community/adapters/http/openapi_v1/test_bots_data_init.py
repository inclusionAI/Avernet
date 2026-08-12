"""Endpoint tests for ``POST /openapi/v1/bots/{bot_id}/data-init`` (Track B #84).

A minimal FastAPI app hosts the bots router with the caller principal overridden
and only the two services the handler touches bound via the injector — mirroring
the legacy ``POST /api/bots/{id}/data-init`` fire-and-forget contract: the
handler dispatches and returns ``in_progress`` without awaiting the async init.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.bots.router import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.data_init_service import DataInitServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError

# Personal cloud bot with an entity attached — the precondition data-init needs
# (the same _engine_config_target helper resolves entity_id/entity_type from).
PERSONAL_BOT = {
    "bot_id": "b1",
    "bot_name": "Cloud",
    "bot_desc": "d",
    "active_engine": "openclaw",
    "bot_type": "personal",
    "status": "ACTIVE",
    "owner_id": "u1",
    "entity_id": "u1",
    "entity_type": "staff",
}


@pytest.fixture
def bot_service():
    svc = MagicMock()
    svc.get_bot.return_value = PERSONAL_BOT
    return svc


@pytest.fixture
def data_init_service():
    svc = AsyncMock()
    svc.trigger_init = AsyncMock(return_value={"status": "completed"})
    return svc


@pytest.fixture
def client(bot_service, data_init_service):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bot_service)
            binder.bind(DataInitServiceProtocol, to=data_init_service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "u1")


def _ok(resp):
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["code"] == 200000
    return body["data"]


def test_data_init_dispatches_async_and_returns_in_progress(client, data_init_service):
    data = _ok(client.post("/openapi/v1/bots/b1/data-init", json={"force": False}))

    assert data["bot_id"] == "b1"
    assert data["status"] == "in_progress"
    # Fire-and-forget: trigger_init is dispatched via asyncio.ensure_future with
    # the caller's owner_id + the bot's resolved entity, and never awaited in
    # the request path.
    data_init_service.trigger_init.assert_called_once()
    kwargs = data_init_service.trigger_init.call_args.kwargs
    assert kwargs == {
        "bot_id": "b1",
        "owner_id": "u1",
        "entity_id": "u1",
        "entity_type": "staff",
        "force": False,
    }


def test_data_init_forwards_force_flag(client, data_init_service):
    _ok(client.post("/openapi/v1/bots/b1/data-init", json={"force": True}))

    assert data_init_service.trigger_init.call_args.kwargs["force"] is True


def test_data_init_refuses_desktop_bot(client, bot_service, data_init_service):
    bot_service.get_bot.return_value = {**PERSONAL_BOT, "bot_type": "desktop"}

    resp = client.post("/openapi/v1/bots/b1/data-init", json={})

    assert resp.status_code == 409
    data_init_service.trigger_init.assert_not_called()


def test_data_init_refuses_service_bot(client, bot_service, data_init_service):
    bot_service.get_bot.return_value = {**PERSONAL_BOT, "bot_type": "service"}

    resp = client.post("/openapi/v1/bots/b1/data-init", json={})

    assert resp.status_code == 409
    data_init_service.trigger_init.assert_not_called()


def test_data_init_missing_for_owner_is_not_found(client, bot_service):
    bot_service.get_bot.side_effect = BotNotFoundError("bot not found: bX")

    resp = client.post("/openapi/v1/bots/bX/data-init", json={})

    assert resp.status_code == 404
    assert resp.json()["message"] == "Not found"
