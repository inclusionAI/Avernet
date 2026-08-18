"""Endpoint tests for public local Bot routes."""
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
from agentclaw.community.adapters.http.openapi_v1.local.router import router
from agentclaw.community.api.local_bot_workflow_service import LocalBotWorkflowServiceProtocol
from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
    DesktopBotInventoryPort,
)
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.core.bot_inventory.services.local_bot_workflow import LocalBotWorkflowService


LOCAL = {
    "bot_id": "l1",
    "bot_name": "Local",
    "bot_desc": "local bot",
    "active_engine": "openclaw",
    "bot_type": "desktop",
    "status": "ACTIVE",
    "owner_id": "u1",
    "machine_id": "m1",
}


@pytest.fixture
def desktop_service():
    svc = MagicMock()
    svc.list_user_bots.return_value = [LOCAL]
    svc.apply_passport_before_create.return_value = {
        "need_authorization": True,
        "bot_id": "l2",
        "iframe_url": "https://passport/iframe",
        "redirect_url": "https://passport/redirect",
    }
    svc.restart.return_value = {"bot_id": "l1", "status": "PENDING"}
    svc.delete.return_value = {"bot_id": "l1"}
    svc.open_folder.return_value = {"bot_id": "l1"}
    svc.list_devices.return_value = (1, [{"machine_id": "m1", "machine_name": "Mac", "status": "ACTIVE"}])
    svc.list_directory.return_value = {"name": "Desktop", "children": []}
    svc.create_after_authorization.return_value = {**LOCAL, "agent_code": "ac-1"}
    return svc


@pytest.fixture
def passport():
    svc = MagicMock()
    svc.query_auth_status.return_value = {"status": "ISSUED"}
    return svc


@pytest.fixture
def auth_rel():
    return MagicMock()


@pytest.fixture
def client(desktop_service, passport, auth_rel):
    class _M(Module):
        def configure(self, binder):
            space = NoopBusinessSpaceContext()
            workflow = LocalBotWorkflowService(
                desktop_service=desktop_service,
                business_space=space,
                passport_plugin=passport,
                auth_relationship_plugin=auth_rel,
            )
            binder.bind(DesktopBotInventoryPort, to=desktop_service)
            binder.bind(BusinessSpaceContextProtocol, to=space)
            binder.bind(PassportPlugin, to=passport)
            binder.bind(AuthRelationshipPlugin, to=auth_rel)
            binder.bind(LocalBotWorkflowServiceProtocol, to=workflow)

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


def test_list_local_bots(client):
    data = _ok(client.get("/openapi/v1/bots/local"))

    assert data["total"] == 1
    assert data["items"][0]["bot_id"] == "l1"



def test_list_local_devices(client, desktop_service):
    data = _ok(client.get("/openapi/v1/bots/local/devices", params={"page": 1, "page_size": 10}))

    assert data["total"] == 1
    assert data["items"][0]["machine_id"] == "m1"
    assert data["items"][0]["machine_name"] == "Mac"
    assert data["items"][0]["status"] == "ACTIVE"
    desktop_service.list_devices.assert_called_once_with(
        user_id="u1",
        page=1,
        page_size=10,
        status=None,
    )


def test_list_local_device_files(client, desktop_service):
    data = _ok(
        client.get(
            "/openapi/v1/bots/local/devices/m1/files",
            params={"dir": "~/Desktop"},
        )
    )

    assert data == {"name": "Desktop", "children": []}
    desktop_service.list_directory.assert_called_once_with(
        machine_id="m1",
        dir="~/Desktop",
    )

def test_create_local_bot_returns_pending_authorization(client, desktop_service):
    resp = client.post(
        "/openapi/v1/bots/local",
        json={"bot_name": "Local 2", "machine_id": "m1", "engine": "openclaw"},
    )

    body = resp.json()
    assert resp.status_code == 202, body
    assert body["code"] == 202000
    assert body["data"]["bot_id"] == "l2"
    assert desktop_service.apply_passport_before_create.call_args.kwargs["user_id"] == "u1"


def test_create_local_bot_rejects_non_personal_space(client):
    resp = client.post(
        "/openapi/v1/bots/local",
        headers={"X-Space-Id": "team:1"},
        json={"bot_name": "Local 2", "machine_id": "m1", "engine": "openclaw"},
    )

    assert resp.status_code == 404


def test_local_auth_status_completes_creation(client, auth_rel):
    data = _ok(
        client.get(
            "/openapi/v1/bots/l1/local/auth-status",
            params={"bot_name": "Local", "machine_id": "m1", "engine": "openclaw"},
        )
    )

    assert data["status"] == "ISSUED"
    assert data["bot"]["bot_id"] == "l1"
    auth_rel.create_relationship.assert_called_once()


def test_local_auth_status_continues_after_relationship_write_failure(client, auth_rel):
    """A relationship write failure after the bot is created is partial success,
    not an overall 500: the bot is usable and an owner-side reconciler can retry
    the grant. Mirrors the README #560 direction for external-identity writes."""
    auth_rel.create_relationship.side_effect = RuntimeError("relationship write failed")

    data = _ok(
        client.get(
            "/openapi/v1/bots/l1/local/auth-status",
            params={"bot_name": "Local", "machine_id": "m1", "engine": "openclaw"},
        )
    )

    assert data["status"] == "ISSUED"
    assert data["bot"]["bot_id"] == "l1"
    auth_rel.create_relationship.assert_called_once()


def test_restart_and_open_folder_verify_ownership(client, desktop_service):
    assert _ok(client.post("/openapi/v1/bots/l1/local/restart"))["bot_id"] == "l1"
    assert _ok(client.post("/openapi/v1/bots/l1/local/open-folder", json={"folder_path": "src"}))["bot_id"] == "l1"
    assert desktop_service.verify_ownership.call_count == 2
