"""Unit tests for AsyncChatClient.

Covers:
- __init__: defaults, custom params, header passthrough
- connect: success, error propagation, handshake response, double connect
- send_message: success with content, wait_result=False, auth_token, timeout
- close: normal close, close when not connected
- context manager: async with pattern, __aexit__ calls close
- _on_chat: delta, final, error, ignored states
- _on_agent: tool/result, assistant, lifecycle/end, final events
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

# Reusable test session key
_TEST_SESSION_KEY = "test-session-key"


def _setup_session_state(client, session_key=_TEST_SESSION_KEY):
    """Helper: register a _SessionState for a given session_key."""
    from secbaas.community.core.service.bot_run._async_chat_client import _SessionState

    return client._sessions.setdefault(session_key, _SessionState())


@pytest.fixture
def mock_bot_ws():
    """Create a mock BotWebSocketClient."""
    with patch(
        "secbaas.community.core.service.bot_run._async_chat_client.BotWebSocketClient"
    ) as mock:
        yield mock


@pytest.fixture
def mock_bot_ws_instance(mock_bot_ws):
    """Create a mock BotWebSocketClient instance with async methods."""
    instance = mock_bot_ws.return_value
    # 所有方法改为 async
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


@pytest.fixture
def hello_response():
    """Standard handshake response."""
    return {
        "server": {"host": "testsrv", "port": 8080},
        "features": {"chat": True},
    }


# ==================== __init__ tests ====================


class TestInit:
    def test_defaults_auto_generates_client_id(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://localhost/ws")
        assert client.uri == "ws://localhost/ws"
        assert client.client_id.startswith("client-")
        assert len(client.client_id) == len("client-") + 8
        assert client.client_version == "1.0.0"
        assert client.headers == {}
        assert client.verbose is False

    def test_custom_params(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(
            uri="wss://host/ws",
            client_id="my-custom-id",
            client_version="2.0.0",
            verbose=True,
        )
        assert client.uri == "wss://host/ws"
        assert client.client_id == "my-custom-id"
        assert client.client_version == "2.0.0"
        assert client.verbose is True

    def test_header_passthrough(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        headers = {"Cookie": "session=abc123", "X-Custom": "val"}
        client = AsyncChatClient(uri="ws://host/ws", headers=headers)
        assert client.headers == headers

    def test_empty_headers_defaults_to_empty_dict(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        assert client.headers == {}

    def test_internal_state_initialized(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        assert client._client is None
        # 纯 async 版本无 _loop 属性
        assert not hasattr(client, "_loop")
        assert isinstance(client._condition, asyncio.Condition)
        assert client._sessions == {}
        assert client._active_sessions == set()


# ==================== connect tests ====================


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, mock_bot_ws, mock_bot_ws_instance):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "myserver"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        result = await client.connect()

        mock_bot_ws.assert_called_once_with(
            uri="ws://host/ws",
            client_id=client.client_id,
            client_version="1.0.0",
            headers={},
        )
        mock_bot_ws_instance.on_event.assert_any_call("chat", client._on_chat)
        mock_bot_ws_instance.on_event.assert_any_call("agent", client._on_agent)
        assert result["server"]["host"] == "myserver"
        assert client._client is mock_bot_ws_instance

    @pytest.mark.asyncio
    async def test_connect_error_propagation(self, mock_bot_ws, mock_bot_ws_instance):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.side_effect = RuntimeError("Connection refused")

        client = AsyncChatClient(uri="ws://host/ws")
        with pytest.raises(RuntimeError, match="Connection refused"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_connect_already_connected_raises(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        with pytest.raises(RuntimeError, match="Already connected"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_connect_calls_underlying_connect(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """Verify connect() directly awaits BotWebSocketClient.connect."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        # 直接 await，不再通过 run_in_executor
        mock_bot_ws_instance.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_verbose_logs(self, mock_bot_ws, mock_bot_ws_instance):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "mysrv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws", verbose=True)
        await client.connect()
        assert client._client is mock_bot_ws_instance


