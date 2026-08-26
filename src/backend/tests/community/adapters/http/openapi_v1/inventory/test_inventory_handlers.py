"""Endpoint tests for public Bot inventory routes."""
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
from agentclaw.community.api.bot_inventory_service import BotInventoryServiceProtocol
from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BotInventoryBotPort,
    BusinessSpaceContextProtocol,
    DesktopBotInventoryPort,
)
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import BotInventoryService
from agentclaw.community.core.bot_inventory.services.lifecycle_view import BotLifecycleView
from agentclaw.community.core.bot_inventory.adapters.noop_service_lifecycle import (
    NoopServiceLifecyclePort,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel


CLOUD = {
    "id": 1,
    "bot_id": "c1",
    "bot_name": "Cloud",
    "bot_desc": "cloud bot",
    "active_engine": "teclaw",
    "bot_type": "personal",
    "status": "ACTIVE",
    "owner_id": "u1",
}
LOCAL = {
    "id": 2,
    "bot_id": "l1",
    "bot_name": "Local",
    "bot_desc": "local bot",
    "active_engine": "openclaw",
    "bot_type": "desktop",
    "status": "OFFLINE",
    "owner_id": "u1",
    "machine_id": "m1",
}


@pytest.fixture
def bot_service():
    svc = MagicMock()
    svc.list_bots_by_conditions.return_value = {"total": 1, "items": [CLOUD]}
    svc.get_bot.return_value = CLOUD
    return svc


@pytest.fixture
def desktop_service():
    svc = MagicMock()
    svc.list_user_bots.return_value = [LOCAL]
    return svc


@pytest.fixture
def client(bot_service, desktop_service):
    class _M(Module):
        def configure(self, binder):
            space = NoopBusinessSpaceContext()
            access = MagicMock()
            access.get_operable_permission_levels.side_effect = lambda **kwargs: {
                int(bot["id"]): PermissionLevel.OWNER for bot in kwargs["bots"]
            }
            inventory = BotInventoryService(
                bot_service=bot_service,
                desktop_service=desktop_service,
                access_service=access,
                business_space=space,
                lifecycle_view=BotLifecycleView(NoopServiceLifecyclePort()),
            )
            binder.bind(BotInventoryBotPort, to=bot_service)
            binder.bind(DesktopBotInventoryPort, to=desktop_service)
            binder.bind(BusinessSpaceContextProtocol, to=space)
            binder.bind(BotInventoryServiceProtocol, to=inventory)

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


def test_list_inventory_combines_personal_cloud_and_local(client):
    data = _ok(client.get("/openapi/v1/bots/all"))

    ids = {item["bot_id"] for item in data["items"]}
    assert data["total"] == 2
    assert ids == {"c1", "l1"}


def test_inventory_openapi_declares_upgrade_action(client):
    schema = client.app.openapi()["components"]["schemas"]["BotInventoryItem"]

    assert "upgrade" in schema["properties"]["actions"]["items"]["enum"]


def test_list_inventory_filters_non_service_bots(client):
    data = _ok(
        client.get("/openapi/v1/bots/all", params={"is_service": "false"})
    )

    assert data["total"] == 2
    assert {item["bot_id"] for item in data["items"]} == {"c1", "l1"}


def test_list_inventory_filters_service_bots_without_leaking_local_bots(client):
    data = _ok(
        client.get("/openapi/v1/bots/all", params={"is_service": "true"})
    )

    assert data["total"] == 0
    assert data["items"] == []


def test_list_inventory_combines_engine_deploy_and_service_filters(client):
    data = _ok(
        client.get(
            "/openapi/v1/bots/all",
            params={
                "engine": "openclaw",
                "deploy_mode": "local",
                "is_service": "false",
            },
        )
    )

    assert data["total"] == 1
    assert [item["bot_id"] for item in data["items"]] == ["l1"]


def test_inventory_consumes_space_header_fail_closed(client):
    resp = client.get("/openapi/v1/bots/all", headers={"X-Space-Id": "team:1"})

    assert resp.status_code == 404
    assert resp.json()["message"] == "Not found"


def test_list_inventory_total_includes_cloud_rows_beyond_first_fetch_window(client, bot_service):
    cloud_rows = [
        {
            **CLOUD,
            "bot_id": f"c{i:03d}",
            "bot_name": f"Cloud {i:03d}",
        }
        for i in range(250)
    ]

    def list_page(**kwargs):
        page = kwargs["page"]
        page_size = kwargs["page_size"]
        start = (page - 1) * page_size
        return {"total": len(cloud_rows), "items": cloud_rows[start : start + page_size]}

    bot_service.list_bots_by_conditions.side_effect = list_page

    data = _ok(client.get("/openapi/v1/bots/all", params={"page": 3, "page_size": 100}))

    assert data["total"] == 251
    ids = {item["bot_id"] for item in data["items"]}
    assert "c249" in ids
    assert "l1" in ids
