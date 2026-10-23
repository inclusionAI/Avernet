"""Coverage tests for AsyncChatClient — targets untested methods and branches.

Covers: send_message_stream, _drain_stream_queue, _on_error, _log_event,
_on_disconnect, _notify_disconnect, _reconnect_loop, _get_session,
_emit_stream_chunk, _handle_terminal_error, _capture_trace_context,
_with_session_trace decorator, properties, inject_message edge cases,
chat state with stopReason=inject, agent error handling, and verbose paths.
"""

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from secbaas.community.api.bot_interaction import (
    InteractionRequestedResult,
    InteractionResolution,
    InteractionResolvedResult,
)
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.bot_interaction import InteractionDispatch
from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    BotSessionError,
    ConcurrentSessionError,
    NotConnectedError,
    SessionState,
    _capture_trace_context,
    _public_interaction_envelope,
)
from secbaas.community.core.service.bot_run._interaction_protocol import (
    EngineInteractionResolveExchange,
)
from secbaas.community.core.service.sse import DefaultStreamConverter

_TEST_SESSION_KEY = "test-session-key"


def _setup_session_state(client, session_key=_TEST_SESSION_KEY):
    return client._sessions.setdefault(session_key, SessionState())


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
    instance.chat_send = AsyncMock(return_value={"ok": True})
    instance.chat_inject = AsyncMock(return_value={"ok": True})
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

    @pytest.mark.asyncio
    async def test_connect_registers_mode_transition_resolved_handler(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)

        await client.connect()

        assert (
            call(
                "mode_transition.resolved",
                client._on_mode_transition_resolved,
            )
            in mock_bot_ws_instance.on_event.call_args_list
        )


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
        state = SessionState()
        state.stream_queue = asyncio.Queue()
        chunk = StreamChunk(type="delta", content="hello")
        AsyncChatClient._emit_stream_chunk(state, chunk)
        assert state.stream_queue.qsize() == 1
        assert state.stream_queue.get_nowait() is chunk

    def test_no_queue_no_error(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = SessionState()
        state.stream_queue = None
        # Should not raise
        AsyncChatClient._emit_stream_chunk(
            state, StreamChunk(type="delta", content="x")
        )


# ==================== _handle_terminal_error ====================


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
                "errorMessage": "some error",
            }
        )
        assert state.state == "error"
        assert state.chat_complete.is_set() is True

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

    @pytest.mark.asyncio
    async def test_on_chat_picks_first_text_content(self, mock_bot_ws):
        """When contents has multiple elements, _on_chat uses the first
        one whose ``text`` field is not None (loop breaks on first hit)."""
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "final",
                "message": {
                    "content": [
                        {"text": "first"},
                        {"text": "second"},
                    ]
                },
            }
        )
        await asyncio.sleep(0)
        chunk = state.stream_queue.get_nowait()
        assert chunk.content == "first"
        assert state.content == "first"

    @pytest.mark.asyncio
    async def test_on_chat_skips_content_without_text(self, mock_bot_ws):
        """Content elements without a ``text`` field are skipped; the
        first element with ``text`` is used."""
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "final",
                "message": {
                    "content": [
                        {"type": "image", "url": "http://x/y.png"},
                        {"text": "real text"},
                    ]
                },
            }
        )
        await asyncio.sleep(0)
        chunk = state.stream_queue.get_nowait()
        assert chunk.content == "real text"

    @pytest.mark.asyncio
    async def test_on_chat_empty_contents_yields_empty_text(self, mock_bot_ws):
        """When message.content is empty or has no text field, text stays ""."""
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_chat(
            {
                "sessionKey": "sk1",
                "state": "final",
                "message": {"content": []},
            }
        )
        await asyncio.sleep(0)
        chunk = state.stream_queue.get_nowait()
        assert chunk.content == ""
        assert state.content == ""


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


# ==================== _on_agent final state ====================


class TestOnAgentFinal:
    @pytest.mark.asyncio
    async def test_on_agent_final_sets_complete_events(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_agent(
            {
                "sessionKey": "sk1",
                "state": "final",
                "message": {
                    "content": [{"type": "text", "text": "agent done"}],
                },
            }
        )
        assert state.state == "final"
        assert state.agent_complete.is_set()
        assert state.chat_complete.is_set()
        assert state.content == "agent done"
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "final"
        assert chunk.content == "agent done"

    @pytest.mark.asyncio
    async def test_on_agent_final_empty_content(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_agent(
            {
                "sessionKey": "sk1",
                "state": "final",
                "message": {},
            }
        )
        assert state.state == "final"
        assert state.agent_complete.is_set()
        assert state.chat_complete.is_set()
        assert state.content == ""
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "final"
        assert chunk.content == ""

    @pytest.mark.asyncio
    async def test_on_agent_final_verbose(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws", verbose=True)
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_agent(
            {
                "sessionKey": "sk1",
                "state": "final",
                "stream": "lifecycle",
                "message": {
                    "content": [{"type": "text", "text": "final text"}],
                },
            }
        )
        assert state.state == "final"
        assert state.agent_complete.is_set()
        assert state.chat_complete.is_set()
        assert state.content == "final text"


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
    async def test_log_event_interaction_resolved_keeps_only_metadata(
        self, mock_bot_ws
    ):
        client = AsyncChatClient(uri="ws://host/ws")
        _setup_session_state(client, "sk1")

        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client.logger"
        ) as mock_logger:
            client._log_event(
                "interaction.resolved",
                {
                    "sessionKey": "sk1",
                    "interactionId": "int-1",
                    "kind": "ask_user",
                    "answers": {"Question?": "answer-must-not-leak"},
                    "selectedOptions": [["answer-must-not-leak"]],
                },
            )

        logged = str(mock_logger.info.call_args)
        assert "interactionId" in logged
        assert "answer-must-not-leak" not in logged

    @pytest.mark.asyncio
    async def test_log_event_mode_transition_resolved_keeps_only_metadata(
        self, mock_bot_ws
    ):
        client = AsyncChatClient(uri="ws://host/ws")
        _setup_session_state(client, "sk1")

        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client.logger"
        ) as mock_logger:
            client._log_event(
                "mode_transition.resolved",
                {
                    "sessionKey": "sk1",
                    "interactionId": "int-mode",
                    "kind": "mode_switch",
                    "phase": "proceeded",
                    "options": [
                        {
                            "label": "private-mode-option-label",
                            "decision": "proceed",
                        }
                    ],
                },
            )

        logged = str(mock_logger.info.call_args)
        assert "interactionId" in logged
        assert "private-mode-option-label" not in logged

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

    @pytest.mark.asyncio
    async def test_log_event_bypass_hits_logs_full_payload(self, mock_bot_ws):
        client = AsyncChatClient(
            uri="ws://host/ws",
            sensitive_bypass_keys_loader=lambda: ("sk1",),
        )
        _setup_session_state(client, "sk1")

        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client.logger"
        ) as mock_logger:
            client._log_event(
                "chat",
                {"sessionKey": "sk1", "message": "secret-content"},
            )

        logged = str(mock_logger.info.call_args)
        assert "secret-content" in logged

    @pytest.mark.asyncio
    async def test_log_event_bypass_miss_filters_sensitive(self, mock_bot_ws):
        client = AsyncChatClient(
            uri="ws://host/ws",
            sensitive_bypass_keys_loader=lambda: ("other-sess",),
        )
        _setup_session_state(client, "sk1")

        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client.logger"
        ) as mock_logger:
            client._log_event(
                "chat",
                {"sessionKey": "sk1", "message": "secret-content"},
            )

        logged = str(mock_logger.info.call_args)
        assert "secret-content" not in logged


# ==================== _on_disconnect / _notify_disconnect ====================


