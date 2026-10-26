"""Endpoint tests for public Bot inventory routes."""
from __future__ import annotations

from unittest.mock import MagicMock
from datetime import datetime
import json
from pathlib import Path

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
from agentclaw.community.core.bot_inventory.adapters.service_lifecycle import (
    ServiceLifecycleView,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
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
    "template_type": "applicationCoding",
}


class _TemplatePortStub:
    """Hands one stored snapshot back — secrets included — so the handler test
    pins the verbatim passthrough end to end."""

    def list_template_configs_by_bot_ids(self, bot_ids):
        return {
            "c1": {
                "devflow_workflow": "release-notes",
                "token": "echoed-to-owner",
                "bot_template_config": {"ext_config": {"thetaKey": "enc:v1:x"}},
                "runtime": "codefuse",
            }
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
def publication_repo(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.bot_inventory.adapters.service_lifecycle.get_current_env",
        lambda: "dev",
    )
    repo = MagicMock()
    repo.list_by_source_bots.return_value = []
    return repo


@pytest.fixture
def client(bot_service, desktop_service, publication_repo):
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
                lifecycle_view=BotLifecycleView(ServiceLifecycleView(publication_repo)),
                edit_lock_view=MagicMock(states_for_bots=MagicMock(return_value={})),
                template_port=_TemplatePortStub(),
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
    by_id = {item["bot_id"]: item for item in data["items"]}
    assert by_id["c1"]["status"] == "ACTIVE"
    assert by_id["l1"]["status"] == "OFFLINE"
    assert all(item["publish"] is None for item in data["items"])


def test_list_inventory_carries_template_snapshot_verbatim(client):
    data = _ok(client.get("/openapi/v1/bots/all"))

    by_id = {item["bot_id"]: item for item in data["items"]}
    cloud = by_id["c1"]
    # The stored snapshot reaches the wire verbatim — the passthrough decision
    # (2026-09-01): an owner-scoped face echoes the caller's own creation
    # input, secrets included, with no allowlist filtering.
    assert cloud["template_type"] == "applicationCoding"
    assert cloud["template_config"] == {
        "devflow_workflow": "release-notes",
        "token": "echoed-to-owner",
        "bot_template_config": {"ext_config": {"thetaKey": "enc:v1:x"}},
        "runtime": "codefuse",
    }
    # Desktop rows carry no template identity.
    assert by_id["l1"]["template_type"] is None
    assert by_id["l1"]["template_config"] is None


def test_inventory_openapi_declares_upgrade_action(client):
    schema = client.app.openapi()["components"]["schemas"]["BotInventoryItem"]

    assert "upgrade" in schema["properties"]["actions"]["items"]["enum"]


def _publication(record_id, status, version):
    return BotPublishRecord(
        id=record_id, source_bot_pk=1, source_bot_id="c1",
        publish_bot_id="c1", name="Service", owner_id="u1",
        permission_owner="u1", env="dev", status=status.value, version=version,
        last_pub_id=0, gmt_create=datetime(2026, 10, 15),
        gmt_modified=datetime(2026, 10, 15),
    )


@pytest.mark.parametrize("bot_status", ["PENDING", "PROVISIONING", "ACTIVE", "FAILED"])
@pytest.mark.parametrize(
    "publish_status,display_state",
    [
        (PublishStatus.DRAFT, "service_draft"),
        (PublishStatus.FAILED, "service_deploying"),
        (PublishStatus.VALIDATING, "service_prestable"),
        (PublishStatus.SUCCESS, "service_online"),
        (PublishStatus.RELEASED, "service_offline"),
    ],
)
def test_service_status_is_bot_status_with_separate_publication(
    client, bot_service, publication_repo, bot_status, publish_status, display_state,
):
    bot_service.list_bots_by_conditions.return_value = {
        "total": 1, "items": [{**CLOUD, "bot_type": "service", "status": bot_status}],
    }
    publication_repo.list_by_source_bots.return_value = [_publication(10, publish_status, 1)]
    data = _ok(client.get("/openapi/v1/bots/all", params={"is_service": "true"}))
    item, = data["items"]
    assert item["status"] == bot_status
    assert item["publish"] == {"id": 10, "status": publish_status.value, "version": 1}
    assert item["display_state"] == display_state
    assert item["internal_status"] == publish_status.value
    assert item["publication_id"] == 10
    assert item["publication_version"] == 1
    publication_repo.list_by_source_bots.assert_called_once()
    publication_repo.list_by_source_bot.assert_not_called()


def test_service_cards_keep_their_own_publication(client, bot_service, publication_repo):
    bot_service.list_bots_by_conditions.return_value = {
        "total": 1, "items": [{**CLOUD, "bot_type": "service"}],
    }
    publication_repo.list_by_source_bots.return_value = [
        _publication(10, PublishStatus.SUCCESS, 1),
        _publication(11, PublishStatus.DRAFT, 2),
    ]
    data = _ok(client.get("/openapi/v1/bots/all", params={"is_service": "true"}))
    assert data["total"] == 2
    cards = {item["publication_id"]: item for item in data["items"]}
    assert cards[10]["publish"] == {"id": 10, "status": PublishStatus.SUCCESS.value, "version": 1}
    assert cards[11]["publish"] == {"id": 11, "status": PublishStatus.DRAFT.value, "version": 2}
    assert {item["status"] for item in cards.values()} == {"ACTIVE"}
    assert cards[10]["card_id"] != cards[11]["card_id"]
    assert "offline" in cards[10]["actions"]
    assert "publish_staging" in cards[11]["actions"]
    publication_repo.list_by_source_bots.assert_called_once()


def test_service_without_publication_does_not_fabricate_publish(client, bot_service):
    bot_service.list_bots_by_conditions.return_value = {
        "total": 1, "items": [{**CLOUD, "bot_type": "service", "status": "PROVISIONING"}],
    }
    data = _ok(client.get("/openapi/v1/bots/all", params={"is_service": "true"}))
    item, = data["items"]
    assert item["status"] == "PROVISIONING"
    assert item["publish"] is None
    assert item["publication_id"] is None
    assert item["actions"] == ["view"]


def test_inventory_publication_schema_is_bounded_and_nullable(client):
    schemas = client.app.openapi()["components"]["schemas"]
    assert set(schemas["InventoryPublication"]["properties"]) == {"id", "status", "version"}
    assert {"type": "null"} in schemas["BotInventoryItem"]["properties"]["publish"]["anyOf"]


def test_inventory_openapi_declares_restart_publish_action(client):
    schema = client.app.openapi()["components"]["schemas"]["BotInventoryItem"]

    assert "restart_publish" in schema["properties"]["actions"]["items"]["enum"]


def test_inventory_openapi_reuses_edit_lock_contract(client):
    schemas = client.app.openapi()["components"]["schemas"]

    edit_lock = schemas["BotInventoryItem"]["properties"]["edit_lock"]
    assert {item.get("$ref") for item in edit_lock["anyOf"]} == {
        "#/components/schemas/EditLock",
        None,
    }


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


@pytest.mark.parametrize("stored_ext", [
    {"avatar_url": "avatar", "expert_origin": {"expert_name": "产品经理", "source_commit": "private-commit", "content_digest": "private-digest"}},
    '{"expert_origin":{"expert_name":"产品经理","source_commit":"private-commit"}}',
])
@pytest.mark.parametrize("bot_type", ["personal", "service"])
def test_inventory_http_projects_only_expert_name(client, bot_service, stored_ext, bot_type):
    bot_service.list_bots_by_conditions.return_value = {"total": 1, "items": [{**CLOUD, "ext": stored_ext, "bot_type": bot_type}]}
    data = _ok(client.get("/openapi/v1/bots/all"))
    by_id = {item["bot_id"]: item for item in data["items"]}
    assert by_id["c1"]["expert_name"] == "产品经理"
    assert by_id["l1"]["expert_name"] is None
    assert "expert_origin" not in str(data)
    assert "private-commit" not in str(data)
    assert "private-digest" not in str(data)
    schema = client.app.openapi()["components"]["schemas"]["BotInventoryItem"]
    assert "expert_name" not in schema["required"]
    assert schema["properties"]["expert_name"]["readOnly"] is True
    public_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "apps/gateway/configs/schemas/bots.openapi.json").exists())
    published = json.loads((public_root / "apps/gateway/configs/schemas/bots.openapi.json").read_text())
    assert published["components"]["schemas"]["BotInventoryItem"]["properties"]["expert_name"] == schema["properties"]["expert_name"]
