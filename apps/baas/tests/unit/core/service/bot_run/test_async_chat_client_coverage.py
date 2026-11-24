"""test async chat client coverage; focused regression tests and fixtures."""

import asyncio
from unittest.mock import MagicMock, call, patch

import pytest

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    SessionState,
    _capture_trace_context,
)
from tests.utils.chat_transport import make_chat_client

from ._async_chat_coverage_support import (
    _patch_decorator_test,
    _setup_session_state,
)
from ._async_chat_coverage_support import (
    mock_bot_ws as mock_bot_ws,
)
from ._async_chat_coverage_support import (
    mock_bot_ws_instance as mock_bot_ws_instance,
)


class TestProperties:
    @pytest.mark.asyncio
    async def test_is_connected_false_when_no_client(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_is_connected_true_when_client_connected(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()
        assert client.is_connected is True

    @pytest.mark.asyncio
    async def test_is_connected_false_when_client_disconnected(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        await client.connect()
        mock_bot_ws_instance.connected = False
        assert client.is_connected is False

    def test_is_reconnecting_default_false(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        assert client.is_reconnecting is False

    def test_is_reconnecting_true_when_set(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        client._reconnecting = True
        assert client.is_reconnecting is True

    def test_has_active_sessions_false_default(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        assert client.has_active_sessions is False

    def test_has_active_sessions_true_when_active(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        client._active_sessions.add("s1")
        assert client.has_active_sessions is True

    def test_active_session_count(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        client._active_sessions.add("s1")
        client._active_sessions.add("s2")
        assert client.active_session_count == 2

    @pytest.mark.asyncio
    async def test_connect_registers_mode_transition_resolved_handler(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)

        await client.connect()

        assert (
            call(
                "mode_transition.resolved",
                client._on_mode_transition_resolved,
            )
            in mock_bot_ws_instance.on_event.call_args_list
        )


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


class TestWithSessionTrace:
    def test_decorator_sets_name_and_qualname(self):
        @_patch_decorator_test
        def dummy(self, payload, *, session_key, state):
            pass

        assert dummy.__name__ == "_on_event"
        assert dummy.__qualname__ == "AsyncChatClient._on_event"

    @pytest.mark.asyncio
    async def test_wrapper_with_single_arg_payload(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
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
        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")

        # _log_event is called with (event_name, payload)
        client._log_event("system", {"sessionKey": "sk1", "data": "test"})
        # Should not raise

    @pytest.mark.asyncio
    async def test_wrapper_empty_session_key(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
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

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.trace_context = sentinel_ctx

        with patch(
            "secbaas.community.core.service.bot_run._async_chat_events.get_tracer_plugin",
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


class TestGetSession:
    def test_exact_match(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client, "exact-key")
        result = client._get_session("exact-key")
        assert result is state

    def test_contains_match(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client, "short-key")
        result = client._get_session("prefix:short-key:suffix")
        assert result is state

    def test_no_match(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        result = client._get_session("nonexistent")
        assert result is None

    def test_empty_key(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        result = client._get_session("")
        assert result is None


class TestEmitStreamChunk:
    def test_put_chunk_when_queue_exists(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        state = SessionState()
        state.stream_queue = asyncio.Queue()
        chunk = StreamChunk(type="delta", content="hello")
        AsyncChatClient._emit_stream_chunk(state, chunk)
        assert state.stream_queue.qsize() == 1
        assert state.stream_queue.get_nowait() is chunk

    def test_no_queue_no_error(self, mock_bot_ws):
        client = make_chat_client(uri="ws://host/ws")
        state = SessionState()
        state.stream_queue = None
        # Should not raise
        AsyncChatClient._emit_stream_chunk(
            state, StreamChunk(type="delta", content="x")
        )


class TestHandleTerminalError:
    def test_sets_error_state_and_completes(self, mock_bot_ws):
        state = SessionState()
        AsyncChatClient._handle_terminal_error(state, "sk1", "boom", "agent")
        assert state.state == "error"
        assert state.chat_complete.is_set()

    def test_emits_error_chunk_to_queue(self, mock_bot_ws):
        state = SessionState()
        state.stream_queue = asyncio.Queue()
        AsyncChatClient._handle_terminal_error(state, "sk1", "boom", "agent")
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "error"
        assert chunk.content == "boom"

    def test_empty_error_msg_uses_source(self, mock_bot_ws):
        state = SessionState()
        AsyncChatClient._handle_terminal_error(state, "sk1", "", "agent")
        assert state.state == "error"
        assert state.chat_complete.is_set()

    def test_error_code_prefixes_msg_and_stores_on_state(self, mock_bot_ws):
        """errorCode 前缀进 chunk 与 state.error_message，供 BotSessionError 透传。"""
        state = SessionState()
        state.stream_queue = asyncio.Queue()
        AsyncChatClient._handle_terminal_error(
            state,
            "sk1",
            "cwd already exists",
            "chat",
            error_code="SESSION_CWD_CONFLICT",
        )
        assert state.error_message == "SESSION_CWD_CONFLICT - cwd already exists"
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "error"
        assert chunk.content == "SESSION_CWD_CONFLICT - cwd already exists"

    def test_no_error_code_keeps_plain_msg(self, mock_bot_ws):
        state = SessionState()
        AsyncChatClient._handle_terminal_error(state, "sk1", "boom", "agent")
        assert state.error_message == "boom"
