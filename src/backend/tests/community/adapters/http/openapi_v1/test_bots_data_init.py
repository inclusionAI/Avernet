"""Endpoint tests for the A-line data-init trigger and status contract."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    bind_edit_lock_seam,
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.bots.router import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.data_init_service import DataInitServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError

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
    svc = MagicMock()
    svc.trigger_init = AsyncMock(return_value={"status": "completed"})
    svc.get_status.return_value = {
        "bot_id": "b1",
        "status": "in_progress",
        "started_at": "2026-08-18T08:00:00+00:00",
    }
    return svc


@pytest.fixture
def client(bot_service, data_init_service):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bot_service)
            binder.bind(DataInitServiceProtocol, to=data_init_service)
            bind_edit_lock_seam(binder)

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

    assert data == {
        "bot_id": "b1",
        "status": "in_progress",
        "message": "data initialization dispatched",
        "started_at": None,
    }
    data_init_service.trigger_init.assert_awaited_once_with(
        bot_id="b1",
        owner_id="u1",
        entity_id="u1",
        entity_type="staff",
        force=False,
        iam_token=None,
    )


def test_data_init_forwards_force_and_iam_cookie(client, data_init_service):
    client.cookies.set("IAM_TOKEN", "iam-secret")
    _ok(client.post("/openapi/v1/bots/b1/data-init", json={"force": True}))

    kwargs = data_init_service.trigger_init.await_args.kwargs
    assert kwargs["force"] is True
    assert kwargs["iam_token"] == "iam-secret"


def test_data_init_status_is_safe_and_does_not_expose_ext(client, data_init_service):
    data = _ok(client.get("/openapi/v1/bots/b1/data-init"))

    assert data == {
        "bot_id": "b1",
        "status": "in_progress",
        "message": None,
        "started_at": "2026-08-18T08:00:00Z",
    }
    assert "ext" not in data
    data_init_service.get_status.assert_called_once_with("b1", "u1")


@pytest.mark.parametrize("method", ["get", "post"])
def test_data_init_refuses_desktop_bot(client, bot_service, data_init_service, method):
    bot_service.get_bot.return_value = {**PERSONAL_BOT, "bot_type": "desktop"}

    resp = client.request(method.upper(), "/openapi/v1/bots/b1/data-init", json={} if method == "post" else None)

    assert resp.status_code == 409
    data_init_service.trigger_init.assert_not_awaited()
    data_init_service.get_status.assert_not_called()


@pytest.mark.parametrize("method", ["get", "post"])
def test_data_init_refuses_service_bot(client, bot_service, data_init_service, method):
    bot_service.get_bot.return_value = {**PERSONAL_BOT, "bot_type": "service"}

    resp = client.request(method.upper(), "/openapi/v1/bots/b1/data-init", json={} if method == "post" else None)

    assert resp.status_code == 409
    data_init_service.trigger_init.assert_not_awaited()
    data_init_service.get_status.assert_not_called()


@pytest.mark.parametrize("method", ["get", "post"])
def test_data_init_missing_for_owner_is_not_found(client, bot_service, method):
    bot_service.get_bot.side_effect = BotNotFoundError("bot not found: bX")

    resp = client.request(method.upper(), "/openapi/v1/bots/bX/data-init", json={} if method == "post" else None)

    assert resp.status_code == 404
    assert resp.json()["message"] == "Not found"
