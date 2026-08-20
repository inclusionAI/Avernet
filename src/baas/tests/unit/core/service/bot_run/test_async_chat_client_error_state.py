"""Unit tests for AsyncChatClient error-state propagation (AVE-GOLD-002).

Covers ChatErrorStateError raised by send_message when WebSocket event
state is "error", and the error_message field on _SessionState.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

_TEST_SESSION_KEY = "test-session-key"


def _setup_session_state(client, session_key=_TEST_SESSION_KEY):
    """Helper: register a _SessionState for a given session_key."""
    from secbaas.community.core.service.bot_run._async_chat_client import _SessionState

    return client._sessions.setdefault(session_key, _SessionState())


@pytest.fixture
def mock_bot_ws():
    with patch(
        "secbaas.community.core.service.bot_run._async_chat_client.BotWebSocketClient"
    ) as mock:
        yield mock


@pytest.fixture
def mock_bot_ws_instance(mock_bot_ws):
    instance = mock_bot_ws.return_value
    instance.connect = AsyncMock(
        return_value={
            "server": {"host": "testsrv", "port": 8080},
            "features": {"chat": True},
        }
    )
    instance.close = AsyncMock()
    instance.chat_send = AsyncMock()
    instance.chat_inject = AsyncMock()
    instance.connected = True
    return instance


class TestChatErrorStateErrorOnAgentError:
    """send_message raises ChatErrorStateError when _on_agent sets state=error."""

    @pytest.mark.asyncio
    async def test_agent_error_state_raises_chat_error_state_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatErrorStateError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def fire_agent_error(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.state = "error"
                state.error_message = "CONNECTION_ERROR"
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = fire_agent_error

        with pytest.raises(ChatErrorStateError, match="CONNECTION_ERROR"):
            await client.send_message("Hi", session_key=_TEST_SESSION_KEY)

    @pytest.mark.asyncio
    async def test_normal_final_does_not_raise(self, mock_bot_ws, mock_bot_ws_instance):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def fire_chat_final(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.content = "OK"
                state.state = "final"
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = fire_chat_final

        content, agent_payloads = await client.send_message(
            "Hi", session_key=_TEST_SESSION_KEY
        )
        assert content == "OK"
        assert agent_payloads == []


class TestSessionStateErrorMessage:
    """_SessionState.error_message is populated on error transitions."""

    def test_handle_terminal_error_stores_error_message(self):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )
        from secbaas.community.core.service.bot_run._session_state import _SessionState

        state = _SessionState()
        AsyncChatClient._handle_terminal_error(
            state, "sk-1", "CONNECTION_ERROR", "agent"
        )
        assert state.state == "error"
        assert state.error_message == "CONNECTION_ERROR"

    def test_handle_terminal_error_default_message(self):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )
        from secbaas.community.core.service.bot_run._session_state import _SessionState

        state = _SessionState()
        AsyncChatClient._handle_terminal_error(state, "sk-1", "", "agent")
        assert state.state == "error"
        assert state.error_message == "agent error"

    @pytest.mark.asyncio
    async def test_on_chat_error_stores_error_message(self):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "state": "error",
            "message": {"content": [{"text": "chat error text"}]},
        }
        client._on_chat(payload)
        await asyncio.sleep(0)
        assert state.state == "error"
        assert state.error_message == "chat error text"

    @pytest.mark.asyncio
    async def test_on_chat_error_empty_text_uses_default(self):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "state": "error",
            "message": {"content": []},
        }
        client._on_chat(payload)
        await asyncio.sleep(0)
        assert state.state == "error"
        assert state.error_message == "chat error"


class TestChatErrorStateErrorDefinition:
    """ChatErrorStateError class definition and behavior."""

    def test_is_exception(self):
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatErrorStateError,
        )

        assert issubclass(ChatErrorStateError, Exception)

    def test_carries_message(self):
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatErrorStateError,
        )

        err = ChatErrorStateError("CONNECTION_ERROR")
        assert str(err) == "CONNECTION_ERROR"

    def test_catchable_as_exception(self):
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatErrorStateError,
        )

        with pytest.raises(Exception):
            raise ChatErrorStateError("test error")


class TestSendStreamErrorTerminalChunk:
    """Stream path: error chunk as terminal chunk is tracked."""

    @pytest.mark.asyncio
    async def test_drain_stream_queue_yields_error_chunk(self):
        """Stream consumer yields error chunk without raising."""
        from secbaas.community.api.sse import StreamChunk
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        queue: asyncio.Queue[StreamChunk] = asyncio.Queue()
        await queue.put(StreamChunk(type="delta", content="hello"))
        await queue.put(StreamChunk(type="error", content="CONNECTION_ERROR"))

        chunks_received = []
        async for chunk in AsyncChatClient._drain_stream_queue(queue, None):
            chunks_received.append(chunk)

        types = [c.type for c in chunks_received]
        assert types == ["delta", "error"]
        assert chunks_received[-1].content == "CONNECTION_ERROR"
