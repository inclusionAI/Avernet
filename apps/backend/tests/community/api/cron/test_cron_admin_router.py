"""Real HTTP parsing/auth plus real admin policy, with isolated runtime relay."""
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.cron import router
from agentclaw.community.api.cron_admin_service import CronAdminServiceProtocol
from agentclaw.community.core.cron.services.cron_admin import CronAdminService

URL = "/api/cron/admin/bots/b1/tasks/t1/disable"
BODY = {"runtime_stage": "online", "reason": "incident"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.cron.services.cron_admin.cron_admin",
        lambda: frozenset({"admin"}),
    )
    bots = Mock()
    bots.get_unique_by_id.return_value = {"bot_type": "service", "owner_id": "owner"}
    relay = Mock()
    relay.update_cron = AsyncMock(return_value={"success": True, "data": {"enabled": False}})
    relay.list_all_crons = AsyncMock(return_value={"success": True, "data": [{"task_id": "t1"}]})
    service = CronAdminService(bots, relay)
    app = FastAPI()
    app.include_router(router)
    injector = Injector()
    injector.binder.bind(CronAdminServiceProtocol, to=service)
    attach_injector(app, injector)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id="admin", staffId="admin", operatorName="Admin")
    with TestClient(app) as client:
        yield client, relay


def test_disable_http_contract(client):
    http, relay = client
    response = http.post(URL, json=BODY)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert relay.update_cron.await_args.kwargs["user_id"] == "owner"
    assert relay.update_cron.await_args.kwargs["body"] == {"enabled": False}


@pytest.mark.parametrize("body", [
    {"runtime_stage": "draft", "reason": "incident"},
    {"reason": "incident"}, {"runtime_stage": "online"},
    BODY | {"reason": " "}, BODY | {"enabled": True}, BODY | {"owner_id": "spoof"},
])
def test_invalid_or_expanded_body_is_rejected(client, body):
    http, relay = client
    assert http.post(URL, json=body).status_code == 422
    relay.update_cron.assert_not_awaited()


def test_list_is_mounted_with_explicit_stage(client):
    http, relay = client
    response = http.get("/api/cron/admin/bots/b1/tasks", params={"runtime_stage": "online"})
    assert response.json()["data"] == [{"task_id": "t1"}]
    relay.list_all_crons.assert_awaited_once()
    relay.update_cron.assert_not_awaited()
    assert http.get("/api/cron/admin/bots/b1/tasks").status_code == 422


def test_unauthorized_http_caller_is_denied(client):
    http, relay = client
    http.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id="owner", staffId="owner", operatorName="Owner")
    result = http.post(URL, json=BODY).json()
    assert result["success"] is False and result["error_code"] == 403
    relay.update_cron.assert_not_awaited()


def test_unexpected_failure_returns_error_not_success(client):
    http, relay = client
    relay.update_cron.side_effect = RuntimeError("private failure details")
    result = http.post(URL, json=BODY).json()
    assert result["success"] is False and result["error_code"] == 500
    assert "private failure" not in result["message"]


def test_main_app_and_production_di_register_admin_service():
    from agentclaw.community.adapters.http.app import app
    from agentclaw.community.di import DeployProfile, build_injector

    injector = build_injector(profile=DeployProfile.TEST)
    assert isinstance(injector.get(CronAdminServiceProtocol), CronAdminService)
    paths = app.openapi()["paths"]
    assert "/api/cron/admin/bots/{bot_id}/tasks/{task_id}/disable" in paths
    assert "/api/cron/admin/bots/{bot_id}/tasks" in paths
