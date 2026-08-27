"""Caller identity OpenAPI handler and lock hand-off coverage."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.caller_identity.router import (
    router,
    update_mcp_call_type,
)
from agentclaw.community.adapters.http.openapi_v1.caller_identity.schemas import (
    McpCallTypeUpdate,
)
from agentclaw.community.api.caller_identity_service import (
    McpCallType,
    McpCallTypeUpdateResult,
)


def _request() -> Request:
    request = Request({"type": "http", "method": "PATCH", "path": "/"})
    request.state.trace_id = "trace-caller"
    return request


def test_public_contract_replaces_legacy_ctoken_entity_and_lock_parameters():
    app = FastAPI()
    app.include_router(router)
    document = app.openapi()

    read = document["paths"]["/openapi/v1/bots/{bot_id}/caller-context"]["get"]
    write = document["paths"]["/openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type"][
        "patch"
    ]
    read_params = {item["name"] for item in read["parameters"]}
    write_params = {item["name"] for item in write["parameters"]}
    body_ref = write["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    body_name = body_ref.rsplit("/", 1)[-1]
    body_fields = set(document["components"]["schemas"][body_name]["properties"])

    assert {"bot_id", "user_id", "owner_id", "stage", "publish_id"} <= read_params
    assert {"bot_id", "server_code", "user_id", "owner_id"} <= write_params
    assert "ctoken" not in read_params | write_params
    assert "entity_id" not in read_params | write_params
    assert body_fields == {"call_type"}
    assert "423" in write["responses"]


@pytest.mark.asyncio
async def test_update_passes_the_server_resolved_lock_epoch_to_the_service():
    service = AsyncMock()
    service.update_mcp_call_type.return_value = McpCallTypeUpdateResult(
        server_code="mcp.weather",
        call_type=McpCallType.CALLER,
        bot_call_type=McpCallType.CALLER,
    )
    locks = Mock()
    locks.get_lock_info.return_value = SimpleNamespace(
        lock=SimpleNamespace(id=37, holder_user_id="owner")
    )

    response = await update_mcp_call_type(
        "bot-1",
        "mcp.weather",
        McpCallTypeUpdate(call_type=McpCallType.CALLER),
        _request(),
        "owner",
        "owner",
        service,
        locks,
    )

    assert response.data.call_type.value == McpCallType.CALLER.value
    service.update_mcp_call_type.assert_awaited_once_with(
        bot_id="bot-1",
        server_code="mcp.weather",
        call_type=McpCallType.CALLER,
        actor_id="owner",
        lock_epoch=37,
        entity_id="owner",
    )


@pytest.mark.asyncio
async def test_update_does_not_forward_another_users_lock_epoch():
    service = AsyncMock()
    service.update_mcp_call_type.return_value = McpCallTypeUpdateResult(
        server_code="mcp.weather",
        call_type=McpCallType.OWNER,
        bot_call_type=McpCallType.OWNER,
    )
    locks = Mock()
    locks.get_lock_info.return_value = SimpleNamespace(
        lock=SimpleNamespace(id=37, holder_user_id="another-user")
    )

    await update_mcp_call_type(
        "bot-1",
        "mcp.weather",
        McpCallTypeUpdate(call_type=McpCallType.OWNER),
        _request(),
        "owner",
        "owner",
        service,
        locks,
    )

    assert service.update_mcp_call_type.await_args.kwargs["lock_epoch"] is None
