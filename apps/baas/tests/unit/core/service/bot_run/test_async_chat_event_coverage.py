"""test async chat event coverage; focused regression tests and fixtures."""

import asyncio
from unittest.mock import patch

import pytest

from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
)

from ._async_chat_coverage_support import (
    _setup_session_state,
)
from ._async_chat_coverage_support import (
    mock_bot_ws as mock_bot_ws,
)


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


class TestLogEvent:
    @pytest.mark.asyncio
    async def test_log_event_interaction_resolved_keeps_only_metadata(
        self, mock_bot_ws
    ):
        client = AsyncChatClient(uri="ws://host/ws")
        _setup_session_state(client, "sk1")

        with patch(
            "secbaas.community.core.service.bot_run._async_chat_events.logger"
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
            "secbaas.community.core.service.bot_run._async_chat_events.logger"
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
            "secbaas.community.core.service.bot_run._async_chat_events.logger"
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
            "secbaas.community.core.service.bot_run._async_chat_events.logger"
        ) as mock_logger:
            client._log_event(
                "chat",
                {"sessionKey": "sk1", "message": "secret-content"},
            )

        logged = str(mock_logger.info.call_args)
        assert "secret-content" not in logged
