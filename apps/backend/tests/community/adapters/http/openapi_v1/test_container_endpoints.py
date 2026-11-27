"""Public service-Bot container endpoint contract tests."""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from threading import Event, get_ident
from unittest.mock import Mock

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.containers.router import (
    list_containers,
    restart_container,
)
from agentclaw.community.core.service_bot.errors import (
    ServiceContainerConflictError,
    ServiceContainerUpstreamError,
    ServicePublicationNotFoundError,
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
async def test_list_containers_leaves_event_loop_available_and_copies_context():
    loop = asyncio.get_running_loop()
    loop_thread = get_ident()
    started = asyncio.Event()
    release = Event()
    caller_context = ContextVar("container_test_caller", default="")
    token = caller_context.set("member")

    def slow_list(bot_id: str, *, actor_id: str, owner_id: str) -> dict:
        assert get_ident() != loop_thread
        assert caller_context.get() == actor_id == "member"
        assert owner_id == "owner"
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=5), "event loop did not release the query"
        return {"bot_id": bot_id, "instances": instances()}

    facade = Mock()
    facade.list_containers.side_effect = slow_list
    pending = asyncio.create_task(
        list_containers("bot-1", request(), "member", "owner", facade)
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        # This task can run before the synchronous provider query completes.
        await asyncio.sleep(0)
        assert not pending.done()
    finally:
        release.set()
        caller_context.reset(token)
        response = await asyncio.wait_for(pending, timeout=5)

    assert response.request_id == "trace-1"
    assert response.data.summary.total == 4
    facade.list_containers.assert_called_once_with(
        "bot-1", actor_id="member", owner_id="owner"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (ServicePublicationNotFoundError, 404, "Not found"),
        (
            ServiceContainerConflictError,
            409,
            "Container is not in a valid state for this operation",
        ),
        (ServiceContainerUpstreamError, 502, "Container service error"),
    ],
)
async def test_list_containers_preserves_error_envelopes(error, status, message):
    facade = Mock()
    facade.list_containers.side_effect = error("private provider details")

    response = await list_containers(
        "bot-1", request(), "member", "owner", facade
    )

    assert response.status_code == status
    assert json.loads(response.body) == {
        "code": status * 1000,
        "message": message,
        "data": None,
        "request_id": "trace-1",
    }
    facade.list_containers.assert_called_once_with(
        "bot-1", actor_id="member", owner_id="owner"
    )


@pytest.mark.asyncio
async def test_list_containers_does_not_swallow_unmapped_errors():
    facade = Mock()
    failure = RuntimeError("unexpected provider failure")
    facade.list_containers.side_effect = failure

    with pytest.raises(RuntimeError) as caught:
        await list_containers("bot-1", request(), "member", "owner", facade)

    assert caught.value is failure


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
