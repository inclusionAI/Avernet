"""Orchestration consumer tests using only the injected capability contract."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    BotSessionError,
    SessionState,
)
from secbaas.community.core.service.bot_run._async_chat_client_transport import (
    ChatTransport,
)
from secbaas.community.core.service.bot_run._chat_errors import ChatRequestError
from secbaas.community.core.service.bot_run._interaction_protocol import (
    EngineInteractionResolveExchange,
)


@pytest.fixture
def facade():
    # spec_set prohibits accidental raw-client, fallback or A2A policy access.
    transport = MagicMock(spec_set=ChatTransport)
    transport.uri = "ws://example.test/ws"
    transport.is_connected = True
    transport.transport_name = "test-capability"
    transport.has_active_sessions.return_value = False
    transport.tracks_session.return_value = False
    transport.can_resolve_interaction.return_value = True
    transport.connect = AsyncMock(return_value={"server": {"host": "fake"}})
    transport.reconnect = AsyncMock(return_value={})
    transport.close = AsyncMock()
    transport.inject = AsyncMock(return_value={"ok": True})
    transport.abort = AsyncMock(return_value={"ok": True})
    transport.send = AsyncMock(return_value={"ok": True})
    client = AsyncChatClient(transport=transport, max_retries=0)
    callbacks = transport.bind_events.call_args.args[0]
    return client, transport, callbacks


@pytest.mark.asyncio
async def test_facade_sends_via_capability_and_consumes_bound_events(facade):
    client, transport, callbacks = facade

    async def send(**kwargs):
        callbacks.events["chat"](
            {
                "sessionKey": kwargs["session_key"],
                "state": "final",
                "message": {"content": [{"text": "answer"}]},
            }
        )
        return {"ok": True}

    transport.send.side_effect = send
    await client.connect()
    result, _ = await client.send_message("hello", session_key="sk", timeout=1)
    assert result == "answer"
    transport.send.assert_awaited_once_with(
        session_key="sk",
        message="hello",
        auth_token=None,
        app_id=None,
        timeout_ms=1000,
        chat_metadata=None,
        attachments=None,
    )
    await client.inject_message("extra", session_key="sk")
    transport.inject.assert_awaited_once_with(
        session_key="sk",
        message="extra",
        auth_token=None,
        chat_metadata=None,
        attachments=None,
    )
    assert await client.chat_abort("sk", "run") == {"ok": True}
    transport.abort.assert_awaited_once_with(session_key="sk", run_id="run")
    client.refresh_outbound_auth({"x-proxypass-token": "fresh"})
    transport.refresh_outbound_auth.assert_called_once_with(
        {"x-proxypass-token": "fresh"}
    )
    assert client.uri == transport.uri
    assert client.transport_name == "test-capability"
    await client.close()
    transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_facade_translates_rejection_without_transport_internals(facade):
    client, transport, _ = facade
    transport.send.return_value = {
        "ok": False,
        "error": {"code": "DENIED", "message": "rejected"},
    }
    with pytest.raises(ChatRequestError, match="DENIED"):
        await client.send_message("hello", session_key="sk")
    assert not client.has_active_sessions
    assert not client.tracks_session("sk")
    await client.close()


@pytest.mark.asyncio
async def test_scoped_failure_ends_only_supplied_unfinished_sessions(facade):
    client, transport, callbacks = facade
    client._sessions.update({key: SessionState() for key in ("owned", "main", "done")})
    client._sessions["done"].state = "final"
    client._sessions["done"].chat_complete.set()
    callbacks.on_sessions_failed(
        ("owned", "done", "unknown"), "run outcome unknown", "secondary"
    )
    assert client._sessions["owned"].state == "error"
    assert client._sessions["owned"].chat_complete.is_set()
    assert client._sessions["done"].state == "final"
    assert not client._sessions["main"].chat_complete.is_set()
    assert not client._disconnect_event.is_set()
    transport.reconnect.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_scoped_failure_wakes_waiter_through_callback(facade):
    client, transport, callbacks = facade

    async def send(**kwargs):
        callbacks.on_sessions_failed(
            (kwargs["session_key"],), "run outcome unknown", "secondary"
        )
        return {"ok": True}

    transport.send.side_effect = send
    with pytest.raises(BotSessionError, match="run outcome unknown"):
        await asyncio.wait_for(client.send_message("hello", session_key="sk"), 1)
    transport.send.assert_awaited_once()
    await client.close()


@pytest.mark.asyncio
async def test_stream_uses_transport_events_not_wire_frames(facade):
    client, transport, callbacks = facade

    async def send(**kwargs):
        callbacks.events["chat"](
            {"sessionKey": "sk", "state": "error", "errorMessage": "broken"}
        )
        return {"ok": True}

    transport.send.side_effect = send
    chunks = [
        chunk
        async for chunk in client.send_message_stream(
            "hello", session_key="sk", timeout=1
        )
    ]
    assert len(chunks) == 1
    assert chunks[0].type == "error"
    assert "broken" in chunks[0].content
    transport.send.assert_awaited_once()
    await client.close()


@pytest.mark.asyncio
async def test_reconnect_calls_operation_without_closing_transport(facade):
    client, transport, _ = facade
    transport.is_connected = False
    client._max_retries = 1
    client._retry_base_backoff = 0

    async def reconnect():
        transport.is_connected = True
        # Complete one monitor iteration deterministically.
        client._closed_intentionally = True
        return {}

    transport.reconnect.side_effect = reconnect
    await asyncio.wait_for(client._reconnect_loop(), 1)
    transport.reconnect.assert_awaited_once()
    transport.close.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_interaction_dispatch_uses_session_uplink_not_main_health(facade):
    client, transport, callbacks = facade
    transport.is_connected = False
    service = MagicMock()
    command = service.claim_for_dispatch.return_value
    command.interaction_id = "interaction-1"
    command.resolution.kind = "exec"
    client._interaction_service = service
    transport.resolve_interaction = AsyncMock(
        return_value=EngineInteractionResolveExchange(
            request={}, response={}, accepted=True, error_message=None
        )
    )
    callbacks.events["interaction.requested"](
        {"sessionKey": "secondary", "interactionId": "interaction-1"}
    )
    transport.can_resolve_interaction.assert_called_once_with("secondary")
    await asyncio.wait_for(client._interaction_tasks[("secondary", "interaction-1")], 1)
    transport.resolve_interaction.assert_awaited_once_with(
        session_key="secondary",
        interaction_id="interaction-1",
        resolution=command.resolution,
    )
    service.record_engine_exchange.assert_called_once()
    await client.close()
