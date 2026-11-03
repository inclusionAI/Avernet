"""test async chat client; focused regression tests and fixtures."""

import asyncio

import pytest

from ._async_chat_test_support import (
    _setup_session_state,
)
from ._async_chat_test_support import (
    mock_bot_ws as mock_bot_ws,
)
from ._async_chat_test_support import (
    mock_bot_ws_instance as mock_bot_ws_instance,
)


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
            return {"ok": True}

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
            return {"ok": True}

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
            return {"ok": True}

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
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = fire_chat_complete

        content, agent_payloads = await client.send_message("Hi", session_key=sk)
        assert content == "new content"
        assert agent_payloads == []
        assert sk not in client._sessions


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