# ==================== send_message tests ====================


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_success(self, mock_bot_ws, mock_bot_ws_instance):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def fire_chat_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.content = "Hello, world!"
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = fire_chat_complete

        content, agent_payloads = await client.send_message("Hi there")
        assert content == "Hello, world!"
        assert agent_payloads == []

    @pytest.mark.asyncio
    async def test_send_message_wait_result_false(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        content, agent_payloads = await client.send_message("Hi", wait_result=False)
        assert content == ""
        assert agent_payloads == []
        mock_bot_ws_instance.chat_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_with_auth_token(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def fire_chat_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = fire_chat_complete

        await client.send_message("Hi", auth_token="my-token")
        assert mock_bot_ws_instance.chat_send.call_count == 1
        call_kwargs = mock_bot_ws_instance.chat_send.call_args[1]
        assert call_kwargs["session_key"] is not None
        assert call_kwargs["message"] == "Hi"
        assert call_kwargs["auth_token"] == "my-token"

    @pytest.mark.asyncio
    async def test_send_message_raises_if_not_connected(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
            NotConnectedError,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        with pytest.raises(
            NotConnectedError,
            match="Not connected",
        ):
            await client.send_message("Hi")

    @pytest.mark.asyncio
    async def test_send_message_with_session_key(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def fire_chat_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = fire_chat_complete

        await client.send_message("Hi", session_key="my-session")
        call_kwargs = mock_bot_ws_instance.chat_send.call_args[1]
        assert call_kwargs["session_key"] == "my-session"
        assert call_kwargs["message"] == "Hi"
        assert call_kwargs["auth_token"] is None

    @pytest.mark.asyncio
    async def test_send_message_timeout(self, mock_bot_ws, mock_bot_ws_instance):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        # Don't fire chat_complete — timeout will occur
        with pytest.raises(TimeoutError):
            await client.send_message("Hi", timeout=0.01)

    @pytest.mark.asyncio
    async def test_send_message_resets_state(self, mock_bot_ws, mock_bot_ws_instance):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        sk = "reset-test-key"
        _setup_session_state(client, sk)
        state = client._sessions[sk]
        state.content = "old content"
        state.agent_payloads = [{"old": "payload"}]
        state.last_stream_is_assistant = True

        async def fire_chat_complete(*args, **kwargs):
            s = client._sessions.get(kwargs["session_key"])
            if s:
                s.content = "new content"
                s.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = fire_chat_complete

        content, agent_payloads = await client.send_message("Hi", session_key=sk)
        assert content == "new content"
        assert agent_payloads == []
        assert sk not in client._sessions


# ==================== close tests ====================


class TestClose:
    @pytest.mark.asyncio
    async def test_close_normal(self, mock_bot_ws, mock_bot_ws_instance):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        client._sessions["s1"] = _setup_session_state(client, "s1")
        client._active_sessions.add("s1")

        await client.close()
        mock_bot_ws_instance.close.assert_called_once()
        assert client._client is None
        assert client._sessions == {}
        assert client._active_sessions == set()

    @pytest.mark.asyncio
    async def test_close_when_not_connected(self, mock_bot_ws, mock_bot_ws_instance):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        await client.close()
        mock_bot_ws_instance.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_directly_awaits_underlying_close(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """close() directly awaits BotWebSocketClient.close(), no executor."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()
        await client.close()
        mock_bot_ws_instance.close.assert_called_once()


# ==================== _on_chat handler tests ====================


class TestOnChat:
    @pytest.mark.asyncio
    async def test_on_chat_delta_state(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "state": "delta",
                "message": {"content": [{"text": "partial response"}]},
            }
        )
        await asyncio.sleep(0)
        assert state.content == "partial response"
        assert state.state == ""
        assert state.chat_complete.is_set() is False

    @pytest.mark.asyncio
    async def test_on_chat_final_state(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "state": "final",
                "message": {"content": [{"text": "complete response"}]},
            }
        )
        await asyncio.sleep(0)
        assert state.state == "final"
        assert state.content == "complete response"
        assert state.chat_complete.is_set() is True

    @pytest.mark.asyncio
    async def test_on_chat_ignores_inject_run_id_final_state(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)
        state.stream_queue = asyncio.Queue()

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "runId": "inject-3678aa1a-4fac-45cf-b181-8430342c661a",
                "state": "final",
                "message": {"content": [{"text": "[BCS Group Context] leaked"}]},
            }
        )
        await asyncio.sleep(0)
        assert state.state == ""
        assert state.content == ""
        assert state.chat_complete.is_set() is False
        assert state.stream_queue.empty() is True

    @pytest.mark.asyncio
    async def test_on_chat_error_state(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "state": "error",
                "errorMessage": "LLM is trying to invoke a non-exist tool",
            }
        )
        await asyncio.sleep(0)
        assert state.state == "error"
        assert state.chat_complete.is_set() is True

    @pytest.mark.asyncio
    async def test_on_chat_unknown_state(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "state": "thinking",
                "message": {"content": [{"text": "thinking..."}]},
            }
        )
        await asyncio.sleep(0)
        assert state.state == ""
        assert state.content == ""
        assert state.chat_complete.is_set() is False

    @pytest.mark.asyncio
    async def test_on_chat_empty_content(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "state": "final",
                "message": {"content": []},
            }
        )
        await asyncio.sleep(0)
        assert state.state == "final"
        assert state.content == ""
        assert state.chat_complete.is_set() is True


# ==================== _on_agent handler tests ====================


class TestOnAgent:
    @pytest.mark.asyncio
    async def test_on_agent_tool_result_appends(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "tool",
            "data": {"phase": "result", "tool": "search"},
        }
        client._on_agent(payload)
        assert len(state.agent_payloads) == 1
        assert state.agent_payloads[0] == payload
        assert state.last_stream_is_assistant is False

    @pytest.mark.asyncio
    async def test_on_agent_tool_non_result_ignored(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "tool",
            "data": {"phase": "start", "tool": "search"},
        }
        client._on_agent(payload)
        assert len(state.agent_payloads) == 0

    @pytest.mark.asyncio
    async def test_on_agent_assistant_appends(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "assistant",
            "data": {"content": "Hello"},
        }
        client._on_agent(payload)
        assert len(state.agent_payloads) == 1
        assert state.agent_payloads[0] == payload
        assert state.last_stream_is_assistant is True

    @pytest.mark.asyncio
    async def test_on_agent_assistant_replaces_on_consecutive(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        first = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "assistant",
            "data": {"content": "First"},
        }
        second = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "assistant",
            "data": {"content": "Second"},
        }

        client._on_agent(first)
        assert len(state.agent_payloads) == 1

        client._on_agent(second)
        assert len(state.agent_payloads) == 1
        assert state.agent_payloads[0] == second
        assert state.last_stream_is_assistant is True

    @pytest.mark.asyncio
    async def test_on_agent_lifecycle_end_sets_event(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "lifecycle",
            "data": {"phase": "end"},
        }
        client._on_agent(payload)
        await asyncio.sleep(0)
        assert state.agent_complete.is_set() is True
        assert state.last_stream_is_assistant is False

    @pytest.mark.asyncio
    async def test_on_agent_lifecycle_non_end_ignored(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "lifecycle",
            "data": {"phase": "start"},
        }
        client._on_agent(payload)
        assert state.agent_complete.is_set() is False
        assert state.last_stream_is_assistant is False

    @pytest.mark.asyncio
    async def test_on_agent_unknown_stream_resets_assistant(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)

        state.last_stream_is_assistant = True

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "unknown",
            "data": {},
        }
        client._on_agent(payload)
        assert state.last_stream_is_assistant is False

    @pytest.mark.asyncio
    async def test_on_agent_final_sets_complete_events(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)
        state.stream_queue = asyncio.Queue()

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "state": "final",
            "message": {
                "content": [{"type": "text", "text": "agent final text"}],
            },
        }
        client._on_agent(payload)

        assert state.state == "final"
        assert state.agent_complete.is_set()
        assert state.chat_complete.is_set()
        assert state.content == "agent final text"

        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "final"
        assert chunk.content == "agent final text"

    @pytest.mark.asyncio
    async def test_on_agent_final_empty_message(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client)
        state.stream_queue = asyncio.Queue()

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "state": "final",
            "message": {},
        }
        client._on_agent(payload)

        assert state.state == "final"
        assert state.agent_complete.is_set()
        assert state.chat_complete.is_set()
        assert state.content == ""

        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "final"
        assert chunk.content == ""


# ==================== integration: full flow ====================


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_send_message_flow(self, mock_bot_ws, mock_bot_ws_instance):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def simulate_chat_response(*args, **kwargs):
            sk = kwargs["session_key"]
            client._on_chat(
                {
                    "sessionKey": sk,
                    "state": "delta",
                    "message": {"content": [{"text": "partial"}]},
                }
            )
            client._on_chat(
                {
                    "sessionKey": sk,
                    "state": "final",
                    "message": {"content": [{"text": "full response"}]},
                }
            )

        mock_bot_ws_instance.chat_send.side_effect = simulate_chat_response

        content, _ = await client.send_message("Hello!")
        assert content == "full response"

    @pytest.mark.asyncio
    async def test_send_message_with_agent_events(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def simulate_with_agent(*args, **kwargs):
            sk = kwargs["session_key"]
            tool_payload = {
                "sessionKey": sk,
                "stream": "tool",
                "data": {"phase": "result", "name": "search"},
            }
            client._on_agent(tool_payload)
            client._on_chat(
                {
                    "sessionKey": sk,
                    "state": "final",
                    "message": {"content": [{"text": "done"}]},
                }
            )

        mock_bot_ws_instance.chat_send.side_effect = simulate_with_agent

        content, agent_payloads = await client.send_message("Hello!")
        assert content == "done"
        assert len(agent_payloads) == 1
        assert agent_payloads[0]["stream"] == "tool"
        assert agent_payloads[0]["data"]["phase"] == "result"

    @pytest.mark.asyncio
    async def test_concurrent_sends_with_different_keys(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        import asyncio

        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def slow_chat_send(*args, **kwargs):
            pass

        mock_bot_ws_instance.chat_send.side_effect = slow_chat_send

        async def send_msg_1():
            return await client.send_message(
                "msg1", session_key="sess-1", wait_result=False
            )

        async def send_msg_2():
            return await client.send_message(
                "msg2", session_key="sess-2", wait_result=False
            )

        results = await asyncio.gather(send_msg_1(), send_msg_2())
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_concurrent_sends_same_key_raises(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """同 sessionKey 并发发送在超时后抛 ConcurrentSessionError。"""
        import asyncio

        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
            ConcurrentSessionError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        # 使用极短超时，让排队快速超时
        client = AsyncChatClient(uri="ws://host/ws", session_key_timeout=0.05)
        await client.connect()

        async def never_complete(*args, **kwargs):
            # 模拟一个永远不完成的发送（不设置 chat_complete）
            await asyncio.sleep(10)

        mock_bot_ws_instance.chat_send.side_effect = never_complete

        async def send_msg():
            return await client.send_message("msg", session_key="same-key")

        t1 = asyncio.create_task(send_msg())
        await asyncio.sleep(0.01)  # 让 t1 开始

        with pytest.raises(ConcurrentSessionError, match="Timed out waiting"):
            await client.send_message("msg", session_key="same-key")

        t1.cancel()
        try:
            await t1
        except (asyncio.CancelledError, Exception):
            pass


# ==================== concurrency semaphore tests ====================


class TestConcurrencySemaphore:
    @pytest.mark.asyncio
    async def test_semaphore_limits_parallel(self, mock_bot_ws, mock_bot_ws_instance):
        """max_concurrent_sessions=2 时，第 3 个请求会阻塞直到前面的完成。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws", max_concurrent_sessions=2)
        await client.connect()

        running = 0
        max_running = 0
        lock = asyncio.Lock()

        async def slow_chat_send(*args, **kwargs):
            nonlocal running, max_running
            async with lock:
                running += 1
                max_running = max(max_running, running)
            await asyncio.sleep(0.05)
            async with lock:
                running -= 1
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = slow_chat_send

        tasks = [
            asyncio.create_task(client.send_message(f"msg{i}", session_key=f"sess-{i}"))
            for i in range(3)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 3
        # 信号量限制为 2，所以同时运行的最大数不超过 2
        assert max_running <= 2

    @pytest.mark.asyncio
    async def test_semaphore_unlimited(self, mock_bot_ws, mock_bot_ws_instance):
        """max_concurrent_sessions=0 时，所有并发请求立即执行。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws", max_concurrent_sessions=0)
        await client.connect()

        async def instant_chat_send(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = instant_chat_send

        tasks = [
            asyncio.create_task(client.send_message(f"msg{i}", session_key=f"sess-{i}"))
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 5


# ==================== session key queueing tests ====================


class TestSessionKeyQueueing:
    @pytest.mark.asyncio
    async def test_same_key_queues_instead_of_reject(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """同一 sessionKey 的第二个请求排队等待第一个完成后执行，而非硬拒绝。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws", session_key_timeout=5.0)
        await client.connect()

        order = []

        async def first_send(*args, **kwargs):
            order.append("first_start")
            await asyncio.sleep(0.1)
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.content = "first response"
                state.chat_complete.set()
            order.append("first_end")

        mock_bot_ws_instance.chat_send.side_effect = first_send

        # 第一个发送
        t1 = asyncio.create_task(client.send_message("first", session_key="same-key"))
        await asyncio.sleep(0.02)  # 让 t1 开始

        # 第二个发送 —— 应该排队等待 t1 完成
        # 使用一个 side_effect 函数，在 t1 完成后设置 chat_complete
        async def second_send(*args, **kwargs):
            order.append("second_start")
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.content = "second response"
                state.chat_complete.set()
            order.append("second_end")

        mock_bot_ws_instance.chat_send.side_effect = second_send

        t2 = asyncio.create_task(client.send_message("second", session_key="same-key"))

        r1 = await t1
        r2 = await t2

        assert r1[0] == "first response"
        assert r2[0] == "second response"
        # 确保顺序正确：第一个完成后第二个才开始
        assert order.index("first_start") < order.index("first_end")
        # second_start 应在 first_end 之后
        assert order.index("second_start") > order.index("first_end")

    @pytest.mark.asyncio
    async def test_same_key_queueing_timeout(self, mock_bot_ws, mock_bot_ws_instance):
        """排队等待超时后抛 ConcurrentSessionError。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
            ConcurrentSessionError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws", session_key_timeout=0.05)
        await client.connect()

        async def never_complete(*args, **kwargs):
            await asyncio.sleep(10)

        mock_bot_ws_instance.chat_send.side_effect = never_complete

        t1 = asyncio.create_task(client.send_message("msg", session_key="same-key"))
        await asyncio.sleep(0.01)

        with pytest.raises(ConcurrentSessionError, match="Timed out waiting"):
            await client.send_message("msg2", session_key="same-key")

        t1.cancel()
        try:
            await t1
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_different_keys_no_blocking(self, mock_bot_ws, mock_bot_ws_instance):
        """不同 sessionKey 的请求互不阻塞。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def instant_send(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = instant_send

        results = await asyncio.gather(
            client.send_message("msg1", session_key="key-1"),
            client.send_message("msg2", session_key="key-2"),
        )
        assert len(results) == 2


# ==================== reconnection tests ====================


class TestReconnection:
    @pytest.mark.asyncio
    async def test_is_reconnecting_property(self, mock_bot_ws, mock_bot_ws_instance):
        """is_reconnecting 初始为 False。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        assert client.is_reconnecting is False

    @pytest.mark.asyncio
    async def test_max_retries_zero_disables_reconnect(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """max_retries=0 时不启动重连监控任务。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()
        # max_retries=0 时不启动 _reconnect_monitor
        assert client._reconnect_monitor is None

    @pytest.mark.asyncio
    async def test_max_retries_nonzero_starts_reconnect_monitor(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """max_retries>0 时启动重连监控任务。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws", max_retries=1)
        await client.connect()
        assert client._reconnect_monitor is not None
        assert not client._reconnect_monitor.done()

        await client.close()

    @pytest.mark.asyncio
    async def test_close_sets_intentionally_closed(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """close() 设置 _closed_intentionally=True 并取消重连监控。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws", max_retries=2)
        await client.connect()
        monitor = client._reconnect_monitor
        assert monitor is not None

        await client.close()
        assert client._closed_intentionally is True
        assert monitor.cancelled() or monitor.done()


# ==================== init params tests (new params) ====================


class TestNewInitParams:
    def test_default_concurrency_params(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        assert client._max_concurrent_sessions == 0
        assert client._session_key_timeout == 30.0
        assert client._max_retries == 1
        assert client._retry_base_backoff == 0.5
        assert client._concurrency_sem is None  # 0 = 无限制
        assert client._active_sessions == set()

    def test_custom_concurrency_params(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(
            uri="ws://host/ws",
            max_concurrent_sessions=5,
            session_key_timeout=10.0,
            max_retries=3,
            retry_base_backoff=1.0,
        )
        assert client._max_concurrent_sessions == 5
        assert client._session_key_timeout == 10.0
        assert client._max_retries == 3
        assert client._retry_base_backoff == 1.0
        assert client._concurrency_sem is not None
        assert client._concurrency_sem._value == 5

    def test_active_sessions_is_set(self, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        assert isinstance(client._active_sessions, set)

    @pytest.mark.asyncio
    async def test_close_clears_active_sessions(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """关闭时清理所有 active sessions。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        # 手动添加 active sessions
        client._active_sessions.add("s1")
        client._active_sessions.add("s2")

        await client.close()
        assert client._active_sessions == set()


# ==================== NotConnectedError tests ====================


class TestNotConnectedError:
    @pytest.mark.asyncio
    async def test_send_message_raises_not_connected(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message raises NotConnectedError when not connected."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
            NotConnectedError,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        # _client is None → NotConnectedError
        with pytest.raises(NotConnectedError, match="Not connected"):
            await client.send_message("Hi", session_key="s1")

    @pytest.mark.asyncio
    async def test_inject_message_raises_not_connected(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """inject_message raises NotConnectedError when not connected."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
            NotConnectedError,
        )

        client = AsyncChatClient(uri="ws://host/ws")
        with pytest.raises(NotConnectedError, match="Not connected"):
            await client.inject_message("Hi", session_key="s1")


# ==================== inject_message semaphore tests ====================


class TestInjectMessageSemaphore:
    @pytest.mark.asyncio
    async def test_inject_respects_semaphore(self, mock_bot_ws, mock_bot_ws_instance):
        """inject_message 受并发信号量约束。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws", max_concurrent_sessions=1)
        await client.connect()

        barrier = asyncio.Event()
        in_inject = asyncio.Event()

        async def slow_inject(*args, **kwargs):
            in_inject.set()
            await barrier.wait()

        mock_bot_ws_instance.chat_inject.side_effect = slow_inject

        # 第一个 inject 占用信号量
        t1 = asyncio.create_task(client.inject_message("m1", session_key="s1"))
        await asyncio.sleep(0.02)
        assert in_inject.is_set()

        # 第二个 inject 应该被信号量阻塞
        started = asyncio.Event()

        async def inject_with_signal(*args, **kwargs):
            started.set()

        mock_bot_ws_instance.chat_inject.side_effect = inject_with_signal

        t2 = asyncio.create_task(client.inject_message("m2", session_key="s2"))
        # 短暂等待，t2 不应该开始（信号量被 t1 占用）
        await asyncio.sleep(0.02)
        assert not started.is_set()

        # 释放 t1
        barrier.set()
        await t1
        # t2 现在应该可以运行
        await t2
        assert started.is_set()

    @pytest.mark.asyncio
    async def test_inject_no_semaphore_when_zero(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """max_concurrent_sessions=0 时 inject_message 不受信号量限制。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws", max_concurrent_sessions=0)
        await client.connect()

        call_count = 0

        async def count_inject(*args, **kwargs):
            nonlocal call_count
            call_count += 1

        mock_bot_ws_instance.chat_inject.side_effect = count_inject

        # 多个并发 inject 都应该立即执行
        await asyncio.gather(
            client.inject_message("m1", session_key="s1"),
            client.inject_message("m2", session_key="s2"),
            client.inject_message("m3", session_key="s3"),
        )
        assert call_count == 3


# ==================== ChatRequestError propagation tests ====================


class TestChatRequestErrorPropagation:
    """Tests for ChatRequestError handling in AsyncChatClient."""

    @pytest.mark.asyncio
    async def test_send_message_propagates_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message propagates ChatRequestError from chat_send and sets chat_complete."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = ChatRequestError(
            message="chat.send failed: UNAVAILABLE - Session validation failed",
            error_code="UNAVAILABLE",
            error_message="Session validation failed",
            retryable=True,
        )

        with pytest.raises(ChatRequestError, match="chat.send failed"):
            await client.send_message("Hi", session_key="sk-err")

    @pytest.mark.asyncio
    async def test_send_message_sets_chat_complete_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message sets chat_complete on ChatRequestError to unblock waiters."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = ChatRequestError(
            message="chat.send failed: UNAVAILABLE - Session validation failed",
            error_code="UNAVAILABLE",
            error_message="Session validation failed",
        )

        # Capture session state before the error to verify chat_complete is set
        captured_state = None

        original_chat_send = mock_bot_ws_instance.chat_send.side_effect

        async def chat_send_with_state_capture(*args, **kwargs):
            state = client._sessions.get(kwargs.get("session_key"))
            nonlocal captured_state
            captured_state = state
            raise original_chat_send

        mock_bot_ws_instance.chat_send.side_effect = chat_send_with_state_capture

        with pytest.raises(ChatRequestError, match="chat.send failed"):
            await client.send_message("Hi", session_key="sk-err")

        # chat_complete should have been set to unblock any waiters
        assert captured_state is not None
        assert captured_state.chat_complete.is_set()

    @pytest.mark.asyncio
    async def test_send_message_cleans_up_session_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message cleans up session state after ChatRequestError."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = ChatRequestError(
            message="chat.send failed: UNAVAILABLE - Session validation failed",
            error_code="UNAVAILABLE",
            error_message="Session validation failed",
        )

        with pytest.raises(ChatRequestError, match="chat.send failed"):
            await client.send_message("Hi", session_key="sk-err")

        # Session should be cleaned up in the finally block
        assert "sk-err" not in client._sessions
        assert "sk-err" not in client._active_sessions


# ==================== ChatRequestError in send_message_stream tests ====================


class TestChatRequestErrorStream:
    """Tests for ChatRequestError handling in send_message_stream."""

    @pytest.mark.asyncio
    async def test_send_message_stream_emits_error_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message_stream emits error StreamChunk when ChatRequestError is raised."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = ChatRequestError(
            message="chat.send failed: UNAVAILABLE - Session validation failed",
            error_code="UNAVAILABLE",
            error_message="Session validation failed",
        )

        chunks = []
        async for chunk in client.send_message_stream("Hi", session_key="sk-err"):
            chunks.append(chunk)

        # Should emit exactly one error chunk
        assert len(chunks) == 1
        assert chunks[0].type == "error"
        assert "UNAVAILABLE" in chunks[0].content
        assert "Session validation failed" in chunks[0].content

    @pytest.mark.asyncio
    async def test_send_message_stream_sets_chat_complete_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message_stream sets chat_complete when ChatRequestError is raised."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
            _SessionState,
        )
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        # Pre-register a session state so we can inspect it after the error
        sk = "sk-err"
        state = _SessionState()
        client._sessions[sk] = state

        mock_bot_ws_instance.chat_send.side_effect = ChatRequestError(
            message="chat.send failed: UNAVAILABLE - Session validation failed",
            error_code="UNAVAILABLE",
            error_message="Session validation failed",
        )

        chunks = []
        async for chunk in client.send_message_stream("Hi", session_key=sk):
            chunks.append(chunk)

        # The session gets cleaned up in the finally block, but chat_complete
        # was set before the iterator returned
        assert len(chunks) == 1
        assert chunks[0].type == "error"

    @pytest.mark.asyncio
    async def test_send_message_stream_cleans_up_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message_stream cleans up session state after ChatRequestError."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = ChatRequestError(
            message="chat.send failed: UNAVAILABLE - Session validation failed",
            error_code="UNAVAILABLE",
            error_message="Session validation failed",
        )

        chunks = []
        async for chunk in client.send_message_stream("Hi", session_key="sk-err"):
            chunks.append(chunk)

        # Session should be cleaned up
        assert "sk-err" not in client._sessions
        assert "sk-err" not in client._active_sessions


# ==================== BotSessionError tests ====================


class TestBotSessionError:
    """Tests for BotSessionError: send_message raises it when state='error'."""

    @pytest.mark.asyncio
    async def test_send_message_raises_bot_session_error_on_error_state(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message raises BotSessionError when session ends with state=error."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
            BotSessionError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def fire_error_chat_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.state = "error"
                state.content = "CONNECTION_ERROR"
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = fire_error_chat_complete

        with pytest.raises(BotSessionError, match="session ended with error state"):
            await client.send_message("Hi", session_key="sk-err")

    @pytest.mark.asyncio
    async def test_send_message_returns_normally_on_final_state(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message returns content normally when state=final (not error)."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def fire_final_chat_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.state = "final"
                state.content = "normal response"
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = fire_final_chat_complete

        content, _ = await client.send_message("Hi", session_key="sk-ok")
        assert content == "normal response"

    @pytest.mark.asyncio
    async def test_send_message_bot_session_error_on_agent_error_event(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message raises BotSessionError when agent event sets state=error."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
            BotSessionError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        async def simulate_agent_error(*args, **kwargs):
            sk = kwargs["session_key"]
            # Simulate agent event with state=error (as per the bug report)
            client._on_agent(
                {
                    "sessionKey": sk,
                    "state": "error",
                    "errorMessage": "CONNECTION_ERROR",
                }
            )

        mock_bot_ws_instance.chat_send.side_effect = simulate_agent_error

        with pytest.raises(BotSessionError, match="session ended with error state"):
            await client.send_message("Hi", session_key="sk-agent-err")

    async def test_send_message_stream_emits_error_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message_stream emits error StreamChunk when ChatRequestError is raised."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = ChatRequestError(
            message="chat.send failed: UNAVAILABLE - Session validation failed",
            error_code="UNAVAILABLE",
            error_message="Session validation failed",
        )

        chunks = []
        async for chunk in client.send_message_stream("Hi", session_key="sk-err"):
            chunks.append(chunk)

        # Should emit exactly one error chunk
        assert len(chunks) == 1
        assert chunks[0].type == "error"
        assert "UNAVAILABLE" in chunks[0].content
        assert "Session validation failed" in chunks[0].content

    @pytest.mark.asyncio
    async def test_send_message_stream_sets_chat_complete_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message_stream sets chat_complete when ChatRequestError is raised."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
            _SessionState,
        )
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        # Pre-register a session state so we can inspect it after the error
        sk = "sk-err"
        state = _SessionState()
        client._sessions[sk] = state

        mock_bot_ws_instance.chat_send.side_effect = ChatRequestError(
            message="chat.send failed: UNAVAILABLE - Session validation failed",
            error_code="UNAVAILABLE",
            error_message="Session validation failed",
        )

        chunks = []
        async for chunk in client.send_message_stream("Hi", session_key=sk):
            chunks.append(chunk)

        # The session gets cleaned up in the finally block, but chat_complete
        # was set before the iterator returned
        assert len(chunks) == 1
        assert chunks[0].type == "error"

    @pytest.mark.asyncio
    async def test_send_message_stream_cleans_up_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message_stream cleans up session state after ChatRequestError."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient,
        )
        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = AsyncChatClient(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = ChatRequestError(
            message="chat.send failed: UNAVAILABLE - Session validation failed",
            error_code="UNAVAILABLE",
            error_message="Session validation failed",
        )

        chunks = []
        async for chunk in client.send_message_stream("Hi", session_key="sk-err"):
            chunks.append(chunk)

        # Session should be cleaned up
        assert "sk-err" not in client._sessions
        assert "sk-err" not in client._active_sessions
