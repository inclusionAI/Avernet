"""Public service-Bot container endpoint contract tests."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.containers.router import (
    list_containers,
    restart_container,
)


def request(method: str = "GET") -> Request:
    req = Request({"type": "http", "method": method, "path": "/"})
    req.state.trace_id = "trace-1"
    return req


def instances() -> list[dict]:
    return [
        {
            "device_uuid": "DEVICE-1",
            "status": "ACTIVE",
            "health_status": "ACTIVE",
            "engine_type": "openclaw",
            "provider_type": "arca",
            "provider_device_id": "ARCA-1",
            "gmt_create": "2026-08-18T09:00:00Z",
        },
        {
            "device_uuid": "DEVICE-2",
            "status": "FAILED",
            "health_status": "ABNORMAL",
            "engine_type": "hermes",
        },
        {
            "device_uuid": "DEVICE-3",
            "status": "UPDATING",
            "health_status": "RESTARTING",
            "engine_type": "claude-code",
        },
        {
            "device_uuid": "DEVICE-4",
            "status": "ACTIVE",
            "health_status": "unexpected",
            "engine_type": "openclaw",
        },
    ]


@pytest.mark.asyncio
async def test_list_containers_projects_summary_and_nullable_metrics():
    facade = Mock()
    facade.list_containers.return_value = {
        "bot_id": "bot-1",
        "instances": instances(),
    }

    response = await list_containers(
        "bot-1", request(), "member", "owner", facade
    )

    assert response.request_id == "trace-1"
    assert response.data.summary.model_dump() == {
        "total": 4,
        "healthy": 1,
        "abnormal": 1,
        "restarting": 1,
        "unknown": 1,
    }
    first = response.data.instances[0]
    assert first.id == "DEVICE-1"
    assert first.provider_instance_id == "ARCA-1"
    assert first.node is None and first.cpu is None and first.memory is None
    facade.list_containers.assert_called_once_with(
        "bot-1", actor_id="member", owner_id="owner"
    )


@pytest.mark.asyncio
async def test_restart_container_returns_accepted_operation():
    facade = Mock()
    facade.restart_container.return_value = {
        "bot_id": "bot-1",
        "instance_id": "DEVICE-2",
        "publish_id": 42,
        "accepted": True,
    }

    response = await restart_container(
        "bot-1", "DEVICE-2", request("POST"), "owner", "owner", facade
    )

    assert response.code == 202000
    assert response.data.publish_id == 42
    facade.restart_container.assert_called_once_with(
        "bot-1",
        "DEVICE-2",
        actor_id="owner",
        owner_id="owner",
    )
