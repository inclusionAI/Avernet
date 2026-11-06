"""test async chat errors; focused regression tests and fixtures."""

import pytest

from tests.utils.chat_transport import make_chat_client

from ._async_chat_test_support import (
    mock_bot_ws as mock_bot_ws,
)
from ._async_chat_test_support import (
    mock_bot_ws_instance as mock_bot_ws_instance,
)


class TestChatRequestErrorPropagation:
    """Tests for ChatRequestError handling in AsyncChatClient."""

    @pytest.mark.asyncio
    async def test_send_message_propagates_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message propagates ChatRequestError from chat_send and sets chat_complete."""

        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = lambda *a, **kw: {
            "ok": False,
            "error": {
                "code": "UNAVAILABLE",
                "message": "Session validation failed",
                "retryable": True,
            },
        }

        with pytest.raises(ChatRequestError, match="chat.send failed"):
            await client.send_message("Hi", session_key="sk-err")

    @pytest.mark.asyncio
    async def test_send_message_sets_chat_complete_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message sets chat_complete on ChatRequestError to unblock waiters."""

        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        error_obj = ChatRequestError(
            message="chat.send failed: UNAVAILABLE - Session validation failed",
            error_code="UNAVAILABLE",
            error_message="Session validation failed",
        )

        # Capture session state before the error to verify chat_complete is set
        captured_state = None

        async def chat_send_with_state_capture(*args, **kwargs):
            state = client._sessions.get(kwargs.get("session_key"))
            nonlocal captured_state
            captured_state = state
            return {
                "ok": False,
                "error": {
                    "code": "UNAVAILABLE",
                    "message": "Session validation failed",
                },
            }

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

        from secbaas.community.core.service.bot_run._bot_websocket_client import (
            ChatRequestError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = lambda *a, **kw: {
            "ok": False,
            "error": {
                "code": "UNAVAILABLE",
                "message": "Session validation failed",
            },
        }

        with pytest.raises(ChatRequestError, match="chat.send failed"):
            await client.send_message("Hi", session_key="sk-err")

        # Session should be cleaned up in the finally block
        assert "sk-err" not in client._sessions
        assert "sk-err" not in client._active_sessions


class TestChatRequestErrorStream:
    """Tests for ChatRequestError handling in send_message_stream."""

    @pytest.mark.asyncio
    async def test_send_message_stream_emits_error_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message_stream emits error StreamChunk when ChatRequestError is raised."""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = lambda *a, **kw: {
            "ok": False,
            "error": {
                "code": "UNAVAILABLE",
                "message": "Session validation failed",
            },
        }

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
            SessionState,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        # Pre-register a session state so we can inspect it after the error
        sk = "sk-err"
        state = SessionState()
        client._sessions[sk] = state

        mock_bot_ws_instance.chat_send.side_effect = lambda *a, **kw: {
            "ok": False,
            "error": {
                "code": "UNAVAILABLE",
                "message": "Session validation failed",
            },
        }

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

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = lambda *a, **kw: {
            "ok": False,
            "error": {
                "code": "UNAVAILABLE",
                "message": "Session validation failed",
            },
        }

        chunks = []
        async for chunk in client.send_message_stream("Hi", session_key="sk-err"):
            chunks.append(chunk)

        # Session should be cleaned up
        assert "sk-err" not in client._sessions
        assert "sk-err" not in client._active_sessions


class TestBotSessionError:
    """Tests for BotSessionError: send_message raises it when state='error'."""

    @pytest.mark.asyncio
    async def test_send_message_raises_bot_session_error_on_error_state(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message raises BotSessionError when session ends with state=error."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            BotSessionError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        async def fire_error_chat_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.state = "error"
                state.content = "CONNECTION_ERROR"
                state.chat_complete.set()
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = fire_error_chat_complete

        with pytest.raises(BotSessionError, match="session ended with error state"):
            await client.send_message("Hi", session_key="sk-err")

    @pytest.mark.asyncio
    async def test_send_message_error_detail_propagates_from_engine(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """引擎 chat error 事件的 errorCode/errorMessage 透传进 BotSessionError。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            BotSessionError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        async def fire_engine_error_event(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                client._on_chat(
                    {
                        "sessionKey": sk,
                        "state": "error",
                        "errorCode": "SESSION_CWD_CONFLICT",
                        "errorMessage": (
                            "isolated session cwd /home/admin/.aicoding/workspace/x "
                            "already exists and is not owned by this session"
                        ),
                    }
                )
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = fire_engine_error_event

        with pytest.raises(BotSessionError) as exc_info:
            await client.send_message("Hi", session_key="sk-err")

        assert "SESSION_CWD_CONFLICT" in str(exc_info.value)
        assert "isolated session cwd" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_message_returns_normally_on_final_state(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message returns content normally when state=final (not error)."""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        async def fire_final_chat_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.state = "final"
                state.content = "normal response"
                state.chat_complete.set()
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = fire_final_chat_complete

        content, _ = await client.send_message("Hi", session_key="sk-ok")
        assert content == "normal response"

    @pytest.mark.asyncio
    async def test_send_message_bot_session_error_on_agent_error_event(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message raises BotSessionError when agent event sets state=error."""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            BotSessionError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
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
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = simulate_agent_error

        with pytest.raises(BotSessionError, match="session ended with error state"):
            await client.send_message("Hi", session_key="sk-agent-err")

    async def test_send_message_stream_emits_error_on_chat_request_error(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """send_message_stream emits error StreamChunk when ChatRequestError is raised."""

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = lambda *a, **kw: {
            "ok": False,
            "error": {
                "code": "UNAVAILABLE",
                "message": "Session validation failed",
            },
        }

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
            SessionState,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        # Pre-register a session state so we can inspect it after the error
        sk = "sk-err"
        state = SessionState()
        client._sessions[sk] = state

        mock_bot_ws_instance.chat_send.side_effect = lambda *a, **kw: {
            "ok": False,
            "error": {
                "code": "UNAVAILABLE",
                "message": "Session validation failed",
            },
        }

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

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        mock_bot_ws_instance.chat_send.side_effect = lambda *a, **kw: {
            "ok": False,
            "error": {
                "code": "UNAVAILABLE",
                "message": "Session validation failed",
            },
        }

        chunks = []
        async for chunk in client.send_message_stream("Hi", session_key="sk-err"):
            chunks.append(chunk)

        # Session should be cleaned up
        assert "sk-err" not in client._sessions
        assert "sk-err" not in client._active_sessions
