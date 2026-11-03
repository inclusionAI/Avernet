"""Transport conformance without a facade or mutable conversation state."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.bootstrap._chat_transport import build_chat_transport
from secbaas.community.core.service.bot_run._async_chat_client_transport import (
    ChatTransportCallbacks,
)
from secbaas.community.core.service.bot_run._bot_a2a_client import A2aUnavailableError
from secbaas.community.core.service.bot_run._chat_errors import ChatRequestError

_WS = "secbaas.community.core.service.bot_run._async_chat_client_ws"
_A2A = "secbaas.community.core.service.bot_run._async_chat_client_a2a"


class _Wire:
    def __init__(self, **kwargs):
        self.callbacks = {}
        self.disconnect_callback = None
        self.connected = False
        self.server_info = {"server": {"host": "stub"}}
        self.connect = AsyncMock(side_effect=self._connect)
        self.close = AsyncMock(side_effect=self._close)
        self.chat_send = AsyncMock(return_value={"ok": True})
        self.chat_abort = AsyncMock(return_value={"ok": True})
        self.has_running_runs = False

    def on_event(self, event, handler):
        self.callbacks[event] = handler

    def on_disconnect(self, handler):
        self.disconnect_callback = handler

    async def _connect(self):
        self.connected = True
        return self.server_info

    async def _close(self):
        self.connected = False
        self.disconnect_callback("disconnect", {})


def _assemble(monkeypatch, mode="on"):
    main, fallback = _Wire(), _Wire()
    monkeypatch.setattr(f"{_A2A}.BotA2aClient", lambda **_: main)
    monkeypatch.setattr(f"{_WS}.BotWebSocketClient", lambda **_: fallback)
    events = {name: MagicMock() for name in ("chat", "agent", "error", "*")}
    callbacks = ChatTransportCallbacks(
        events=events, on_disconnect=MagicMock(), on_sessions_failed=MagicMock()
    )
    transport = build_chat_transport(uri="ws://example.test/ws", mode=mode)
    transport.bind_events(callbacks)
    return transport, main if mode != "off" else fallback, fallback, callbacks


async def _send(transport, key="owned"):
    return await transport.send(
        session_key=key,
        message="message",
        auth_token=None,
        app_id=None,
        timeout_ms=None,
        chat_metadata=None,
        attachments=[{"type": "image"}],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "on"])
@pytest.mark.parametrize(
    "failure", [RuntimeError("handshake failed"), asyncio.CancelledError()]
)
async def test_failed_main_handshake_disposes_candidate(monkeypatch, mode, failure):
    transport, main, _, callbacks = _assemble(monkeypatch, mode)
    main.connect.side_effect = failure
    with pytest.raises(type(failure)):
        await transport.connect()
    main.close.assert_awaited_once()
    assert not transport.is_connected
    assert transport.transport_name == "unknown"
    callbacks.on_disconnect.assert_not_called()
    await transport.close()
    main.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("handshake failed"), asyncio.CancelledError()]
)
async def test_failed_fallback_handshake_disposes_only_candidate(monkeypatch, failure):
    transport, main, fallback, callbacks = _assemble(monkeypatch)
    await transport.connect()
    fallback.connect.side_effect = failure
    with pytest.raises(type(failure)):
        await _send(transport)
    fallback.close.assert_awaited_once()
    main.close.assert_not_awaited()
    assert transport.is_connected
    assert not transport.has_active_sessions()
    callbacks.on_disconnect.assert_not_called()
    callbacks.on_sessions_failed.assert_not_called()
    await transport.close()


@pytest.mark.asyncio
async def test_fallback_disconnect_only_notifies_owned_sessions(monkeypatch):
    transport, main, fallback, callbacks = _assemble(monkeypatch)
    await transport.connect()
    await _send(transport)
    assert transport.tracks_session("owned")
    fallback.disconnect_callback("disconnect", {})
    assert not transport.tracks_session("owned")
    assert transport.is_connected
    callbacks.on_sessions_failed.assert_called_once_with(
        ("owned",),
        "fallback connection lost; run outcome unknown",
        "fallback_disconnect",
    )
    callbacks.on_disconnect.assert_not_called()
    main.close.assert_not_awaited()
    await transport.close()


@pytest.mark.asyncio
async def test_reconnect_replaces_main_but_preserves_fallback_run(monkeypatch):
    transport, main, fallback, callbacks = _assemble(monkeypatch, "grey")
    await transport.connect()
    await _send(transport)
    replacement = _Wire()
    monkeypatch.setattr(
        f"{_A2A}.BotA2aClient", MagicMock(side_effect=A2aUnavailableError("no card"))
    )
    monkeypatch.setattr(f"{_WS}.BotWebSocketClient", lambda **_: replacement)
    await transport.reconnect()
    main.close.assert_awaited_once()
    assert transport.transport_name == "v3-ws"
    assert transport.tracks_session("owned")
    assert transport.has_active_sessions()
    fallback.close.assert_not_awaited()
    callbacks.on_disconnect.assert_not_called()
    await transport.abort(session_key="owned", run_id="run")
    fallback.chat_abort.assert_awaited_once_with(session_key="owned", run_id="run")
    replacement.chat_abort.assert_not_awaited()
    await transport.close()
    replacement.close.assert_awaited_once()
    fallback.close.assert_awaited_once()
    assert not transport.has_active_sessions()
    assert not transport.tracks_session("owned")
    callbacks.on_sessions_failed.assert_not_called()


@pytest.mark.asyncio
async def test_full_close_disposes_fallback_even_if_main_close_fails(monkeypatch):
    transport, main, fallback, _ = _assemble(monkeypatch)
    await transport.connect()
    await _send(transport)
    main.close.side_effect = RuntimeError("close failed")
    with pytest.raises(RuntimeError, match="close failed"):
        await transport.close()
    fallback.close.assert_awaited_once()
    assert not transport.is_connected
    assert not transport.tracks_session("owned")


@pytest.mark.asyncio
async def test_retired_fallback_cannot_end_a_new_connection_run(monkeypatch):
    transport, _, old, callbacks = _assemble(monkeypatch)
    await transport.connect()
    await _send(transport)
    old.disconnect_callback("disconnect", {})
    replacement = _Wire()
    monkeypatch.setattr(f"{_WS}.BotWebSocketClient", lambda **_: replacement)
    await _send(transport)
    payload = {"sessionKey": "owned", "state": "final"}
    old.callbacks["chat"](payload)
    old.disconnect_callback("disconnect", {})
    assert transport.tracks_session("owned")
    callbacks.events["chat"].assert_not_called()
    callbacks.on_sessions_failed.assert_called_once()
    replacement.callbacks["chat"](payload)
    assert not transport.tracks_session("owned")
    callbacks.events["chat"].assert_called_once_with(payload)
    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("ACK uncertain"),
        asyncio.CancelledError(),
        ChatRequestError("rejected"),
    ],
)
async def test_only_explicit_rejection_releases_run_ownership(monkeypatch, failure):
    transport, _, fallback, _ = _assemble(monkeypatch)
    await transport.connect()
    fallback.chat_send.side_effect = failure
    with pytest.raises(type(failure)):
        await _send(transport)
    assert transport.tracks_session("owned") is (
        not isinstance(failure, ChatRequestError)
    )
    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "on"])
async def test_event_binding_is_required_and_snapshots_registry(monkeypatch, mode):
    transport, wire, _, callbacks = _assemble(monkeypatch, mode)
    unbound = build_chat_transport(uri="ws://example.test/ws", mode=mode)
    with pytest.raises(RuntimeError, match="must be bound"):
        await unbound.connect()
    assert not wire.connected
    original = callbacks.events["chat"]
    callbacks.events["chat"] = MagicMock()
    await transport.connect()
    with pytest.raises(RuntimeError, match="before connecting"):
        transport.bind_events(callbacks)
    wire.callbacks["chat"]({"sessionKey": "sk", "state": "delta"})
    original.assert_called_once()
    callbacks.events["chat"].assert_not_called()
    await transport.close()


@pytest.mark.parametrize("mode", ["off", "on"])
def test_transport_owns_a_copy_of_headers(mode):
    headers = {"x-proxypass-token": "initial"}
    transport = build_chat_transport(
        uri="ws://example.test/ws", mode=mode, headers=headers
    )
    headers["x-proxypass-token"] = "caller-edit"
    assert transport.headers["x-proxypass-token"] == "initial"
    transport.refresh_outbound_auth({"x-proxypass-token": "refreshed"})
    transport.refresh_outbound_auth({"x-proxypass-token": ""})
    assert transport.headers["x-proxypass-token"] == "refreshed"
    assert headers["x-proxypass-token"] == "caller-edit"


def test_bootstrap_injects_factory_as_callable_not_a_built_transport():
    from secbaas.community.bootstrap._core_services import CoreServiceContainer

    provider = CoreServiceContainer.chat_client_pool.kwargs["transport_factory"]
    assert provider() is build_chat_transport


@pytest.mark.asyncio
async def test_interaction_uplink_availability_is_scoped_to_session(monkeypatch):
    transport, main, fallback, _ = _assemble(monkeypatch)
    assert not transport.can_resolve_interaction("main")
    await transport.connect()
    assert transport.can_resolve_interaction("main")
    await _send(transport)
    main.connected = False
    assert not transport.is_connected
    assert not transport.can_resolve_interaction("main")
    assert transport.can_resolve_interaction("owned")
    fallback.connected = False
    assert not transport.can_resolve_interaction("owned")
    await transport.close()
