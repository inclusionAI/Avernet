"""Coverage for api/routers/ws.py _handle: reject (4001) + accept (delegate)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.community.api.routers.ws import _handle, openclaw_ws


class _FakeWS:
    def __init__(self):
        self.accepted = False
        self.closed_with = None
    async def accept(self): self.accepted = True
    async def close(self, *, code, reason): self.closed_with = (code, reason)


@pytest.mark.asyncio
async def test_openclaw_ws_forwards_materialization_service():
    ws = MagicMock()
    auth = MagicMock()
    materialization_service = MagicMock()

    with patch(
        "engine.community.api.routers.ws._handle",
        new_callable=AsyncMock,
    ) as handle_mock:
        await openclaw_ws(ws, auth, materialization_service)

    handle_mock.assert_awaited_once_with(
        ws,
        "openclaw",
        auth,
        materialization_service,
    )


@pytest.mark.asyncio
async def test_handle_rejects_when_engine_not_active():
    ws = _FakeWS()
    manager = MagicMock()
    manager.engine = "openclaw"  # active is openclaw, asking for claude_code
    auth = MagicMock()
    with patch("engine.community.manager.EngineManager.get_instance", return_value=manager):
        await _handle(ws, "claude_code", auth)
    assert ws.accepted is True
    assert ws.closed_with[0] == 4001
    assert "claude_code" in ws.closed_with[1]


@pytest.mark.asyncio
async def test_handle_delegates_when_engine_active():
    ws = _FakeWS()
    manager = MagicMock()
    manager.engine = "claude_code"  # active matches
    auth = MagicMock()
    materialization_service = MagicMock()
    fake_server = MagicMock()
    fake_server.handle_connection = AsyncMock()
    with patch("engine.community.manager.EngineManager.get_instance", return_value=manager), \
         patch(
             "engine.community.api.routers.ws.get_server",
             return_value=fake_server,
         ) as get_server_mock:
        await _handle(ws, "claude_code", auth, materialization_service)
    assert ws.accepted is False  # EngineWebSocketServer.handle_connection owns accept
    fake_server.handle_connection.assert_awaited_once()
    # verify delegate passes auth + ws through
    args, kwargs = fake_server.handle_connection.call_args
    assert kwargs.get("auth_gate_service") is auth
    get_server_mock.assert_called_once_with(materialization_service)
