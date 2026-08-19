"""Endpoint tests for public dormant Bot activation routes."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.bots.router import router
from agentclaw.community.api.bot_dormant_service import (
    BotDormantActivateServiceProtocol,
)
from agentclaw.community.api.bot_service import BotServiceProtocol

# A personal cloud bot in the only state activate accepts.
PERSONAL_RECYCLED = {
    "bot_id": "b1",
    "bot_name": "Sleeper",
    "bot_type": "personal",
    "status": "RECYCLED",
    "owner_id": "u1",
}


@pytest.fixture
def bot_service():
    svc = MagicMock()
    svc.get_bot.return_value = PERSONAL_RECYCLED
    return svc


@pytest.fixture
def activate_service():
    svc = MagicMock()
    svc.activate.return_value = {"status": "REACTIVATING", "message": "激活中"}
    return svc


@pytest.fixture
def client(bot_service, activate_service):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bot_service)
            binder.bind(BotDormantActivateServiceProtocol, to=activate_service)

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


def test_activate_personal_cloud_bot(client, activate_service):
    data = _ok(client.post("/openapi/v1/bots/b1/activate"))

    assert data["bot_id"] == "b1"
    assert data["status"] == "REACTIVATING"
    # Delegated with the caller's owner identity — the handler must not invent
    # a different user for the reactivation flow.
    assert activate_service.activate.call_args.kwargs == {
        "bot_id": "b1",
        "user_id": "u1",
    }


def test_activate_refuses_desktop_bot(client, bot_service, activate_service):
    bot_service.get_bot.return_value = {**PERSONAL_RECYCLED, "bot_type": "desktop"}

    resp = client.post("/openapi/v1/bots/b1/activate")

    assert resp.status_code == 409
    activate_service.activate.assert_not_called()


def test_activate_refuses_service_bot(client, bot_service, activate_service):
    bot_service.get_bot.return_value = {**PERSONAL_RECYCLED, "bot_type": "service"}

    resp = client.post("/openapi/v1/bots/b1/activate")

    assert resp.status_code == 409
    activate_service.activate.assert_not_called()


def test_activate_missing_for_owner_is_not_found(client, bot_service):
    # BotService.get_bot raises BotNotFoundError for a bot not owned by the
    # caller — the handler surfaces it as 404, never 200 with empty data.
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotNotFoundError,
    )

    bot_service.get_bot.side_effect = BotNotFoundError("bot not found: bX")

    resp = client.post("/openapi/v1/bots/bX/activate")

    assert resp.status_code == 404
    assert resp.json()["message"] == "Not found"
