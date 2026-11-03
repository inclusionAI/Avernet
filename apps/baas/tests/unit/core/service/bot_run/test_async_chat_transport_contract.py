"""Shared transport lifecycle contract and WS/A2A isolation regressions."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    SessionState,
)
from secbaas.community.core.service.bot_run._bot_a2a_client import A2aUnavailableError

from ._async_chat_interaction_support import (
    _claim_service_for,
    _make_exchange,
)

_WS_MODULE = "secbaas.community.core.service.bot_run._async_chat_client_ws"
_A2A_MODULE = "secbaas.community.core.service.bot_run._async_chat_client_a2a"


class _HandshakeClient:
    """Emit a frame inside connect, before it returns to the orchestrator.

    Deliberately expose no low-level `_event_handlers` registry: callback
    bridging must use the explicit contract, not another transport's internals.
    """

    def __init__(self, **kwargs):
        self.headers = dict(kwargs.get("headers") or {})
        self.callbacks = {}
        self.disconnect_callback = None
        self.connected = False
        self.server_info = {"server": {"host": "handshake-test"}}
        self.chat_send = AsyncMock(return_value={"ok": True})
        self.chat_inject = AsyncMock(return_value={"ok": True})
        self.chat_abort = AsyncMock(return_value={"ok": True})
        self.interaction_resolve = AsyncMock(return_value=_make_exchange())
        self.has_running_runs = False
        self.refresh_proxypass_token = MagicMock()

    def on_event(self, event, handler):
        self.callbacks[event] = handler

    def on_disconnect(self, handler):
        self.disconnect_callback = handler

    async def connect(self):
        assert set(self.callbacks) == {
            "chat",
            "agent",
            "error",
            "*",
            "interaction.requested",
            "interaction.resolved",
            "mode_transition.resolved",
        }
        assert self.disconnect_callback is not None
        self.callbacks["chat"](
            {
                "sessionKey": "handshake",
                "state": "final",
                "message": {"content": [{"text": "during connect"}]},
            }
        )
        self.connected = True
        return self.server_info

    async def close(self):
        self.connected = False


@pytest.mark.asyncio
@pytest.mark.parametrize("leg", ["ws", "grey-ws", "a2a", "fallback-ws"])
async def test_callbacks_bound_before_handshake(monkeypatch, leg):
    monkeypatch.setattr(f"{_WS_MODULE}.BotWebSocketClient", _HandshakeClient)
    if leg == "grey-ws":
        monkeypatch.setattr(
            f"{_A2A_MODULE}.BotA2aClient",
            MagicMock(side_effect=A2aUnavailableError("probe unavailable")),
        )
    else:
        monkeypatch.setattr(f"{_A2A_MODULE}.BotA2aClient", _HandshakeClient)
    acc = AsyncChatClient(
        uri="ws://host/ws",
        max_retries=0,
        transport_mode="off" if leg == "ws" else "grey",
    )
    try:
        if leg == "fallback-ws":
            await acc.connect()
            acc._sessions["handshake"] = state = SessionState()
            await acc._chat_transport.create_fallback()
        else:
            acc._sessions["handshake"] = state = SessionState()
            await acc.connect()
        assert state.chat_complete.is_set()
        assert state.content == "during connect"
    finally:
        await acc.close()


@pytest.mark.asyncio
async def test_ws_reconnect_binds_before_handshake(monkeypatch):
    monkeypatch.setattr(f"{_WS_MODULE}.BotWebSocketClient", _HandshakeClient)
    acc = AsyncChatClient(uri="ws://host/ws", max_retries=0)
    await acc.connect()
    acc._sessions["handshake"] = state = SessionState()
    acc.refresh_outbound_auth({"x-proxypass-token": "reconnect-placeholder"})
    acc._client.connected = False
    acc._max_retries = 1
    acc._retry_base_backoff = 0
    task = asyncio.create_task(acc._reconnect_loop())
    try:
        await asyncio.wait_for(state.chat_complete.wait(), timeout=1)
        assert state.content == "during connect"
        assert acc.transport_name == "v3-ws"
        assert acc._client.headers["x-proxypass-token"] == "reconnect-placeholder"
    finally:
        await acc.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,leg", [("off", "main"), ("grey", "main"), ("on", "fallback")]
)
async def test_ws_operations_never_mint_or_receive_a2a_arguments(
    monkeypatch, mode, leg
):
    from secbaas.community.api.bot_interaction import InteractionResolution

    monkeypatch.setattr(f"{_WS_MODULE}.BotWebSocketClient", _HandshakeClient)
    if mode == "grey":
        monkeypatch.setattr(
            f"{_A2A_MODULE}.BotA2aClient",
            MagicMock(side_effect=A2aUnavailableError("probe unavailable")),
        )
    else:
        monkeypatch.setattr(f"{_A2A_MODULE}.BotA2aClient", _HandshakeClient)
    minter = AsyncMock(side_effect=AssertionError("WS invoked an A2A minter"))
    resolution = InteractionResolution(kind="exec", decision="allow-once")
    service = _claim_service_for(resolution)
    acc = AsyncChatClient(
        uri="ws://host/ws",
        transport_mode=mode,
        max_retries=0,
        proxypass_token_minter=minter,
        interaction_service=service,
        headers={"x-proxypass-token": "old-placeholder"},
    )
    try:
        await acc.connect()
        acc.refresh_outbound_auth({"x-proxypass-token": "new-placeholder"})
        # Attachment fallback handshakes must see the refreshed snapshot.
        attachments = [{"type": "image"}] if leg == "fallback" else None
        await acc.send_message(
            "message", session_key="sk1", attachments=attachments, wait_result=False
        )
        await acc.inject_message(
            "injection", session_key="sk1", attachments=attachments
        )
        await acc.chat_abort(session_key="sk1", run_id="run1")
        await acc._dispatch_interaction_answer(
            session_key="sk1", interaction_id="int-1", deadline_ms=None
        )
        target = (
            acc._chat_transport.fallback_client if leg == "fallback" else acc._client
        )
        target.chat_send.assert_awaited_once()
        target.chat_inject.assert_awaited_once()
        target.chat_abort.assert_awaited_once_with(session_key="sk1", run_id="run1")
        target.interaction_resolve.assert_awaited_once_with(
            interaction_id="int-1", resolution=resolution
        )
        assert target.headers["x-proxypass-token"] == (
            "new-placeholder" if leg == "fallback" else "old-placeholder"
        )
        target.refresh_proxypass_token.assert_not_called()
        minter.assert_not_awaited()
    finally:
        await acc.close()


@pytest.mark.parametrize("event", ["chat", "agent", "error"])
@pytest.mark.parametrize("ignore_case", [False, True])
def test_fallback_terminal_prefers_exact_session(event, ignore_case):
    acc = AsyncChatClient(
        uri="ws://host/ws", transport_mode="on", ignore_case=ignore_case
    )
    strategy = acc._chat_transport
    strategy.routed_sessions.update({"a": "v3", "ab": "v3"})
    bridge = strategy._scoped_bridge(acc._event_bridge_snapshot()[event])
    bridge({"sessionKey": "ab", "state": "error" if event == "error" else "final"})
    assert strategy.routed_sessions == {"a": "v3"}
