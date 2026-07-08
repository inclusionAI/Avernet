"""Route-B acceptance: MCP config sync reaches the live singlebox engine.

This is the MCP sample room:
  backend live API
  -> real local BaaS/device allocation
  -> backend DeviceAccessor resolves the engine connection
  -> real DeviceMCPSyncPlugin pushes to the engine /api/mcp API
  -> test reads the engine back and verifies the config changed

MCP Center is fixture-backed in local acceptance because the target here is
the backend-to-singlebox sync path, not the remote MCP catalog service.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.community._flows.mcp.api_lifecycle import MCP_LIVE_SYNC_FLOWS
from tests.community.acceptance._fixtures.live_personal_bot import (
    assert_success as _assert_success,
)
from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
)
from tests.community.acceptance._fixtures.live_personal_bot import (
    fresh_id as _fresh_id,
)
from tests.community.framework.flow import FlowContext
from tests.community.framework.flow_runner_live import run_flow_live


def _engine_headers(connection: dict[str, Any]) -> dict[str, str]:
    token = connection.get("token") or ""
    return {"openclawToken": token} if token else {}


def _engine_get_mcp(
    engine_url: str,
    headers: dict[str, str],
    server_code: str,
) -> dict[str, Any]:
    with httpx.Client(base_url=engine_url, headers=headers, timeout=30.0) as client:
        response = client.get(f"/api/mcp/{server_code}")
    return _assert_success(response)["data"]


def _engine_upsert_mcp(
    engine_url: str,
    headers: dict[str, str],
    *,
    server_code: str,
) -> None:
    body = {
        "server_code": server_code,
        "description": "singlebox acceptance preinstall",
        "transport": "http",
        "url": f"https://singlebox.initial.invalid/{server_code}",
        "headers": {"x-singlebox-token": "initial"},
        "timeout_seconds": 30,
        "enabled": True,
    }
    with httpx.Client(base_url=engine_url, headers=headers, timeout=30.0) as client:
        response = client.post("/api/mcp", json=body)
        if response.status_code == 409:
            update = dict(body)
            update.pop("server_code", None)
            response = client.put(f"/api/mcp/{server_code}", json=update)
    _assert_success(response)


@pytest.mark.acceptance
def test_mcp_user_config_sync_reaches_live_singlebox_engine(
    live_backend,
    acceptance_fs_root,
):
    user_id = _fresh_id("e2e_mcp_user")
    server_code = "mcp.singlebox.acceptance"
    headers = {"x-user-id": user_id}

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=user_id,
            bot_name_prefix="MCP Acceptance",
            bot_desc="MCP live sync acceptance bot",
        )
        bot_id = bot["bot_id"]
        binding_id = bot["binding_id"]

        connection_payload = _assert_success(
            client.get(f"/api/v1/devices/{binding_id}/connection")
        )
        connection = connection_payload["data"]
        assert connection["available"] is True, connection
        assert connection["url"], connection

    engine_url = connection["url"].rstrip("/")
    engine_headers = _engine_headers(connection)
    _engine_upsert_mcp(engine_url, engine_headers, server_code=server_code)
    before = _engine_get_mcp(engine_url, engine_headers, server_code)
    assert before["headers"] == {"x-singlebox-token": "initial"}

    ctx = FlowContext()
    ctx["entity_id"] = user_id
    ctx["bot_id"] = bot_id
    ctx["server_code"] = server_code
    flow = MCP_LIVE_SYNC_FLOWS[0]
    result_ctx = run_flow_live(
        flow,
        base_url=live_backend,
        fs_root=acceptance_fs_root,
        default_headers=headers,
        initial_context=ctx,
    )

    sync_results = result_ctx["sync_results"]
    assert any(
        result.get("bot_id") == bot_id and result.get("synced") is True
        for result in sync_results
    ), sync_results

    after = _engine_get_mcp(engine_url, engine_headers, server_code)
    assert after["headers"] == {"x-singlebox-token": "updated"}
