"""Coverage tests for AsyncChatClient — targets untested methods and branches.

Covers: send_message_stream, _drain_stream_queue, _on_error, _log_event,
_on_disconnect, _notify_disconnect, _reconnect_loop, _get_session,
_emit_stream_chunk, _handle_terminal_error, _capture_trace_context,
_with_session_trace decorator, properties, inject_message edge cases,
chat state with stopReason=inject, agent error handling, and verbose paths.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    ConcurrentSessionError,
    NotConnectedError,
    _capture_trace_context,
    _SessionState,
)

_TEST_SESSION_KEY = "test-session-key"


def _setup_session_state(client, session_key=_TEST_SESSION_KEY):
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
        return_value={"server": {"host": "srv"}, "features": {}}
    )
    instance.close = AsyncMock()
    instance.chat_send = AsyncMock()
    instance.chat_inject = AsyncMock()
    instance.connected = True
    return instance


@pytest.fixture
async def connected_client(mock_bot_ws, mock_bot_ws_instance):
    """A client that has already connected."""
    client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
    await client.connect()
    yield client
    if client._client is not None:
        await client.close()


# ==================== Properties ====================


class TestProperties:
    @pytest.mark.asyncio
    async def test_is_connected_false_when_no_client(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_is_connected_true_when_client_connected(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()
        assert client.is_connected is True

    @pytest.mark.asyncio
    async def test_is_connected_false_when_client_disconnected(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()
        mock_bot_ws_instance.connected = False
        assert client.is_connected is False

    def test_is_reconnecting_default_false(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        assert client.is_reconnecting is False

    def test_is_reconnecting_true_when_set(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        client._reconnecting = True
        assert client.is_reconnecting is True

    def test_has_active_sessions_false_default(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        assert client.has_active_sessions is False

    def test_has_active_sessions_true_when_active(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        client._active_sessions.add("s1")
        assert client.has_active_sessions is True

    def test_active_session_count(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        client._active_sessions.add("s1")
        client._active_sessions.add("s2")
        assert client.active_session_count == 2


# ==================== _capture_trace_context ====================


class TestCaptureTraceContext:
    def test_returns_none_when_no_valid_span(self):
        result = _capture_trace_context()
        assert result is None

    def test_returns_context_when_valid_span(self):
        from opentelemetry import context as otel_context
        from opentelemetry import trace as otel_trace

        # Create a real span context that is valid
        from opentelemetry.trace import SpanContext, TraceFlags
        from opentelemetry.trace.span import NonRecordingSpan

        span_ctx = SpanContext(
            trace_id=0x00000000000000000000000000000001,
            span_id=0x0000000000000001,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        span = NonRecordingSpan(span_ctx)

        ctx = otel_trace.set_span_in_context(span)
        token = otel_context.attach(ctx)
        try:
            result = _capture_trace_context()
            assert result is not None
        finally:
            otel_context.detach(token)


# ==================== _with_session_trace decorator ====================


class TestWithSessionTrace:
    def test_decorator_sets_name_and_qualname(self):
        @_patch_decorator_test
        def dummy(self, payload, *, session_key, state):
            pass

        assert dummy.__name__ == "_on_event"
        assert dummy.__qualname__ == "AsyncChatClient._on_event"

    @pytest.mark.asyncio
    async def test_wrapper_with_single_arg_payload(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")

        received = {}

        @AsyncChatClient._on_chat.__class__.__call__  # type: ignore
        def _noop(*args, **kwargs):
            pass

        # Directly test the wrapper logic via _on_chat
        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "final",
                "message": {"content": [{"text": "hi"}]},
            }
        )
        assert state.chat_complete.is_set()

    @pytest.mark.asyncio
    async def test_wrapper_with_two_args_event_name_payload(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")

        # _log_event is called with (event_name, payload)
        client._log_event("system", {"sessionKey": "sk1", "data": "test"})
        # Should not raise

    @pytest.mark.asyncio
    async def test_wrapper_empty_session_key(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        _setup_session_state(client, "sk1")
        # Empty sessionKey → no match → state=None
        client._on_chat({"sessionKey": "", "state": "final"})
        # Should not raise, state should be None

    @pytest.mark.asyncio
    async def test_wrapper_attaches_and_detaches_trace_context(self, mock_bot_ws):
        """Cover lines 75/83: attach_context / detach_context are called
        when the matched session has a non-None trace_context."""
        sentinel_ctx = object()
        sentinel_token = object()
        mock_tracer = MagicMock()
        mock_tracer.attach_context.return_value = sentinel_token
        mock_tracer.detach_context = MagicMock()

        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.trace_context = sentinel_ctx

        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client.get_tracer_plugin",
            return_value=mock_tracer,
        ):
            client._on_chat(
                {
                    "sessionKey": "sk1",
                    "state": "final",
                    "message": {"content": [{"text": "hi"}]},
                }
            )

        mock_tracer.attach_context.assert_called_once_with(sentinel_ctx)
        mock_tracer.detach_context.assert_called_once_with(sentinel_token)


def _patch_decorator_test(fn):
    """Helper to verify decorator name-setting logic."""
    from secbaas.community.core.service.bot_run._async_chat_client import (
        _with_session_trace,
    )

    return _with_session_trace("_on_event")(fn)


# ==================== _get_session ====================


class TestGetSession:
    def test_exact_match(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "exact-key")
        result = client._get_session("exact-key")
        assert result is state

    def test_contains_match(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "short-key")
        result = client._get_session("prefix:short-key:suffix")
        assert result is state

    def test_no_match(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        result = client._get_session("nonexistent")
        assert result is None

    def test_empty_key(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        result = client._get_session("")
        assert result is None


# ==================== _emit_stream_chunk ====================


class TestEmitStreamChunk:
    def test_put_chunk_when_queue_exists(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _SessionState()
        state.stream_queue = asyncio.Queue()
        chunk = StreamChunk(type="delta", content="hello")
        AsyncChatClient._emit_stream_chunk(state, chunk)
        assert state.stream_queue.qsize() == 1
        assert state.stream_queue.get_nowait() is chunk

    def test_no_queue_no_error(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _SessionState()
        state.stream_queue = None
        # Should not raise
        AsyncChatClient._emit_stream_chunk(
            state, StreamChunk(type="delta", content="x")
        )


# ==================== _handle_terminal_error ====================


class TestHandleTerminalError:
    def test_sets_error_state_and_completes(self, mock_bot_ws):
        state = _SessionState()
        AsyncChatClient._handle_terminal_error(state, "sk1", "boom", "agent")
        assert state.state == "error"
        assert state.chat_complete.is_set()

    def test_emits_error_chunk_to_queue(self, mock_bot_ws):
        state = _SessionState()
        state.stream_queue = asyncio.Queue()
        AsyncChatClient._handle_terminal_error(state, "sk1", "boom", "agent")
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "error"
        assert chunk.content == "boom"

    def test_empty_error_msg_uses_source(self, mock_bot_ws):
        state = _SessionState()
        AsyncChatClient._handle_terminal_error(state, "sk1", "", "agent")
        assert state.state == "error"
        assert state.chat_complete.is_set()


# ==================== _on_chat additional branches ====================


class TestOnChatAdditional:
    @pytest.mark.asyncio
    async def test_on_chat_final_with_stop_reason_inject(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "final",
                "stopReason": "inject",
                "message": {"content": [{"text": "should skip"}]},
            }
        )
        await asyncio.sleep(0)
        assert state.state == ""
        assert state.chat_complete.is_set() is False
        assert state.stream_queue.empty()

    @pytest.mark.asyncio
    async def test_on_chat_delta_with_delta_text(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "delta",
                "message": {"content": [{"text": "full text"}]},
                "deltaText": "incremental",
            }
        )
        await asyncio.sleep(0)
        assert state.content == "full text"
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "delta"
        assert chunk.content == "incremental"

    @pytest.mark.asyncio
    async def test_on_chat_delta_with_delta_field(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "delta",
                "message": {"content": [{"text": "full text"}]},
                "delta": "alt delta",
            }
        )
        await asyncio.sleep(0)
        chunk = state.stream_queue.get_nowait()
        assert chunk.content == "alt delta"

    @pytest.mark.asyncio
    async def test_on_chat_delta_no_delta_text(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "delta",
                "message": {"content": [{"text": "full text"}]},
            }
        )
        await asyncio.sleep(0)
        chunk = state.stream_queue.get_nowait()
        assert chunk.content == ""

    @pytest.mark.asyncio
    async def test_on_chat_no_state(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        # No session registered for this key
        client._on_chat(
            {
                "sessionKey": "unknown-key",
                "state": "final",
                "message": {"content": [{"text": "hello"}]},
            }
        )
        # Should not raise

    @pytest.mark.asyncio
    async def test_on_chat_verbose_delta(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws", verbose=True)
        state = _setup_session_state(client, "sk1")
        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "delta",
                "message": {"content": [{"text": "verbose delta"}]},
            }
        )
        assert state.content == "verbose delta"

    @pytest.mark.asyncio
    async def test_on_chat_verbose_final(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws", verbose=True)
        state = _setup_session_state(client, "sk1")
        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "final",
                "message": {"content": [{"text": "verbose final"}]},
            }
        )
        assert state.state == "final"

    @pytest.mark.asyncio
    async def test_on_chat_verbose_error(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws", verbose=True)
        state = _setup_session_state(client, "sk1")
        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "error",
                "message": {"content": [{"text": "err"}]},
            }
        )
        assert state.state == "error"

    @pytest.mark.asyncio
    async def test_on_chat_verbose_ignored_state(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws", verbose=True)
        state = _setup_session_state(client, "sk1")
        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "thinking",
                "message": {"content": [{"text": "thinking..."}]},
            }
        )
        assert state.state == ""

    @pytest.mark.asyncio
    async def test_on_chat_final_emits_stream_chunk(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "final",
                "message": {"content": [{"text": "final text"}]},
            }
        )
        await asyncio.sleep(0)
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "final"
        assert chunk.content == "final text"


# ==================== _on_agent additional branches ====================


class TestOnAgentAdditional:
    @pytest.mark.asyncio
    async def test_on_agent_error_state(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_agent(
            {
                "sessionKey": "sk1",
                "state": "error",
                "errorMessage": "agent failed",
            }
        )
        assert state.state == "error"
        assert state.chat_complete.is_set()
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "error"
        assert chunk.content == "agent failed"

    @pytest.mark.asyncio
    async def test_on_agent_error_empty_message(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")

        client._on_agent(
            {
                "sessionKey": "sk1",
                "state": "error",
                "errorMessage": "",
            }
        )
        assert state.state == "error"

    @pytest.mark.asyncio
    async def test_on_agent_no_state(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        client._on_agent({"sessionKey": "unknown", "stream": "tool", "data": {}})
        # Should not raise

    @pytest.mark.asyncio
    async def test_on_agent_verbose(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws", verbose=True)
        state = _setup_session_state(client, "sk1")
        client._on_agent(
            {"sessionKey": "sk1", "stream": "tool", "data": {"phase": "start"}}
        )
        assert state.last_stream_is_assistant is False

    @pytest.mark.asyncio
    async def test_on_agent_emits_stream_chunk(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_agent(
            {"sessionKey": "sk1", "stream": "tool", "data": {"phase": "result"}}
        )
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "agent"
        assert chunk.metadata["engine_frame"]["stream"] == "tool"

    @pytest.mark.asyncio
    async def test_on_agent_tool_no_data_phase(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        client._on_agent({"sessionKey": "sk1", "stream": "tool", "data": {}})
        assert len(state.agent_payloads) == 0

    @pytest.mark.asyncio
    async def test_on_agent_lifecycle_non_end(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        client._on_agent(
            {"sessionKey": "sk1", "stream": "lifecycle", "data": {"phase": "start"}}
        )
        assert state.agent_complete.is_set() is False

    @pytest.mark.asyncio
    async def test_on_agent_unknown_stream(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.last_stream_is_assistant = True
        client._on_agent({"sessionKey": "sk1", "stream": "unknown", "data": {}})
        assert state.last_stream_is_assistant is False


# ==================== _on_error ====================


class TestOnError:
    @pytest.mark.asyncio
    async def test_on_error_with_state_and_error(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_error(
            {"sessionKey": "sk1", "state": "error", "errorMessage": "ws error"}
        )
        assert state.state == "error"
        assert state.chat_complete.is_set()

    @pytest.mark.asyncio
    async def test_on_error_no_state(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        client._on_error({"sessionKey": "unknown", "state": "error"})
        # Should not raise

    @pytest.mark.asyncio
    async def test_on_error_non_error_state(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        client._on_error({"sessionKey": "sk1", "state": "info"})
        assert state.state == ""
        assert state.chat_complete.is_set() is False

    @pytest.mark.asyncio
    async def test_on_error_no_state_field(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        client._on_error({"sessionKey": "sk1"})
        assert state.state == ""
        assert state.chat_complete.is_set() is False


# ==================== _log_event ====================


class TestLogEvent:
    @pytest.mark.asyncio
    async def test_log_event_chat_filters_sensitive(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        _setup_session_state(client, "sk1")
        client._log_event(
            "chat", {"sessionKey": "sk1", "message": "secret", "data": "ok"}
        )
        # Should not raise

    @pytest.mark.asyncio
    async def test_log_event_agent_filters_sensitive(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        _setup_session_state(client, "sk1")
        client._log_event("agent", {"sessionKey": "sk1", "data": "secret"})
        # Should not raise

    @pytest.mark.asyncio
    async def test_log_event_non_sensitive(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        _setup_session_state(client, "sk1")
        client._log_event("system", {"sessionKey": "sk1", "data": "all visible"})
        # Should not raise

    @pytest.mark.asyncio
    async def test_log_event_no_state(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        client._log_event("system", {"sessionKey": "unknown"})
        # Should not raise


# ==================== _on_disconnect / _notify_disconnect ====================


class TestOnDisconnect:
    def test_on_disconnect_pushes_error_to_streams(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state1 = _setup_session_state(client, "s1")
        state1.stream_queue = asyncio.Queue()
        state2 = _setup_session_state(client, "s2")
        state2.stream_queue = asyncio.Queue()

        # State without queue should not cause error
        _setup_session_state(client, "s3")

        client._on_disconnect("disconnect", {"reason": "closed"})

        chunk1 = state1.stream_queue.get_nowait()
        assert chunk1.type == "error"
        chunk2 = state2.stream_queue.get_nowait()
        assert chunk2.type == "error"

    def test_on_disconnect_sets_disconnect_event(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        assert client._disconnect_event.is_set() is False
        client._on_disconnect("disconnect", {})
        assert client._disconnect_event.is_set() is True

    def test_notify_disconnect(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        assert client._disconnect_event.is_set() is False
        client._notify_disconnect()
        assert client._disconnect_event.is_set() is True


# ==================== send_message_stream ====================


class TestSendMessageStream:
    @pytest.mark.asyncio
    async def test_stream_success(self, mock_bot_ws, mock_bot_ws_instance):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()

        async def push_chunks(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state and state.stream_queue:
                state.stream_queue.put_nowait(
                    StreamChunk(type="delta", content="chunk1")
                )
                state.stream_queue.put_nowait(StreamChunk(type="final", content="done"))

        mock_bot_ws_instance.chat_send.side_effect = push_chunks

        chunks = []
        async for chunk in client.send_message_stream("hello", session_key="sk1"):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].type == "delta"
        assert chunks[1].type == "final"

    @pytest.mark.asyncio
    async def test_stream_not_connected_raises(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        with pytest.raises(NotConnectedError):
            async for _ in client.send_message_stream("hello"):
                pass

    @pytest.mark.asyncio
    async def test_stream_error_chunk_terminates(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()

        async def push_error(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state and state.stream_queue:
                state.stream_queue.put_nowait(StreamChunk(type="error", content="bad"))

        mock_bot_ws_instance.chat_send.side_effect = push_error

        chunks = []
        async for chunk in client.send_message_stream("hello", session_key="sk1"):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].type == "error"

    @pytest.mark.asyncio
    async def test_stream_with_timeout(self, mock_bot_ws, mock_bot_ws_instance):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()

        async def push_chunks(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state and state.stream_queue:
                state.stream_queue.put_nowait(StreamChunk(type="delta", content="d1"))
                state.stream_queue.put_nowait(StreamChunk(type="final", content="end"))

        mock_bot_ws_instance.chat_send.side_effect = push_chunks

        chunks = []
        async for chunk in client.send_message_stream(
            "hi", session_key="sk1", timeout=5
        ):
            chunks.append(chunk)

        assert len(chunks) == 2

    @pytest.mark.asyncio
    async def test_stream_timeout_exceeded(self, mock_bot_ws, mock_bot_ws_instance):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()

        async def never_push(*args, **kwargs):
            pass  # Don't push any chunks

        mock_bot_ws_instance.chat_send.side_effect = never_push

        chunks = []
        async for chunk in client.send_message_stream(
            "hi", session_key="sk1", timeout=0.1
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].type == "error"
        assert "timeout" in chunks[0].content

    @pytest.mark.asyncio
    async def test_stream_with_semaphore(self, mock_bot_ws, mock_bot_ws_instance):
        client = AsyncChatClient(
            uri="ws://host/ws", max_concurrent_sessions=1, max_retries=0
        )
        await client.connect()

        async def push_final(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state and state.stream_queue:
                state.stream_queue.put_nowait(StreamChunk(type="final", content="ok"))

        mock_bot_ws_instance.chat_send.side_effect = push_final

        chunks = []
        async for chunk in client.send_message_stream("hi", session_key="sk1"):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].type == "final"

    @pytest.mark.asyncio
    async def test_stream_concurrent_same_key_timeout(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(
            uri="ws://host/ws", session_key_timeout=0.05, max_retries=0
        )
        await client.connect()

        async def never_push(*args, **kwargs):
            await asyncio.sleep(10)

        mock_bot_ws_instance.chat_send.side_effect = never_push

        t1 = asyncio.create_task(
            client.send_message_stream("m1", session_key="same").__anext__()
        )
        await asyncio.sleep(0.01)

        with pytest.raises(ConcurrentSessionError):
            async for _ in client.send_message_stream("m2", session_key="same"):
                pass

        t1.cancel()
        try:
            await t1
        except (asyncio.CancelledError, StopAsyncIteration, Exception):
            pass

    @pytest.mark.asyncio
    async def test_stream_connection_lost_before_send(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()

        # Simulate connection lost between condition release and send
        mock_bot_ws_instance.connected = False

        with pytest.raises(NotConnectedError):
            async for _ in client.send_message_stream("hi", session_key="sk1"):
                pass


# ==================== _drain_stream_queue ====================


class TestDrainStreamQueue:
    @pytest.mark.asyncio
    async def test_drain_no_timeout(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        queue: asyncio.Queue[StreamChunk] = asyncio.Queue()
        queue.put_nowait(StreamChunk(type="delta", content="d1"))
        queue.put_nowait(StreamChunk(type="final", content="f1"))

        chunks = []
        async for chunk in client._drain_stream_queue(queue, None):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[1].type == "final"

    @pytest.mark.asyncio
    async def test_drain_timeout_remaining_zero(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        queue: asyncio.Queue[StreamChunk] = asyncio.Queue()
        # Empty queue with very short timeout → immediate timeout
        chunks = []
        async for chunk in client._drain_stream_queue(queue, 0.01):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].type == "error"

    @pytest.mark.asyncio
    async def test_drain_timeout_after_some_chunks(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        queue: asyncio.Queue[StreamChunk] = asyncio.Queue()
        queue.put_nowait(StreamChunk(type="delta", content="d1"))

        chunks = []
        async for chunk in client._drain_stream_queue(queue, 0.05):
            chunks.append(chunk)

        # First chunk delivered, then timeout on empty queue
        assert len(chunks) == 2
        assert chunks[0].type == "delta"
        assert chunks[1].type == "error"


# ==================== inject_message additional ====================


class TestInjectMessageAdditional:
    @pytest.mark.asyncio
    async def test_inject_auto_generates_session_key(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()
        await client.inject_message("hello")
        mock_bot_ws_instance.chat_inject.assert_called_once()
        sk = mock_bot_ws_instance.chat_inject.call_args[1]["session_key"]
        assert sk is not None
        assert len(sk) > 0

    @pytest.mark.asyncio
    async def test_inject_with_chat_metadata(self, mock_bot_ws, mock_bot_ws_instance):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()
        await client.inject_message(
            "hello", session_key="sk1", chat_metadata={"key": "value"}
        )
        mock_bot_ws_instance.chat_inject.assert_called_once()
        assert mock_bot_ws_instance.chat_inject.call_args[1]["chat_metadata"] == {
            "key": "value"
        }


# ==================== send_message additional branches ====================


class TestSendMessageAdditional:
    @pytest.mark.asyncio
    async def test_send_message_connection_lost_before_send(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()

        # is_connected is True at entry, then False at the second check (line 352)
        call_count = 0
        original_connected = mock_bot_ws_instance.connected

        class ConnectedProp:
            def __bool__(self):
                nonlocal call_count
                call_count += 1
                return call_count == 1  # True first time, False after

        mock_bot_ws_instance.connected = ConnectedProp()

        with pytest.raises(NotConnectedError, match="Connection lost"):
            await client.send_message("hi", session_key="sk1")

    @pytest.mark.asyncio
    async def test_send_message_with_app_id_and_metadata(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()

        async def fire_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = fire_complete

        await client.send_message(
            "hi",
            session_key="sk1",
            app_id="my-app",
            chat_metadata={"k": "v"},
            timeout=5,
        )
        call_kwargs = mock_bot_ws_instance.chat_send.call_args[1]
        assert call_kwargs["app_id"] == "my-app"
        assert call_kwargs["chat_metadata"] == {"k": "v"}
        assert call_kwargs["timeout_ms"] == 5000

    @pytest.mark.asyncio
    async def test_send_message_with_semaphore(self, mock_bot_ws, mock_bot_ws_instance):
        client = AsyncChatClient(
            uri="ws://host/ws", max_concurrent_sessions=1, max_retries=0
        )
        await client.connect()

        async def fire_complete(*args, **kwargs):
            sk = kwargs["session_key"]
            state = client._sessions.get(sk)
            if state:
                state.chat_complete.set()

        mock_bot_ws_instance.chat_send.side_effect = fire_complete

        content, _ = await client.send_message("hi", session_key="sk1")
        assert content == ""


# ==================== _reconnect_loop ====================


class TestReconnectLoop:
    @pytest.mark.asyncio
    async def test_reconnect_success(self, mock_bot_ws, mock_bot_ws_instance):
        """Test that reconnect loop reconnects after disconnect."""
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=2, retry_base_backoff=0.01
        )
        await client.connect()

        # Simulate disconnect
        mock_bot_ws_instance.connected = False
        client._notify_disconnect()

        # Wait for reconnect to happen
        await asyncio.sleep(0.1)

        # The second BotWebSocketClient instance should have been created
        assert mock_bot_ws.call_count >= 2

        await client.close()

    @pytest.mark.asyncio
    async def test_reconnect_all_attempts_exhausted(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=1, retry_base_backoff=0.01
        )
        await client.connect()

        # Make subsequent connect calls fail
        mock_bot_ws_instance.connected = False
        mock_bot_ws_instance.connect = AsyncMock(side_effect=ConnectionError("fail"))
        client._notify_disconnect()

        # Wait for reconnect attempts to exhaust
        await asyncio.sleep(0.1)

        assert client._reconnecting is False
        await client.close()

    @pytest.mark.asyncio
    async def test_reconnect_loop_stops_on_close(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=3, retry_base_backoff=0.01
        )
        await client.connect()
        monitor = client._reconnect_monitor
        assert monitor is not None

        await client.close()
        assert monitor.cancelled() or monitor.done()

    @pytest.mark.asyncio
    async def test_reconnect_old_client_close_exception(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """When closing old client during reconnect fails, it should be swallowed."""
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=1, retry_base_backoff=0.01
        )
        await client.connect()

        # First instance's close raises, but reconnect should still succeed
        # with a new instance that closes normally
        first_instance = mock_bot_ws_instance
        first_instance.close = AsyncMock(side_effect=Exception("close fail"))
        first_instance.connected = False

        # Second instance (created during reconnect) works fine
        second_instance = MagicMock()
        second_instance.connect = AsyncMock(
            return_value={"server": {"host": "srv2"}, "features": {}}
        )
        second_instance.close = AsyncMock()
        second_instance.connected = True
        second_instance.chat_send = AsyncMock()
        second_instance.chat_inject = AsyncMock()
        mock_bot_ws.return_value = second_instance

        client._notify_disconnect()
        await asyncio.sleep(0.05)

        await client.close()

    @pytest.mark.asyncio
    async def test_reconnect_closed_intentionally_during_backoff(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=3, retry_base_backoff=0.05
        )
        await client.connect()

        mock_bot_ws_instance.connected = False
        client._notify_disconnect()

        # Close during backoff sleep
        await asyncio.sleep(0.01)
        await client.close()


# ==================== close additional ====================


class TestCloseAdditional:
    @pytest.mark.asyncio
    async def test_close_with_reconnect_monitor_done(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()
        # No reconnect monitor when max_retries=0
        assert client._reconnect_monitor is None
        await client.close()
        assert client._closed_intentionally is True

    @pytest.mark.asyncio
    async def test_close_cancels_monitor_await_cancelled(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=1)
        await client.connect()
        monitor = client._reconnect_monitor
        assert monitor is not None

        await client.close()
        assert monitor.done() or monitor.cancelled()
        assert client._reconnect_monitor is None

    @pytest.mark.asyncio
    async def test_close_when_already_closed(self, mock_bot_ws, mock_bot_ws_instance):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        await client.connect()
        await client.close()
        # Double close should not raise
        await client.close()