class TestOnDisconnect:
    def test_on_disconnect_pushes_error_to_streams(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        state1 = _setup_session_state(client, "s1")
        state1.stream_queue = asyncio.Queue()
        state2 = _setup_session_state(client, "s2")
        state2.stream_queue = asyncio.Queue()

        # State without queue should not cause error
        state3 = _setup_session_state(client, "s3")

        client._on_disconnect("disconnect", {"reason": "closed"})

        chunk1 = state1.stream_queue.get_nowait()
        assert chunk1.type == "error"
        assert chunk1.content == "connection lost"
        chunk2 = state2.stream_queue.get_nowait()
        assert chunk2.type == "error"
        # 断连时所有在途会话被包装成终态 error（无 queue 的会话仅置状态）
        for state in (state1, state2, state3):
            assert state.state == "error"
            assert state.chat_complete.is_set()

    def test_on_disconnect_skips_completed_sessions(self, mock_bot_ws):
        client = AsyncChatClient(uri="ws://host/ws")
        done = _setup_session_state(client, "done")
        done.state = "final"
        done.content = "ok"
        done.chat_complete.set()
        done.stream_queue = asyncio.Queue()

        client._on_disconnect("disconnect", {})

        # 已完成的会话不被覆盖成 error，也不追加 error chunk
        assert done.state == "final"
        assert done.stream_queue.empty()

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
            return {"ok": True}

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
            return {"ok": True}

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
            return {"ok": True}

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
            return {"ok": True}  # Don't push any chunks

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
            return {"ok": True}

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
            return {"ok": True}

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
            return {"ok": True}

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
            return {"ok": True}

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
        assert (
            mock_bot_ws_instance.on_event.call_args_list.count(
                call(
                    "mode_transition.resolved",
                    client._on_mode_transition_resolved,
                )
            )
            >= 2
        )

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


class TestPublicInteractionEnvelope:
    def test_rejects_non_object_payload(self):
        with pytest.raises(
            ValueError, match="interaction event envelope payload must be an object"
        ):
            _public_interaction_envelope(
                {"type": "event", "payload": "not-an-object"},
                baas_interaction_id="BAAS-INTERACTION-public-1",
            )

    def test_rewrites_legacy_id_without_mutating_engine_envelope(self):
        engine_envelope = {
            "type": "event",
            "event": "interaction.resolved",
            "payload": {
                "sessionKey": "sk1",
                "interactionId": "engine-interaction-1",
                "id": "legacy-engine-interaction-1",
            },
        }

        public_envelope = _public_interaction_envelope(
            engine_envelope,
            baas_interaction_id="BAAS-INTERACTION-public-1",
        )

        assert public_envelope == {
            "type": "event",
            "event": "interaction.resolved",
            "payload": {
                "sessionKey": "sk1",
                "interactionId": "BAAS-INTERACTION-public-1",
                "id": "BAAS-INTERACTION-public-1",
            },
        }
        assert engine_envelope == {
            "type": "event",
            "event": "interaction.resolved",
            "payload": {
                "sessionKey": "sk1",
                "interactionId": "engine-interaction-1",
                "id": "legacy-engine-interaction-1",
            },
        }


class TestInteractionEvents:
    def test_requested_is_ignored_when_interaction_processing_is_disabled(
        self, mock_bot_ws
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_interaction_requested(
            {"sessionKey": "sk1", "interactionId": "int-1"}
        )

        assert state.stream_queue.empty()
        assert client._interaction_tasks == {}

    def test_resolved_is_ignored_when_interaction_processing_is_disabled(
        self, mock_bot_ws
    ):
        client = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_interaction_resolved({"sessionKey": "sk1", "interactionId": "int-1"})

        assert state.stream_queue.empty()

    @pytest.mark.asyncio
    async def test_requested_passes_validated_identity_and_emits_chunk(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.record_requested.return_value = InteractionRequestedResult(
            baas_interaction_id="BAAS-INTERACTION-public-1",
            created=True,
        )
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        payload = {
            "sessionKey": "sk1",
            "interactionId": "int-1",
            "kind": "exec",
            "seq": 42,
            "expiresAtMs": 123456,
            "options": [{"label": "Once", "decision": "allow-once"}],
        }
        client._on_interaction_requested(payload)

        service.record_requested.assert_called_once_with(
            session_key="sk1",
            interaction_id="int-1",
            envelope={
                "type": "event",
                "event": "interaction.requested",
                "payload": payload,
                "seq": 42,
            },
            allowed_decisions=("allow-once",),
            expires_at_ms=123456,
        )
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "interaction"
        assert chunk.metadata["event"] == "interaction.requested"
        assert chunk.metadata["payload"]["event"] == "interaction.requested"
        assert chunk.metadata["payload"]["seq"] == 42
        assert (
            chunk.metadata["payload"]["payload"]["interactionId"]
            == "BAAS-INTERACTION-public-1"
        )
        assert payload["interactionId"] == "int-1"

    @pytest.mark.asyncio
    async def test_requested_mixed_options_reach_persistence_chunk_and_converter(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.record_requested.return_value = InteractionRequestedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mixed",
            created=True,
        )
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()
        payload = {
            "sessionKey": "sk1",
            "interactionId": "int-mixed",
            "kind": "exec",
            "command": "make test",
            "options": [
                "not-an-object",
                {"label": "Once", "decision": "", "value": "allow-once"},
                {"label": "Deny", "decision": 42, "value": "deny"},
                {"label": "Once replacement", "decision": "allow-once"},
                {},
            ],
        }
        original = deepcopy(payload)

        client._on_interaction_requested(payload)

        persisted = service.record_requested.call_args.kwargs
        assert persisted["allowed_decisions"] == ("allow-once", "deny")
        assert persisted["envelope"]["payload"] is payload
        assert payload == original
        chunk = state.stream_queue.get_nowait()
        event = DefaultStreamConverter().convert(chunk, run_id="bcn-run")
        assert event is not None
        data = json.loads(event.data)
        assert data["options"] == [
            {"label": "Once replacement", "decision": "allow-once"},
            {"label": "Deny", "decision": "deny"},
        ]

    @pytest.mark.asyncio
    async def test_duplicate_requested_does_not_emit_duplicate_chunk(self, mock_bot_ws):
        service = MagicMock()
        service.record_requested.side_effect = [
            InteractionRequestedResult(
                baas_interaction_id="BAAS-INTERACTION-public-1",
                created=True,
            ),
            InteractionRequestedResult(
                baas_interaction_id="BAAS-INTERACTION-public-1",
                created=False,
            ),
        ]
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()
        payload = {"sessionKey": "sk1", "interactionId": "int-1"}

        client._on_interaction_requested(payload)
        client._on_interaction_requested(payload)

        assert state.stream_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_requested_persistence_failure_is_not_silently_ignored(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.record_requested.side_effect = RuntimeError("db unavailable")
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        with pytest.raises(RuntimeError, match="db unavailable"):
            client._on_interaction_requested(
                {"sessionKey": "sk1", "interactionId": "int-1"}
            )

        assert state.stream_queue.empty()

    @pytest.mark.asyncio
    async def test_resolved_uses_callback_identity_and_emits_public_chunk(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.mark_resolved.return_value = InteractionResolvedResult(
            baas_interaction_id="BAAS-INTERACTION-public-1",
            applied=True,
        )
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        payload = {
            "sessionKey": "sk1",
            "interactionId": "int-1",
            "decision": "allow-once",
        }
        client._on_interaction_resolved(payload)

        service.mark_resolved.assert_called_once_with(
            session_key="sk1",
            interaction_id="int-1",
            envelope={
                "type": "event",
                "event": "interaction.resolved",
                "payload": payload,
            },
        )
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "interaction"
        assert chunk.metadata["event"] == "interaction.resolved"
        assert chunk.metadata["payload"]["event"] == "interaction.resolved"
        assert (
            chunk.metadata["payload"]["payload"]["interactionId"]
            == "BAAS-INTERACTION-public-1"
        )
        assert payload["interactionId"] == "int-1"

    @pytest.mark.asyncio
    async def test_duplicate_resolved_does_not_emit_duplicate_chunk(self, mock_bot_ws):
        service = MagicMock()
        service.mark_resolved.side_effect = [
            InteractionResolvedResult(
                baas_interaction_id="BAAS-INTERACTION-public-1",
                applied=True,
            ),
            InteractionResolvedResult(
                baas_interaction_id="BAAS-INTERACTION-public-1",
                applied=False,
            ),
        ]
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()
        payload = {"sessionKey": "sk1", "interactionId": "int-1"}

        client._on_interaction_resolved(payload)
        client._on_interaction_resolved(payload)

        assert state.stream_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_mode_transition_resolved_emits_once_after_rpc_terminalized_record(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.record_requested.return_value = InteractionRequestedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mode",
            created=True,
        )
        service.mark_resolved.return_value = InteractionResolvedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mode",
            applied=False,
        )
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()
        requested = {
            "sessionKey": "sk1",
            "interactionId": "int-mode",
            "kind": "mode_switch",
            "options": [
                {"label": "Continue to execution", "decision": "proceed"},
                {"label": "Stay in planning", "decision": "stay"},
            ],
        }
        client._on_interaction_requested(requested)
        requested_chunk = state.stream_queue.get_nowait()
        assert requested_chunk.metadata["event"] == "interaction.requested"
        resolved = {
            "sessionKey": "sk1",
            "interactionId": "int-mode",
            "transitionId": "int-mode",
            "kind": "mode_switch",
            "phase": "proceeded",
            "decision": "proceed",
            "status": "resolved",
            "options": [
                {"label": "Continue to execution", "decision": "proceed"},
                {"label": "Stay in planning", "decision": "stay"},
            ],
            "seq": 131,
        }

        client._on_mode_transition_resolved(resolved)
        client._on_mode_transition_resolved(resolved)

        assert state.stream_queue.qsize() == 1
        chunk = state.stream_queue.get_nowait()
        assert chunk.metadata["event"] == "mode_transition.resolved"
        assert chunk.metadata["payload"] == {
            "type": "event",
            "event": "mode_transition.resolved",
            "payload": {
                **resolved,
                "interactionId": "BAAS-INTERACTION-public-mode",
                "transitionId": "BAAS-INTERACTION-public-mode",
            },
            "seq": 131,
        }
        assert resolved["interactionId"] == "int-mode"
        assert resolved["transitionId"] == "int-mode"
        event = DefaultStreamConverter().convert(chunk, run_id="bcn-run")
        assert event is not None
        data = json.loads(event.data)
        assert isinstance(data.pop("ts"), int)
        assert data == {
            "runId": "bcn-run",
            "seq": 1,
            "interactionId": "BAAS-INTERACTION-public-mode",
            "kind": "mode_switch",
            "phase": "resolved",
            "decision": "proceed",
        }

    def test_mode_transition_resolved_without_exposed_request_does_not_emit(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.mark_resolved.return_value = InteractionResolvedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mode",
            applied=True,
        )
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_mode_transition_resolved(
            {
                "sessionKey": "sk1",
                "interactionId": "int-mode",
                "kind": "mode_switch",
                "decision": "stay",
            }
        )

        assert state.stream_queue.empty()

    def test_mode_transition_requested_without_stream_queue_is_not_exposed(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.record_requested.return_value = InteractionRequestedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mode",
            created=True,
        )
        service.mark_resolved.return_value = InteractionResolvedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mode",
            applied=False,
        )
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")

        client._on_interaction_requested(
            {
                "sessionKey": "sk1",
                "interactionId": "int-mode",
                "kind": "mode_switch",
                "options": [{"label": "Continue", "decision": "proceed"}],
            }
        )
        state.stream_queue = asyncio.Queue()
        client._on_mode_transition_resolved(
            {
                "sessionKey": "sk1",
                "interactionId": "int-mode",
                "kind": "mode_switch",
                "decision": "proceed",
            }
        )

        assert state.stream_queue.empty()

    def test_mode_transition_resolved_rejects_non_mode_kind_before_delivery(
        self, mock_bot_ws
    ):
        service = MagicMock()
        client = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        with pytest.raises(ValueError, match="kind must be mode_switch"):
            client._on_mode_transition_resolved(
                {
                    "sessionKey": "sk1",
                    "interactionId": "int-exec",
                    "kind": "exec",
                    "decision": "allow-once",
                }
            )

        assert state.stream_queue.empty()
        service.mark_resolved.assert_not_called()


@pytest.mark.asyncio
async def test_interaction_dispatch_uses_typed_claim_command(mock_bot_ws):
    service = MagicMock()
    resolution = InteractionResolution(
        kind="ask_user",
        decision="submit",
        answer="target: staging",
        message="target: staging",
        values={"target": "staging"},
        answers={"Where?": "staging"},
        selected_options=(("staging",),),
    )
    service.claim_for_dispatch.return_value = InteractionDispatch(
        session_key="sk1",
        interaction_id="int-1",
        resolution=resolution,
    )
    engine_client = MagicMock()
    engine_request = {
        "type": "req",
        "id": "engine-1",
        "method": "interaction.resolve",
        "params": {"interactionId": "int-1", "decision": "allow-once"},
    }
    engine_response = {"type": "res", "id": "engine-1", "ok": True}
    engine_client.interaction_resolve = AsyncMock(
        return_value=EngineInteractionResolveExchange.from_frames(
            request=engine_request,
            response=engine_response,
        )
    )
    client = AsyncChatClient(
        uri="ws://host/ws", max_retries=0, interaction_service=service
    )
    client._client = engine_client

    await client._dispatch_interaction_answer(
        session_key="sk1",
        interaction_id="int-1",
        deadline_ms=None,
    )

    engine_client.interaction_resolve.assert_awaited_once_with(
        interaction_id="int-1",
        resolution=resolution,
    )
    service.record_engine_exchange.assert_called_once_with(
        session_key="sk1",
        interaction_id="int-1",
        engine_req=engine_request,
        engine_res=engine_response,
    )


@pytest.mark.asyncio
async def test_mode_switch_dispatch_marks_accepted_rpc_resolved(mock_bot_ws):
    service = MagicMock()
    resolution = InteractionResolution(kind="mode_switch", decision="stay")
    service.claim_for_dispatch.return_value = InteractionDispatch(
        session_key="sk1",
        interaction_id="int-mode-1",
        resolution=resolution,
    )
    engine_client = MagicMock()
    engine_request = {
        "type": "req",
        "id": "engine-mode-1",
        "method": "mode_transition.resolve",
        "params": {"transitionId": "int-mode-1", "decision": "stay"},
    }
    engine_response = {
        "type": "res",
        "id": "engine-mode-1",
        "ok": True,
        "payload": {
            "accepted": True,
            "transitionId": "int-mode-1",
            "decision": "stay",
        },
    }
    engine_client.interaction_resolve = AsyncMock(
        return_value=EngineInteractionResolveExchange.from_frames(
            request=engine_request,
            response=engine_response,
        )
    )
    client = AsyncChatClient(
        uri="ws://host/ws", max_retries=0, interaction_service=service
    )
    client._client = engine_client

    await client._dispatch_interaction_answer(
        session_key="sk1",
        interaction_id="int-mode-1",
        deadline_ms=None,
    )

    service.mark_resolved.assert_called_once_with(
        session_key="sk1",
        interaction_id="int-mode-1",
        envelope=engine_response,
    )


# ══════════ P1-1: proxypass 路由前缀保留（_a2a_base_url_from_ws）══════════


class TestA2aBaseUrlFromWs:
    """ws(s):// URL → A2A HTTP base：只剥 WS endpoint 后缀，保留代理前缀。

    WS 后缀词汇集（_claw_service._build_ws_url / adapter.ws_path()）：
      /api/openclaw/ws       — openclaw adapter + DEFAULT_WS_PATH
      /api/claude_code/ws    — claude_code adapter
      /api/ws                — aicoding adapter
      /api/{engine}/ws      — 无 adapter 引擎的默认
      /ws                    — 直连形态（无前缀）
    """

    def test_proxypass_shape_keeps_route_prefix_wss(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        uri = "wss://proxy.example/proxypass/sbx-1:20003/api/openclaw/ws"
        assert (
            _a2a_base_url_from_ws(uri) == "https://proxy.example/proxypass/sbx-1:20003"
        )

    def test_proxypass_shape_keeps_route_prefix_ws(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        uri = "ws://proxy.example/proxypass/sbx-1/api/claude_code/ws"
        assert _a2a_base_url_from_ws(uri) == "http://proxy.example/proxypass/sbx-1"

    def test_aicoding_custom_adapter_ws_path_shape(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        # aicoding adapter 的 ws_path=/api/ws：剥后不得把 /api 留在 base 上
        uri = "wss://proxy.example/proxypass/sbx-1/api/ws"
        assert _a2a_base_url_from_ws(uri) == "https://proxy.example/proxypass/sbx-1"

    def test_default_engine_path_shape(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        uri = "ws://proxy.example/proxypass/sbx-1/api/teclaw/ws"
        assert _a2a_base_url_from_ws(uri) == "http://proxy.example/proxypass/sbx-1"

    def test_direct_shape_no_prefix_strips_to_bare_base(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        assert _a2a_base_url_from_ws("ws://host:20003/ws") == "http://host:20003"

    def test_direct_shape_no_path(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        assert _a2a_base_url_from_ws("ws://host:20003") == "http://host:20003"

    def test_non_ws_scheme_returns_none(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        assert _a2a_base_url_from_ws("http://host/api/openclaw/ws") is None
        assert _a2a_base_url_from_ws("not-a-url") is None
        assert _a2a_base_url_from_ws("") is None


# ══════════ P1-4: 附件 v3 回退 run 的 HITL 答复归属路由 ══════════


def _make_exchange(request_id="e-1", accepted=True) -> EngineInteractionResolveExchange:
    request = {
        "type": "req",
        "id": request_id,
        "method": "interaction.resolve",
        "params": {"interactionId": "int-1", "decision": "allow-once"},
    }
    response = {"type": "res", "id": request_id, "ok": accepted}
    if not accepted:
        response["ok"] = False
        response["error"] = {"code": "UNAVAILABLE", "message": "rejected"}
    return EngineInteractionResolveExchange.from_frames(
        request=request, response=response
    )


def _claim_service_for(resolution: InteractionResolution) -> MagicMock:
    service = MagicMock()
    service.claim_for_dispatch.return_value = InteractionDispatch(
        session_key="sk1",
        interaction_id="int-1",
        resolution=resolution,
    )
    return service


class TestInteractionAnswerTransportAttribution:
    """B3 附件 run 建在 v3 专用连接上——它的 ResolveInteraction 也必须走
    v3：A2A kernel 注册表里没有该 run，错发 A2A 得 not_found、审批永不
    恢复。正常 A2A session 的答复走 A2A 客户端，并带 session 标识（供
    run 上下文取 IAM token，P1-5）。off/v3 模式恒走 self._client 且签名
    原样（现行契约）。"""

    @pytest.mark.asyncio
    async def test_rerouted_session_answer_goes_to_v3_fallback(self, mock_bot_ws):
        resolution = InteractionResolution(kind="exec", decision="allow-once")
        service = _claim_service_for(resolution)
        acc = AsyncChatClient(
            uri="ws://host/ws",
            max_retries=0,
            interaction_service=service,
            transport_mode="grey",
        )
        acc._chat_transport._current_name = "a2a"
        a2a_client = MagicMock(name="a2a")
        a2a_client.interaction_resolve = AsyncMock(return_value=_make_exchange())
        v3_client = MagicMock(name="v3")
        v3_client.interaction_resolve = AsyncMock(return_value=_make_exchange())
        acc._client = a2a_client
        acc._chat_transport.fallback_client = v3_client
        acc._chat_transport.routed_sessions["sk1"] = "v3"  # B3 归属：附件 reroute 过

        await acc._dispatch_interaction_answer(
            session_key="sk1", interaction_id="int-1", deadline_ms=None
        )

        v3_client.interaction_resolve.assert_awaited_once_with(
            interaction_id="int-1", resolution=resolution
        )
        a2a_client.interaction_resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_normal_a2a_session_answer_goes_to_a2a_with_session_key(
        self, mock_bot_ws
    ):
        resolution = InteractionResolution(kind="exec", decision="allow-once")
        service = _claim_service_for(resolution)
        acc = AsyncChatClient(
            uri="ws://host/ws",
            max_retries=0,
            interaction_service=service,
            transport_mode="grey",
        )
        acc._chat_transport._current_name = "a2a"
        a2a_client = MagicMock(name="a2a")
        a2a_client.interaction_resolve = AsyncMock(return_value=_make_exchange())
        v3_client = MagicMock(name="v3")
        v3_client.interaction_resolve = AsyncMock(return_value=_make_exchange())
        acc._client = a2a_client
        acc._chat_transport.fallback_client = v3_client

        await acc._dispatch_interaction_answer(
            session_key="sk1", interaction_id="int-1", deadline_ms=None
        )

        a2a_client.interaction_resolve.assert_awaited_once_with(
            interaction_id="int-1", resolution=resolution, session_key="sk1"
        )
        v3_client.interaction_resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rerouted_session_without_fallback_marks_failed(self, mock_bot_ws):
        resolution = InteractionResolution(kind="exec", decision="allow-once")
        service = _claim_service_for(resolution)
        acc = AsyncChatClient(
            uri="ws://host/ws",
            max_retries=0,
            interaction_service=service,
            transport_mode="grey",
        )
        acc._chat_transport._current_name = "a2a"
        acc._client = MagicMock(name="a2a")
        acc._chat_transport.routed_sessions["sk1"] = "v3"
        acc._chat_transport.fallback_client = None  # fallback 已收口

        await acc._dispatch_interaction_answer(
            session_key="sk1", interaction_id="int-1", deadline_ms=None
        )

        service.mark_failed.assert_called_once()
        assert service.mark_failed.call_args.kwargs["session_key"] == "sk1"

    @pytest.mark.asyncio
    async def test_off_mode_signature_stays_unchanged(self, mock_bot_ws):
        """off/v3 模式：恒走 self._client、签名不带 session_key（现行契约
        test_interaction_dispatch_uses_typed_claim_command 的精确钉）。"""
        resolution = InteractionResolution(kind="exec", decision="allow-once")
        service = _claim_service_for(resolution)
        acc = AsyncChatClient(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        engine_client = MagicMock()
        engine_client.interaction_resolve = AsyncMock(return_value=_make_exchange())
        acc._client = engine_client  # off 模式 v3-ws
        assert not hasattr(acc._chat_transport, "routed_sessions")

        await acc._dispatch_interaction_answer(
            session_key="sk1", interaction_id="int-1", deadline_ms=None
        )

        engine_client.interaction_resolve.assert_awaited_once_with(
            interaction_id="int-1", resolution=resolution
        )
        assert (
            engine_client.interaction_resolve.await_args.kwargs.get("session_key")
            is None
        )


# ==================== a2a_mode construct validation ====================


class TestA2aModeValidation:
    def test_invalid_a2a_mode_is_rejected_at_construct(self, mock_bot_ws):
        """非法灰度模式在构造期即拒——不落到中途探测时才炸。"""
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient as _ACC,
        )

        with pytest.raises(ValueError, match="invalid transport_mode"):
            _ACC(uri="ws://host/ws", transport_mode="bogus")

    @pytest.mark.parametrize("mode", ["off", "grey", "on"])
    def test_valid_modes_are_accepted(self, mode, mock_bot_ws):
        from secbaas.community.core.service.bot_run._async_chat_client import (
            AsyncChatClient as _ACC,
        )

        client = _ACC(uri="ws://host/ws", transport_mode=mode)
        assert client.transport_mode == mode

    def test_constructor_selects_transport_strategy_once(self, mock_bot_ws):
        """off 默认只选 WS；grey/on 只选 A2A，构造后不允许混走另一实现。"""
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            A2aChatTransport,
        )
        from secbaas.community.core.service.bot_run._async_chat_client_ws import (
            WebSocketChatTransport,
        )

        default_client = AsyncChatClient(uri="ws://host/ws")
        assert isinstance(default_client._chat_transport, WebSocketChatTransport)
        assert not hasattr(default_client._chat_transport, "fallback_client")
        assert not hasattr(default_client._chat_transport, "routed_sessions")

        for mode in ("off", "grey", "on"):
            client = AsyncChatClient(uri="ws://host/ws", transport_mode=mode)
            assert client.transport_name == "unknown"
            assert not hasattr(client, "_transport")

        for mode in ("grey", "on"):
            client = AsyncChatClient(uri="ws://host/ws", transport_mode=mode)
            assert isinstance(client._chat_transport, A2aChatTransport)
            assert client._chat_transport.mode == mode

    @pytest.mark.asyncio
    async def test_a2a_reconnect_uses_selected_strategy(self, mock_bot_ws):
        """A2A 断链重连仍走 A2A 策略，不落回硬编码 WS 构造。"""

        def make_a2a_client(**_kwargs):
            instance = MagicMock()
            instance.connected = True
            instance.connect = AsyncMock(return_value=True)
            instance.close = AsyncMock()
            instance.server_info = {"features": {}}
            return instance

        a2a_cls = MagicMock(side_effect=make_a2a_client)
        ws_cls = MagicMock(side_effect=AssertionError("A2A reconnect used WS"))
        with (
            patch(
                "secbaas.community.core.service.bot_run._async_chat_client_a2a."
                "BotA2aClient",
                a2a_cls,
            ),
            patch(
                "secbaas.community.core.service.bot_run._async_chat_client."
                "BotWebSocketClient",
                ws_cls,
            ),
        ):
            client = AsyncChatClient(
                uri="ws://host/api/openclaw/ws",
                transport_mode="on",
                max_retries=1,
                retry_base_backoff=0,
            )
            await client.connect()
            old_client = client._client

            old_client.connected = False
            client._notify_disconnect()
            await asyncio.sleep(0.02)

            assert a2a_cls.call_count == 2
            assert ws_cls.call_count == 0
            assert client._client is not old_client
            assert client._chat_transport.transport_name == "a2a"
            await client.close()

    @pytest.mark.asyncio
    async def test_a2a_on_without_base_url_raises(self, mock_bot_ws):
        """transport_mode=on 无可推导 HTTP base 时明确失败，不伪装成 WS 成功。"""
        from secbaas.community.core.service.bot_run._bot_a2a_client import (
            A2aUnavailableError,
        )

        client = AsyncChatClient(uri="ws://host/not-openclaw", transport_mode="on")
        # A non-openclaw/custom path is still convertible to a host base, so
        # force the strategy boundary directly to cover the no-base contract.
        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client_a2a."
            "_a2a_base_url_from_ws",
            return_value=None,
        ):
            with pytest.raises(A2aUnavailableError):
                await client.connect()


class TestA2aGreyReconnectFallbackOwnership:
    """grey 重连回退 WS 后，既有 fallback run 仍必须按归属路由。"""

    @pytest.mark.asyncio
    async def test_late_abort_and_interaction_stay_on_fallback(self, mock_bot_ws):
        class _FakeEndpoint:
            connected = True

            def __init__(self, name: str):
                self.name = name
                self.calls: list[str] = []

            async def chat_abort(self, *args, **kwargs):
                self.calls.extend(args)
                return {"ok": True, "endpoint": self.name}

        acc = AsyncChatClient(uri="ws://host/api/openclaw/ws", transport_mode="grey")
        fallback = _FakeEndpoint("fallback")
        new_main = _FakeEndpoint("new-main-ws")
        acc._client = new_main
        acc._chat_transport._current_name = "v3-ws"
        acc._chat_transport.fallback_client = fallback
        acc._chat_transport.routed_sessions["s-att"] = "v3"

        assert await acc.chat_abort("s-att", "r1") == {
            "ok": True,
            "endpoint": "fallback",
        }
        client, extra = await acc._chat_transport.interaction_target(
            session_key="s-att"
        )
        assert client is fallback
        assert extra == {}

        # 未登记归属的 normal run 仍走重连后的 WS main，不带 A2A session key。
        client, extra = await acc._chat_transport.interaction_target(
            session_key="s-normal"
        )
        assert client is new_main
        assert extra == {}


# ==================== A2A base URL: unknown custom ws path ====================


class TestA2aBaseUrlUnknownWsPath:
    def test_unknown_custom_ws_path_falls_back_to_bare_host(self):
        """末段非 ``ws`` 的自定义 ws_path：不猜前缀归属——base 维持 host 根，
        由能力探测诚实回退（portal 前缀只在词汇表内剥）。"""
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        assert (
            _a2a_base_url_from_ws("ws://host:20003/custom/endpoint")
            == "http://host:20003"
        )


# ==================== B3: A2A 传输上的附件注入 → v3 重路由 ====================


class _StubV3Transport:
    """BotWebSocketClient 的 duck 替身——仅测试注入重路由消费的面。

    R3-2 扩展：记录断连处理器并可模拟 recv_loop finally 的断连通知
    （connected=False + 回调），供 fallback 断连收口/重建场景使用。
    """

    instances: list["_StubV3Transport"] = []

    def __init__(self, uri, client_id=None, client_version="1.0.0", headers=None):
        self.uri = uri
        self.connected = True
        self._event_handlers: dict = {}
        self.inject_calls: list[tuple[str, tuple, dict]] = []
        self.chat_send_calls: list[tuple[str, tuple, dict]] = []
        self.chat_abort_calls: list[tuple[str, dict]] = []
        self.close_calls = 0
        self.disconnect_handler = None
        _StubV3Transport.instances.append(self)

    async def connect(self) -> dict:
        return {"ok": True, "payload": {"server": {}}}

    def on_event(self, name: str, handler) -> None:
        self._event_handlers[name] = handler

    def on_disconnect(self, handler) -> None:
        self.disconnect_handler = handler

    def simulate_disconnect(self) -> None:
        """模拟 recv_loop finally：连接失效 + 触发断连处理器。"""
        self.connected = False
        if self.disconnect_handler is not None:
            self.disconnect_handler("disconnect", {})

    async def chat_inject(self, session_key: str, *args, **kwargs) -> dict:
        self.inject_calls.append((session_key, args, kwargs))
        return {"ok": True}

    async def chat_send(self, session_key: str, *args, **kwargs) -> dict:
        self.chat_send_calls.append((session_key, args, kwargs))
        return {"ok": True}

    async def chat_abort(self, session_key: str, run_id=None) -> dict:
        self.chat_abort_calls.append((session_key, {"run_id": run_id}))
        return {"ok": True}

    async def close(self) -> None:
        self.close_calls += 1
        self.connected = False


@pytest.fixture
def stub_v3_patch():
    _StubV3Transport.instances = []
    with patch(
        "secbaas.community.core.service.bot_run._async_chat_client.BotWebSocketClient",
        _StubV3Transport,
    ):
        yield
    _StubV3Transport.instances = []


def _a2a_acc_with_stub_main_client() -> AsyncChatClient:
    """A2A 策略 + 可收口的替身主连接（close 可 await）。"""
    acc = AsyncChatClient(uri="ws://host/ws", max_retries=0, transport_mode="grey")
    acc._client = MagicMock()
    acc._client.connected = True
    acc._client.close = AsyncMock()
    acc._chat_transport._current_name = "a2a"  # 已连接的 A2A 策略测试面
    return acc


class TestInjectAttachmentsV3Reroute:
    """16-5 双入口的另一侧：注入带附件同样必须重路由到 v3 专用连接。"""

    @pytest.mark.asyncio
    async def test_a2a_inject_with_attachments_reroutes_to_v3(
        self, mock_bot_ws, stub_v3_patch
    ):
        acc = _a2a_acc_with_stub_main_client()

        await acc.inject_message(
            "with files", session_key="sk-att", attachments=[{"kind": "screenshot"}]
        )

        fallback = acc._chat_transport.fallback_client
        assert fallback is not None, "附件注入必须懒建 v3 专用连接"
        assert fallback is _StubV3Transport.instances[-1]
        assert fallback.inject_calls, "chat_inject 未打到 v3 fallback"
        session_key, args, kwargs = fallback.inject_calls[0]
        assert session_key == "sk-att"
        assert args == (), f"参数应全关键字转 v3: {args}"
        assert kwargs["message"] == "with files", f"message 必须原样转 v3: {kwargs}"
        assert kwargs["attachments"] == [{"kind": "screenshot"}]
        # 注入不开启主 run 等待面；保留旧行为：不注入归属表。
        assert acc._chat_transport.routed_sessions == {}

        await acc.close()
        assert acc._chat_transport.fallback_client is None, (
            "注入收口后 fallback 随 close 清空"
        )

    @pytest.mark.asyncio
    async def test_second_a2a_inject_reuses_cached_v3_fallback(
        self, mock_bot_ws, stub_v3_patch
    ):
        """fallback 懒建一次后必须缓存复用——不得逐请求重建连接。"""
        acc = _a2a_acc_with_stub_main_client()

        await acc.inject_message("first", session_key="sk-1", attachments=[{"k": 1}])
        first_fallback = acc._chat_transport.fallback_client
        await acc.inject_message("second", session_key="sk-2", attachments=[{"k": 2}])

        assert acc._chat_transport.fallback_client is first_fallback, (
            "second 注入必须复用同一 fallback"
        )
        created = len(_StubV3Transport.instances)
        assert created == 1, f"fallback 被重建 {created} 次（应为 1）"
        assert len(first_fallback.inject_calls) == 2

        await acc.close()
        assert first_fallback.close_calls == 1, "收口必须关唯一的 fallback"

    @pytest.mark.asyncio
    async def test_a2a_inject_attachment_preserves_send_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """注入附件走 fallback，但不覆盖/清空已有主 run 归属。"""
        acc = _a2a_acc_with_stub_main_client()

        await acc.send_message(
            "main", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
        )
        assert "sk-att" in acc._chat_transport.routed_sessions

        await acc.inject_message("side", session_key="sk-att", attachments=[{"k": 2}])

        assert "sk-att" in acc._chat_transport.routed_sessions
        await acc.close()

    async def test_bridge_snapshot_is_empty_without_client(self, mock_bot_ws):
        """快照从当前传输取 handlers——无传输时空表（fallback 不注册假面）。"""
        acc = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        assert acc._event_bridge_snapshot() == {}

    @pytest.mark.asyncio
    async def test_close_swallows_v3_fallback_close_failure(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """fallback close 抛错必须吞掉——主连接收口不得被旁支拖死。"""
        acc = AsyncChatClient(uri="ws://host/ws", max_retries=0, transport_mode="grey")
        await acc.connect()

        bad_fallback = MagicMock()
        bad_fallback.close = AsyncMock(side_effect=RuntimeError("fallback boom"))
        acc._chat_transport.fallback_client = bad_fallback

        await acc.close()  # 不得上抛

        bad_fallback.close.assert_awaited_once()
        assert acc._chat_transport.fallback_client is None, "close 后 fallback 必须清空"
        mock_bot_ws_instance.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_cleans_normal_v3_fallback(
        self, mock_bot_ws, mock_bot_ws_instance, stub_v3_patch
    ):
        acc = _a2a_acc_with_stub_main_client()

        await acc.inject_message("m", session_key="sk-1", attachments=[{"k": 1}])
        fallback = acc._chat_transport.fallback_client
        assert fallback is not None

        await acc.close()

        assert fallback.close_calls == 1, "fallback 随 close 收口"
        assert acc._chat_transport.fallback_client is None
        assert acc._chat_transport.routed_sessions == {}


class TestV3FallbackDisconnectRecovery:
    """PR #2743 R3-2：fallback 断连的隔离收口与连接恢复。

    回归钉：fallback WS 断连此前既不通知等待方（ACK 后上层悬挂等自身
    超时）、也不失效坏连接（后续附件请求继续复用）。现在断连处理器只
    终结归属 fallback 的会话——A2A 主连接的在途请求不受影响。
    """

    async def _wait_until(self, predicate, timeout_s: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("condition not reached in time")
            await asyncio.sleep(0.005)

    def _acc_with_awaitable_main(self) -> AsyncChatClient:
        acc = _a2a_acc_with_stub_main_client()
        acc._client.chat_send = AsyncMock(return_value={"ok": True})
        return acc

    @pytest.mark.asyncio
    async def test_ack_then_disconnect_terminates_waiter(
        self, mock_bot_ws, stub_v3_patch
    ):
        """验收 1+2 组合（非流式）：ACK 后断连——无超时等待方及时收到
        明确 error，不悬挂；且不自动重发已 ACK 的请求。"""
        acc = self._acc_with_awaitable_main()

        task = asyncio.create_task(
            acc.send_message(
                "hi",
                session_key="sk-att-1",
                attachments=[{"kind": "screenshot"}],
            )
        )
        await self._wait_until(
            lambda: (
                bool(_StubV3Transport.instances)
                and len(_StubV3Transport.instances[-1].chat_send_calls) >= 1
            )
        )
        fallback = acc._chat_transport.fallback_client
        assert fallback is not None

        fallback.simulate_disconnect()

        with pytest.raises(BotSessionError, match="fallback connection lost"):
            await asyncio.wait_for(task, 2)
        # 不自动重发：断连后 fallback/主连接都没有第二次 chat.send
        assert len(fallback.chat_send_calls) == 1
        assert acc._client.chat_send.await_count == 0
        # 坏连接不再被持有；归属路由表收口
        assert acc._chat_transport.fallback_client is None
        assert acc._chat_transport.routed_sessions == {}

    @pytest.mark.asyncio
    async def test_ack_then_disconnect_terminates_stream_waiter(
        self, mock_bot_ws, stub_v3_patch
    ):
        """验收 2（流式）：断连后流式消费者收到 error chunk 并结束迭代。"""
        acc = self._acc_with_awaitable_main()

        async def consume() -> list[StreamChunk]:
            chunks: list[StreamChunk] = []
            async for chunk in acc.send_message_stream(
                "hi",
                session_key="sk-att-2",
                attachments=[{"kind": "screenshot"}],
            ):
                chunks.append(chunk)
            return chunks

        task = asyncio.create_task(consume())
        await self._wait_until(
            lambda: (
                bool(_StubV3Transport.instances)
                and len(_StubV3Transport.instances[-1].chat_send_calls) >= 1
            )
        )
        acc._chat_transport.fallback_client.simulate_disconnect()

        chunks = await asyncio.wait_for(task, 2)
        errs = [c for c in chunks if c.type == "error"]
        assert errs, f"stream ended without error chunk: {chunks}"
        assert "fallback connection lost" in errs[-1].content

    @pytest.mark.asyncio
    async def test_disconnect_spares_inflight_a2a_requests(
        self, mock_bot_ws, stub_v3_patch
    ):
        """验收 3：fallback 断连只终结归属会话——A2A 主连接上 ACK 已过、
        仍在等回复的请求不被误杀（R17-5 隔离裁决）。"""
        acc = self._acc_with_awaitable_main()

        a2a_task = asyncio.create_task(acc.send_message("plain", session_key="sk-a2a"))
        att_task = asyncio.create_task(
            acc.send_message(
                "hi",
                session_key="sk-att-3",
                attachments=[{"kind": "screenshot"}],
            )
        )
        await self._wait_until(
            lambda: (
                bool(_StubV3Transport.instances)
                and len(_StubV3Transport.instances[-1].chat_send_calls) >= 1
            )
        )
        await self._wait_until(lambda: acc._client.chat_send.await_count == 1)

        acc._chat_transport.fallback_client.simulate_disconnect()

        with pytest.raises(BotSessionError):
            await asyncio.wait_for(att_task, 2)
        # A2A 请求不受影响：仍在等待
        await asyncio.sleep(0.1)
        assert not a2a_task.done(), "fallback 断连误杀了 A2A 侧在途请求"
        # 收尾：A2A 会话正常自然结束（chat_complete 置位）
        state = acc._sessions.get("sk-a2a")
        state.chat_complete.set()
        await asyncio.wait_for(a2a_task, 2)

    @pytest.mark.asyncio
    async def test_attachment_after_disconnect_rebuilds_connection(
        self, mock_bot_ws, stub_v3_patch
    ):
        """验收 4：断连后再次发送附件——建立新连接并正常发送，不缺
        不重用旧连接。"""
        acc = self._acc_with_awaitable_main()

        async def send_att(sk: str, min_count: int):
            task = asyncio.create_task(
                acc.send_message(
                    "hi", session_key=sk, attachments=[{"kind": "screenshot"}]
                )
            )
            await self._wait_until(
                lambda: (
                    bool(_StubV3Transport.instances)
                    and len(_StubV3Transport.instances[-1].chat_send_calls) >= min_count
                )
            )
            return task

        task1 = await send_att("sk-att-4a", 1)
        first = acc._chat_transport.fallback_client
        first.simulate_disconnect()
        with pytest.raises(BotSessionError):
            await asyncio.wait_for(task1, 2)

        created = len(_StubV3Transport.instances)
        task2 = asyncio.create_task(
            acc.send_message(
                "hi", session_key="sk-att-4b", attachments=[{"kind": "screenshot"}]
            )
        )
        await self._wait_until(
            lambda: (
                len(_StubV3Transport.instances) > created
                and len(_StubV3Transport.instances[-1].chat_send_calls) >= 1
            )
        )
        second = acc._chat_transport.fallback_client
        assert second is not None and second is not first, "复用了已断开连接"
        assert len(_StubV3Transport.instances) == created + 1
        assert second in _StubV3Transport.instances
        assert len(second.chat_send_calls) == 1

        # 收尾：第二连接存活；旧连接的迟到断连通知（验收 5）不
        # 清新连接、不误杀其等待方
        first.simulate_disconnect()
        assert acc._chat_transport.fallback_client is second, (
            "旧连接迟到通知清掉了新连接"
        )
        await asyncio.sleep(0.05)
        assert not task2.done(), "旧连接迟到通知误杀了新连接的等待方"
        second.simulate_disconnect()
        with pytest.raises(BotSessionError):
            await asyncio.wait_for(task2, 2)

    @pytest.mark.asyncio
    async def test_dead_fallback_is_judged_unhealthy_without_notification(
        self, mock_bot_ws, stub_v3_patch
    ):
        """复用健康检查：连接已死但断连通知尚未送达（recv_loop 还在
        finally 路径上）时，_ensure_v3_fallback 不得返回死连接——就地
        失效收口（归属表清理）并重建。"""
        acc = self._acc_with_awaitable_main()
        await acc.inject_message("m", session_key="sk-att-5", attachments=[{"k": 1}])
        first = acc._chat_transport.fallback_client
        assert first is not None
        first.connected = False  # 死连接，未触发断连处理器

        await acc.inject_message("m2", session_key="sk-att-5", attachments=[{"k": 1}])

        second = acc._chat_transport.fallback_client
        assert second is not first, "死连接被复用"
        assert len(_StubV3Transport.instances) == 2
        assert len(second.inject_calls) == 1
        await acc.close()


class TestAttachFallbackRunOwnershipLifecycle:
    """R7-F2：附件 v3 fallback run 的归属锚定 run 生命周期，不随等待方
    收口。wait_result=False 的 ACK 返回不等于 run 结束——abort/resolve 仍
    必须路由 v3、池归属查询/sweep 保护仍必须认账；标记由后台终态帧
    （chat final/error）释放，而非 send_message 的 finally。"""

    async def _wait_until(self, predicate, timeout_s: float = 2.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("condition not met within timeout")

    def _acc_with_owned_a2a_main(self) -> AsyncChatClient:
        """transport=a2a + 显式诚实面主连接（归属查询/abort 不误报）。"""
        acc = AsyncChatClient(uri="ws://host/ws", max_retries=0, transport_mode="grey")
        acc._chat_transport._current_name = "a2a"
        acc._client = MagicMock()
        acc._client.connected = True
        acc._client.close = AsyncMock()
        acc._client.chat_abort = AsyncMock(return_value={"ok": False})
        acc._client.tracks_session = MagicMock(return_value=False)
        acc._client.has_running_runs = False
        # 桥面快照从主连接 _event_handlers 复刻——挂编排器真实 chat
        # 处理器，fallback 收引擎帧走真分发路径（非 mock）。
        acc._client._event_handlers = {"chat": acc._on_chat}
        return acc

    @pytest.mark.asyncio
    async def test_ack_return_keeps_v3_ownership_alive(
        self, mock_bot_ws, stub_v3_patch
    ):
        """主复现：send_message(wait_result=False, attachments) ACK 返回后
        ——标记存活、abort 随 run 走 v3、池两查询认账、不误发 A2A。"""
        acc = self._acc_with_owned_a2a_main()

        await acc.send_message(
            "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
        )

        fallback = acc._chat_transport.fallback_client
        assert fallback is not None and fallback.chat_send_calls, "未重路由 v3"
        # ACK 后归属标记不得被等待方 finally 收口
        assert "sk-att" in acc._chat_transport.routed_sessions, "ACK 后归属标记被误清"
        # 池归属路由查询：找到持 run 的连接
        assert acc.tracks_session("sk-att") is True, "池归属查询不认后台附件 run"
        # sweep 排空判断：连接不得当空闲收口
        assert acc.has_active_sessions is True, "sweep 判断不认后台附件 run"
        # abort 必须发到 v3 fallback（A2A 客户端没有该 run）
        await acc.chat_abort("sk-att")
        assert fallback.chat_abort_calls == [("sk-att", {"run_id": None})], (
            "abort 未随 run 走 v3"
        )
        acc._client.chat_abort.assert_not_awaited()  # 被误发 A2A 主连接即失败
        await acc.close()

    @pytest.mark.asyncio
    async def test_background_terminal_frame_releases_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """后台终态帧（chat final——等待方已收口、state 为 None 的帧）是
        标记的释放点：归属随 run 终止结束，连接可被 sweep 收口。"""
        acc = self._acc_with_owned_a2a_main()

        await acc.send_message(
            "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
        )

        fallback = acc._chat_transport.fallback_client
        chat_handler = fallback._event_handlers["chat"]
        # 引擎经 v3 连接推 final——此时 orchestrator 的 session state 已被
        # ACK 方 finally pop（state=None 分支是归属标记唯一见证者）
        chat_handler(
            {
                "sessionKey": "sk-att",
                "state": "final",
                "message": {"content": [{"type": "text", "text": "ok"}]},
            }
        )

        assert "sk-att" not in acc._chat_transport.routed_sessions, "终态帧后标记泄漏"
        assert acc.tracks_session("sk-att") is False, "终态后归属查询仍认账"
        assert acc.has_active_sessions is False, "终态后连接仍不可收口"
        await acc.close()

    @pytest.mark.asyncio
    async def test_waiter_timeout_keeps_v3_ownership(self, mock_bot_ws, stub_v3_patch):
        """wait_result=True 超时：等待方虽收口，engine 侧 run 仍在执行
        ——归属必须存活（abort 仍路由 v3），由后台终态帧释放。"""
        acc = self._acc_with_owned_a2a_main()

        with pytest.raises(TimeoutError):
            await acc.send_message(
                "m",
                session_key="sk-att",
                attachments=[{"k": 1}],
                wait_result=True,
                timeout=0.05,
            )

        assert "sk-att" in acc._chat_transport.routed_sessions, "超时后归属标记被误清"
        await acc.chat_abort("sk-att")
        assert acc._chat_transport.fallback_client.chat_abort_calls == [
            ("sk-att", {"run_id": None})
        ]
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()

    @pytest.mark.asyncio
    async def test_engine_error_terminal_releases_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """等待中的 run 以 error 终态收口=生命周期结束——归属随释放。"""
        acc = self._acc_with_owned_a2a_main()

        task = asyncio.create_task(
            acc.send_message(
                "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=True
            )
        )
        await self._wait_until(
            lambda: (
                acc._chat_transport.fallback_client is not None
                and bool(acc._chat_transport.fallback_client.chat_send_calls)
            )
        )
        chat_handler = acc._chat_transport.fallback_client._event_handlers["chat"]
        chat_handler(
            {
                "sessionKey": "sk-att",
                "state": "error",
                "errorMessage": "engine boom",
                "errorCode": "E_BOOM",
            }
        )

        with pytest.raises(BotSessionError):
            await task
        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "error 终态后标记泄漏"
        )
        await acc.close()

    @pytest.mark.asyncio
    async def test_send_failure_clears_ownership_no_stale_marker(
        self, mock_bot_ws, stub_v3_patch
    ):
        """chat_send 被引擎拒（run 从未开始）——不得残留归属标记（否则
        后续该 session 的 abort/resolve 会被误路由 v3）。"""
        acc = self._acc_with_owned_a2a_main()

        with patch.object(
            _StubV3Transport,
            "chat_send",
            AsyncMock(
                return_value={
                    "ok": False,
                    "error": {"code": "REJECTED", "message": "no"},
                }
            ),
        ):
            from secbaas.community.core.service.bot_run._async_chat_client import (
                ChatRequestError,
            )

            with pytest.raises(ChatRequestError):
                await acc.send_message(
                    "m",
                    session_key="sk-att",
                    attachments=[{"k": 1}],
                    wait_result=False,
                )

        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "失败发送残留归属标记"
        )
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()

    # ─────────── R8：归属释放必须锚定真实终态，不枚举等待方退出方式 ───────────

    @pytest.mark.asyncio
    async def test_inject_final_does_not_release_background_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """R8-F1：后台附件 run 在途时，同 session 的注入 final（runId 带
        inject- 前缀 / stopReason=inject）不是主 run 终态——不得释放归属，
        否则主 run 的 abort 会被误路由 A2A 主连接。"""
        acc = self._acc_with_owned_a2a_main()
        await acc.send_message(
            "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
        )
        fallback = acc._chat_transport.fallback_client
        chat_handler = fallback._event_handlers["chat"]

        # 两种注入终态形态，主 run 都必须在途认账
        chat_handler(
            {
                "sessionKey": "sk-att",
                "state": "final",
                "runId": "inject-sidecar",
                "message": {"content": [{"type": "text", "text": "injected"}]},
            }
        )
        chat_handler(
            {
                "sessionKey": "sk-att",
                "state": "final",
                "stopReason": "inject",
                "message": {"content": [{"type": "text", "text": "injected"}]},
            }
        )

        assert "sk-att" in acc._chat_transport.routed_sessions, (
            "注入 final 误释放主 run 归属"
        )
        assert acc.tracks_session("sk-att") is True, "归属查询因注入误翻"
        await acc.chat_abort("sk-att")
        assert fallback.chat_abort_calls == [("sk-att", {"run_id": None})], (
            "主 run 的 abort 被注入误导到错误连接"
        )
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()

    @pytest.mark.asyncio
    async def test_waiter_cancellation_keeps_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """R8-F2a：取消本地等待协程（调用方 task.cancel() / 外层 wait_for
        超时都是 CancelledError 进 finally）不会取消已发到引擎的 run
        ——归属必须保留，abort 仍路由 v3。"""
        acc = self._acc_with_owned_a2a_main()

        task = asyncio.create_task(
            acc.send_message(
                "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=True
            )
        )
        await self._wait_until(
            lambda: (
                acc._chat_transport.fallback_client is not None
                and bool(acc._chat_transport.fallback_client.chat_send_calls)
            )
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert "sk-att" in acc._chat_transport.routed_sessions, (
            "取消等待方误删在途 run 归属"
        )
        await acc.chat_abort("sk-att")
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()

    @pytest.mark.asyncio
    async def test_stream_early_close_keeps_ownership(self, mock_bot_ws, stub_v3_patch):
        """R8-F2b：流式消费方在终态 chunk 之前提前关闭（break/超时/异常）
        ——引擎侧 run 仍在执行，finally 不得清归属。"""
        acc = self._acc_with_owned_a2a_main()

        chunks: list[StreamChunk] = []

        async def consume():
            agen = acc.send_message_stream(
                "m",
                session_key="sk-att",
                attachments=[{"k": 1}],
                timeout=5.0,
            )
            async for chunk in agen:
                chunks.append(chunk)
                break  # 终态前提前关闭
            # 显式收口：否则 async-gen finalizer 延迟执行，finally 时序
            # 不确定（假绿）——R8 的确定性形态必须当场触发 finally。
            await agen.aclose()

        t = asyncio.create_task(consume())
        await self._wait_until(
            lambda: (
                acc._chat_transport.fallback_client is not None
                and bool(acc._chat_transport.fallback_client.chat_send_calls)
            )
        )
        acc._chat_transport.fallback_client._event_handlers["chat"](
            {
                "sessionKey": "sk-att",
                "state": "delta",
                "deltaText": "部分响应",
                "message": {"content": [{"type": "text", "text": "部分响应"}]},
            }
        )
        await t
        assert chunks, "未消费到任何 chunk 就提前结束（fixture 失效）"

        assert "sk-att" in acc._chat_transport.routed_sessions, (
            "流式提前关闭误删在途 run 归属"
        )
        await acc.chat_abort("sk-att")
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()

    @pytest.mark.asyncio
    async def test_terminal_before_send_returns_releases_ownership(self, mock_bot_ws):
        """R8-F3：终态帧在 chat_send() 等待方恢复前被 WS 接收循环分派
        （state 仍在、终态走有 state 分支）——归属释放不得依赖"state
        已被等待方 pop"的形态，否则此后再无终态可触发清理，标记永久残留
        （连接无法排空、owner 悬挂）。"""
        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client."
            "BotWebSocketClient",
            _TerminalOnSendV3,
        ):
            acc = self._acc_with_owned_a2a_main()
            await acc.send_message(
                "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
            )

        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "终态已到达但归属永久残留"
        )
        assert acc.tracks_session("sk-att") is False, "终态后归属查询仍认账"
        assert acc.has_active_sessions is False, "终态后连接仍不可排空"
        await acc.close()

    # ─────────── R9：归属释放的时序/key 规范化/终态通道三面 ───────────

    @pytest.mark.asyncio
    async def test_stream_disconnect_while_enqueued_raises_notconnected(
        self, mock_bot_ws
    ):
        """R9-F1：流式请求通过入口连接检查后在并发/同 session 排队期间
        断连——恢复执行必须抛 NotConnectedError，不得被 finally 里未初始化
        变量的 UnboundLocalError 吞掉真实断连原因。"""
        acc = AsyncChatClient(uri="ws://host/ws", max_retries=0)
        acc._client = MagicMock()
        acc._client.connected = True
        acc._client.close = AsyncMock()

        # 先占住同 session 让生成器排队（入口检查此时已通过）
        async with acc._condition:
            acc._active_sessions.add("sk-att")

        async def consume():
            agen = acc.send_message_stream(
                "m", session_key="sk-att", attachments=[{"k": 1}], timeout=5.0
            )
            async for chunk in agen:
                pass

        t = asyncio.create_task(consume())
        await asyncio.sleep(0.1)  # t 已在 condition 排队
        acc._client.connected = False  # 排队期间断连
        async with acc._condition:
            acc._active_sessions.discard("sk-att")
            acc._condition.notify_all()

        with pytest.raises(NotConnectedError):
            await t
        await acc.close()

    @pytest.mark.asyncio
    async def test_decorated_terminal_key_still_releases_ownership(
        self, mock_bot_ws, stub_v3_patch
    ):
        """R9-F2：引擎终态帧的 sessionKey 是发送 key 的前后缀装饰形态
        （SessionKeyMatcher 语义）——释放必须能从装饰 key 归并回发送时的
        登记键，等待中与后台两态都不许残留。"""
        acc = self._acc_with_owned_a2a_main()
        decorated = "agent:claude-code-ws:session:sk-att:user:claude-code-ws"
        final_frame = {
            "sessionKey": decorated,
            "state": "final",
            "message": {"content": [{"type": "text", "text": "done"}]},
        }

        # 后台态：等待方已收口，state=None 分支收帧
        await acc.send_message(
            "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
        )
        assert "sk-att" in acc._chat_transport.routed_sessions
        acc._chat_transport.fallback_client._event_handlers["chat"](final_frame)
        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "装饰 key 的后台终态未释放归属"
        )
        assert acc.has_active_sessions is False

        # 等待中态：装饰 key 终态唤醒等待方，归属同样释放
        task = asyncio.create_task(
            acc.send_message(
                "m2", session_key="sk-att", attachments=[{"k": 1}], wait_result=True
            )
        )
        await self._wait_until(
            lambda: len(acc._chat_transport.fallback_client.chat_send_calls) == 2
        )
        acc._chat_transport.fallback_client._event_handlers["chat"](final_frame)
        content, _payloads = await task
        assert content == "done"
        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "装饰 key 的等待中终态未释放归属"
        )
        await acc.close()

    @pytest.mark.asyncio
    async def test_agent_and_error_terminal_channels_release_ownership(
        self, mock_bot_ws
    ):
        """R9-F3a：终态不保证走 chat 通道（_derive_event_name 可派发为
        agent，WS 转发层遇终态直接结束）——agent final/error 与 error 通道
        必须同样释放归属，后台态（state=None）不许漏。"""
        acc = self._acc_with_owned_a2a_main()
        acc._client._event_handlers = {
            "chat": acc._on_chat,
            "agent": acc._on_agent,
            "error": acc._on_error,
        }
        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client."
            "BotWebSocketClient",
            _StubV3Transport,
        ):
            # agent final 通道（后台态）
            await acc.send_message(
                "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
            )
            acc._chat_transport.fallback_client._event_handlers["agent"](
                {"sessionKey": "sk-att", "stream": "assistant", "state": "final"}
            )
            assert "sk-att" not in acc._chat_transport.routed_sessions, (
                "agent final 通道未释放归属"
            )

            # error 通道（后台态）
            await acc.send_message(
                "m2", session_key="sk-att", attachments=[{"k": 1}], wait_result=False
            )
            acc._chat_transport.fallback_client._event_handlers["error"](
                {"sessionKey": "sk-att", "state": "error", "errorMessage": "boom"}
            )
            assert "sk-att" not in acc._chat_transport.routed_sessions, (
                "error 通道未释放归属"
            )
        await acc.close()

    @pytest.mark.asyncio
    async def test_agent_final_wake_releases_ownership_in_wait(
        self, mock_bot_ws, stub_v3_patch
    ):
        """R9-F3b：等待方被 agent final 唤醒（仅 agent 通道终结、无 chat
        final）——run 已终结，归属必须释放。"""
        acc = self._acc_with_owned_a2a_main()
        acc._client._event_handlers = {
            "chat": acc._on_chat,
            "agent": acc._on_agent,
            "error": acc._on_error,
        }

        task = asyncio.create_task(
            acc.send_message(
                "m", session_key="sk-att", attachments=[{"k": 1}], wait_result=True
            )
        )
        await self._wait_until(
            lambda: (
                acc._chat_transport.fallback_client is not None
                and bool(acc._chat_transport.fallback_client.chat_send_calls)
            )
        )
        acc._chat_transport.fallback_client._event_handlers["agent"](
            {
                "sessionKey": "sk-att",
                "stream": "assistant",
                "state": "final",
                "message": {"content": [{"text": "agent done"}]},
            }
        )
        await task
        assert "sk-att" not in acc._chat_transport.routed_sessions, (
            "agent final 唤醒后归属未释放"
        )
        assert acc.has_active_sessions is False
        await acc.close()

    # ─────────── R10：归属释放只认 v3 fallback 来源帧 ───────────

    @pytest.mark.asyncio
    async def test_a2a_terminal_spares_fallback_ownership_of_prefix_session(
        self, mock_bot_ws, stub_v3_patch
    ):
        """R10：A2A 主连接与 v3 fallback 共用事件 handler，但归属标记只属
        于 fallback 上的 run——A2A session（裸 key，自身是另一 fallback
        session 的超串，如 chat-10 ⊃ chat-1）的终态经主连接分发时，不得
        在归属子表 contains 误中前缀 session 而清掉 fallback 的归属。"""
        acc = self._acc_with_owned_a2a_main()
        acc._client._event_handlers["chat"] = acc._on_chat  # 双注册同形

        # chat-1：附件 run，后台在途于 v3 fallback
        await acc.send_message(
            "m1", session_key="chat-1", attachments=[{"k": 1}], wait_result=False
        )
        assert "chat-1" in acc._chat_transport.routed_sessions
        fallback = acc._chat_transport.fallback_client

        # chat-10：A2A 主连接 run（裸 key，包含 chat-1 为前缀）终结
        acc._client._event_handlers["chat"](
            {
                "sessionKey": "chat-10",
                "state": "final",
                "message": {"content": [{"type": "text", "text": "done"}]},
            }
        )

        assert "chat-1" in acc._chat_transport.routed_sessions, (
            "A2A session 终态误清了 fallback 前缀 session 的归属"
        )
        assert acc.tracks_session("chat-1") is True
        # chat-1 的取消仍必须路由到持有 run 的 v3 fallback
        await acc.chat_abort("chat-1")
        assert fallback.chat_abort_calls == [("chat-1", {"run_id": None})], (
            "跨 session 误清理后 abort 被错发 A2A 主连接"
        )
        acc._client.chat_abort.assert_not_awaited()
        await acc.close()


class _GatedV3Transport(_StubV3Transport):
    """慢建连替身：connect 闸在类级事件上——复现并发首发都看到
    `_v3_fallback is None` 的竞态窗口（R7-F4）。"""

    release: asyncio.Event
    gated_instances: list["_GatedV3Transport"] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _GatedV3Transport.gated_instances.append(self)

    async def connect(self) -> dict:
        await self.release.wait()
        self.connected = True
        return {"ok": True, "payload": {"server": {}}}


class _TerminalOnSendV3(_StubV3Transport):
    """终态先于 ACK 替身：chat_send 在返回前就让 WS 接收循环把主 run
    的 final 帧分派出去——复现"终态在 chat_send() 等待方恢复前到达"
    的时序（R8-F3）。"""

    async def chat_send(self, session_key, *args, **kwargs):
        self._event_handlers["chat"](
            {
                "sessionKey": session_key,
                "state": "final",
                "message": {"content": [{"type": "text", "text": "all done"}]},
            }
        )
        self.chat_send_calls.append((session_key, args, kwargs))
        return {"ok": True}


class TestV3FallbackConcurrentInit:
    """R7-F4：不同 session 并发首发附件——fallback 的检查/建连/发布必须
    互斥且锁内重查，连接唯一；否则两条连接下场是身份守卫吞掉首连断连
    （等待者悬挂）、末连断连误杀另一条连接的全部归属 run。"""

    @pytest.mark.asyncio
    async def test_concurrent_first_attachments_share_single_connection(
        self, mock_bot_ws
    ):
        gated = asyncio.Event()
        _GatedV3Transport.gated_instances = []
        _GatedV3Transport.release = gated
        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client."
            "BotWebSocketClient",
            _GatedV3Transport,
        ):
            acc = _a2a_acc_with_stub_main_client()

            t1 = asyncio.create_task(
                acc.send_message(
                    "m1", session_key="sk-1", attachments=[{"k": 1}], wait_result=False
                )
            )
            t2 = asyncio.create_task(
                acc.send_message(
                    "m2", session_key="sk-2", attachments=[{"k": 1}], wait_result=False
                )
            )
            # 两个任务都进入 _ensure_v3_fallback、闸在 connect 上——
            # 此时构造数即建连竞态的仲裁点（必须只建一条）。
            await asyncio.sleep(0.1)
            constructed = len(_GatedV3Transport.gated_instances)
            gated.set()
            await t1
            await t2

        assert constructed == 1, (
            f"并发首发构造了 {constructed} 条连接（互斥缺失，应为 1）"
        )
        single = acc._chat_transport.fallback_client
        assert single is not None, "fallback 未发布"
        # 两个 run 都必须走这唯一的连接，归属都指向它
        assert len(single.chat_send_calls) == 2, "并发首发被分散到多条连接"
        assert "sk-1" in acc._chat_transport.routed_sessions
        assert "sk-2" in acc._chat_transport.routed_sessions
        # 唯一连接断连 → 归属按预期整体收口（不再有"各连各的"误杀面）
        single.simulate_disconnect()
        assert acc._chat_transport.routed_sessions == {}, "断连后归属未整体收口"
        await acc.close()
