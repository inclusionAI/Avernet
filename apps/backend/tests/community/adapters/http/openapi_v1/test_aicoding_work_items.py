"""Public OpenAPI adapter tests for the Agent Coding work-items snapshot."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.api.work_items_service import (
    AICodingWorkItemsServiceProtocol,
)
from agentclaw.community.adapters.http.openapi_v1.aicoding.work_items import (
    router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.core.aicoding.work_item_bots import (
    WorkItemsBotTarget,
)
from agentclaw.community.core.aicoding.services.work_items_service import (
    AICodingWorkItemsService,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
)


def _target() -> WorkItemsBotTarget:
    return WorkItemsBotTarget(bot_id="bot-1", bot_name="Bot 1", owner_id="315629")


def _resolver() -> MagicMock:
    context = MagicMock()
    context.provider = "baas"
    context.conn_info = {"type": "baas"}
    resolver = MagicMock(spec=DeviceContextResolver)
    resolver.resolve_for_bot.return_value = context
    return resolver


def _transport(response: dict) -> MagicMock:
    transport = MagicMock(spec=DeviceAdapterTransport)
    transport.invoke = AsyncMock(return_value=response)
    return transport


def _build_client(*, principal: dict, response: dict, targets=None) -> TestClient:
    lister = MagicMock()
    lister.list_bot_targets.return_value = targets or []
    resolver = _resolver()
    transport = _transport(response)
    app = FastAPI()
    app.include_router(router)

    class _Module(Module):
        def configure(self, binder):
            binder.bind(
                AICodingWorkItemsServiceProtocol,
                to=AICodingWorkItemsService(lister, resolver, transport),
            )

    attach_injector(app, Injector([_Module()]))
    app.dependency_overrides[require_principal] = lambda: principal
    mount_public_error_handlers(app)
    return TestClient(app)


def test_public_route_uses_signed_user_and_standard_envelope():
    target = _target()
    response_payload = {
        "success": True,
        "data": [
            {
                "id": "user:315629:s1:agent:bot-1",
                "user_id": "315629",
                "title": "s1",
                "gmt_modified": "1794000000000Z",
                "run_status": "running",
            }
        ],
    }
    client = _build_client(
        principal={"user_id": "315629"},
        response=response_payload,
        targets=[target],
    )

    response = client.get(
        "/openapi/v1/bots/aicoding/work-items",
        params={
            "source": "mine",
            "since_ms": 1_794_000_000_000,
            "run_status": "non_idle",
            "limit": 50,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200000
    assert payload["message"] == "OK"
    assert payload["data"]["items"][0]["session_key"] == ("user:315629:s1:agent:bot-1")
    assert payload["data"]["bots"][0]["status"] == "ok"
    assert payload["data"]["scan_limit"] == 50
    assert payload["data"]["filters"] == {
        "source": ["mine"],
        "since_ms": 1_794_000_000_000,
        "run_status": ["non_idle"],
    }


def test_public_route_defaults_are_optional():
    client = _build_client(
        principal={"user_id": "315629"},
        response={"success": True, "data": []},
        targets=[_target()],
    )

    response = client.get("/openapi/v1/bots/aicoding/work-items")

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200000
    assert payload["data"]["filters"] == {
        "source": None,
        "since_ms": None,
        "run_status": None,
    }
    assert payload["data"]["scan_limit"] == 50


def test_public_route_refuses_an_app_only_caller():
    client = _build_client(
        principal={"app_id": 42},
        response={"success": True, "data": []},
        targets=[],
    )

    response = client.get("/openapi/v1/bots/aicoding/work-items")

    assert response.status_code == 401
    assert response.json()["code"] == 401000
    assert response.json()["data"] is None
