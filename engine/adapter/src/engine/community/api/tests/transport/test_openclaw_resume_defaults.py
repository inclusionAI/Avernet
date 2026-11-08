"""Default recovery must not reject or duplicate an otherwise valid chat."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.community.api.tests.transport.test_ws_server import (
    _req, auth_gate_service, fake_engine, server,
)
from engine.community.manager import EngineManager


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disabled", "capacity", "retain_failure"])
async def test_recovery_unavailable_uses_ordinary_chat_once(
    server, fake_engine, auth_gate_service, monkeypatch, mode,
):
    EngineManager.get_instance()._engine = "openclaw"
    auth_gate_service.enabled = False
    server._subscribe_conn_to_session = AsyncMock()
    server._run_ack_chat = AsyncMock()
    server._run_resumable_chat = AsyncMock()
    params = {"sessionKey": "sk", "message": "hello", "idempotencyKey": "one"}
    if mode == "disabled":
        params["resumeEnabled"] = False
    elif mode == "capacity":
        monkeypatch.setattr("engine.community.api.transport.openclaw_resume.MAX_RUNS", 0)
    else:
        fake_engine.on_connection_open.side_effect = RuntimeError("unavailable")
    result = await server._handle_chat_send(
        SimpleNamespace(send_text=AsyncMock()), "conn-1", _req("chat.send", params), params,
        auth_gate_service=auth_gate_service,
    )
    assert result is None
    await asyncio.gather(*server._ack_chat_tasks["conn-1"])
    server._run_ack_chat.assert_awaited_once()
    server._run_resumable_chat.assert_not_awaited()
    assert not server._openclaw_resume._runs
    assert fake_engine.on_connection_open.await_count == (mode == "retain_failure")


@pytest.mark.asyncio
async def test_cancelled_recovery_setup_discards_run_without_sending(
    server, fake_engine, auth_gate_service,
):
    EngineManager.get_instance()._engine = "openclaw"
    auth_gate_service.enabled = False
    fake_engine.on_connection_open.side_effect = asyncio.CancelledError()
    server._run_ack_chat = AsyncMock()
    server._run_resumable_chat = AsyncMock()
    params = {"sessionKey": "sk", "message": "hello"}
    with pytest.raises(asyncio.CancelledError):
        await server._handle_chat_send(
            SimpleNamespace(send_text=AsyncMock()), "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )
    assert not server._openclaw_resume._runs
    server._run_ack_chat.assert_not_awaited()
    server._run_resumable_chat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["hermes", "claude_code", "deepseek_harness", "aicoding", "teclaw"])
@pytest.mark.parametrize("explicit_enable", [False, True])
async def test_other_engine_send_keeps_original_path(
    server, fake_engine, auth_gate_service, engine, explicit_enable,
):
    EngineManager.get_instance()._engine = engine
    auth_gate_service.enabled = False
    server._subscribe_conn_to_session = AsyncMock()
    server._stream_chat_events = AsyncMock()
    params = {"sessionKey": "sk", "message": "hello"}
    if explicit_enable:
        params["resumeEnabled"] = True
    result = await server._handle_chat_send(
        SimpleNamespace(send_text=AsyncMock()), "conn-1", _req("chat.send", params), params,
        auth_gate_service=auth_gate_service,
    )
    assert result.ok and result.payload == {"accepted": True}
    await asyncio.sleep(0)
    server._stream_chat_events.assert_awaited_once()
    fake_engine.on_connection_open.assert_not_awaited()
    assert not server._openclaw_resume._runs
