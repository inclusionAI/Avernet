"""Coverage tests for DefaultStreamConverter — targets untested branches.

Covers: heartbeat SSE comment frame, error with errorKind, aborted,
agent with non-dict data, command_output non-claude, lifecycle invalid
phase, claude tool invalid type, claude tool start with args,
_claude_tool_result with dict, openclaw tool invalid phase,
delta with empty content, final with empty content.
"""

import json
import logging
import sys
from copy import deepcopy

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.sse import DefaultStreamConverter


def test_converter_uses_dedicated_logger():
    converter_module = sys.modules[DefaultStreamConverter.__module__]

    assert converter_module.logger.name == "bcn-converter"


def _data(event) -> dict:
    d = json.loads(event.data)
    d.pop("ts", None)
    return d


def _interaction_chunk(event: str, payload: dict) -> StreamChunk:
    envelope = {
        "type": "event",
        "event": event,
        "payload": payload,
    }
    if "seq" in payload:
        envelope["seq"] = payload["seq"]
    return StreamChunk(
        type="interaction",
        metadata={"event": event, "payload": envelope},
    )


def _capture_interaction_logs(monkeypatch, caplog) -> None:
    logger = logging.getLogger("test.interaction-converter")
    logger.propagate = True
    monkeypatch.setattr(
        "secbaas.community.core.service.sse._default_converter.logger",
        logger,
    )
    caplog.set_level(logging.WARNING, logger=logger.name)


class TestHeartbeat:
    def test_heartbeat_produces_comment_frame(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(type="heartbeat"),
            run_id="run-1",
        )
        assert event is not None
        assert event.event == ": heartbeat"
        assert event.data == ""
        assert event.to_sse() == ": heartbeat\n\n"


class TestErrorBranch:
    def test_error_with_content(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(type="error", content="something broke"),
            run_id="run-1",
        )
        assert event.event == "chat"
        d = _data(event)
        assert d["state"] == "error"
        assert d["errorMessage"] == "something broke"

    def test_error_empty_content_uses_default(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(type="error", content=""),
            run_id="run-1",
        )
        d = _data(event)
        assert d["errorMessage"] == "Unknown error"

    def test_error_with_error_kind_metadata(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="error",
                content="denied",
                metadata={"errorKind": "permission_denied"},
            ),
            run_id="run-1",
        )
        d = _data(event)
        assert d["errorKind"] == "permission_denied"

    def test_error_without_error_kind_metadata(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(type="error", content="oops", metadata=None),
            run_id="run-1",
        )
        d = _data(event)
        assert "errorKind" not in d


class TestAbortedBranch:
    def test_aborted_basic(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(type="aborted"),
            run_id="run-1",
        )
        assert event.event == "chat"
        d = _data(event)
        assert d["state"] == "aborted"

    def test_aborted_with_stop_reason(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="aborted",
                metadata={"stopReason": "user_cancelled"},
            ),
            run_id="run-1",
        )
        d = _data(event)
        assert d["stopReason"] == "user_cancelled"


class TestDeltaEmptyContent:
    def test_delta_empty_content_no_delta_text(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(type="delta", content=""),
            run_id="run-1",
        )
        d = _data(event)
        assert d["state"] == "delta"
        assert "deltaText" not in d


class TestFinalEmptyContent:
    def test_final_empty_content_no_message(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(type="final", content=""),
            run_id="run-1",
        )
        d = _data(event)
        assert d["state"] == "final"
        assert "message" not in d


class TestAgentNonDictData:
    def test_agent_data_not_dict_defaults_to_empty(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                metadata={
                    "engine_frame": {
                        "stream": "lifecycle",
                        "data": "not a dict",
                    }
                },
            ),
            run_id="run-1",
        )
        assert event is None

    def test_agent_data_none_defaults_to_empty(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                metadata={
                    "engine_frame": {
                        "stream": "lifecycle",
                        "data": None,
                    }
                },
            ),
            run_id="run-1",
        )
        assert event is None


class TestCommandOutputNonClaude:
    def test_non_claude_command_output_dropped(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                engine_type="openclaw",
                metadata={
                    "engine_frame": {
                        "stream": "command_output",
                        "data": {"phase": "end", "output": "ok"},
                    }
                },
            ),
            run_id="run-1",
        )
        assert event is None


class TestLifecycleInvalidPhase:
    def test_lifecycle_unknown_phase_dropped(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                metadata={
                    "engine_frame": {
                        "stream": "lifecycle",
                        "data": {"phase": "middle"},
                    }
                },
            ),
            run_id="run-1",
        )
        assert event is None

    def test_lifecycle_no_phase_dropped(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                metadata={
                    "engine_frame": {
                        "stream": "lifecycle",
                        "data": {},
                    }
                },
            ),
            run_id="run-1",
        )
        assert event is None


class TestClaudeToolInvalidType:
    def test_claude_tool_unknown_type_dropped(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                engine_type="claude_code",
                metadata={
                    "engine_frame": {
                        "stream": "tool",
                        "data": {"type": "unknown"},
                    }
                },
            ),
            run_id="run-1",
        )
        assert event is None

    def test_claude_tool_no_type_dropped(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                engine_type="claude_code",
                metadata={
                    "engine_frame": {
                        "stream": "tool",
                        "data": {},
                    }
                },
            ),
            run_id="run-1",
        )
        assert event is None


class TestClaudeToolStartWithArgs:
    def test_start_with_input(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                engine_type="claude_code",
                metadata={
                    "engine_frame": {
                        "stream": "tool",
                        "data": {
                            "type": "start",
                            "toolName": "Read",
                            "toolCallId": "tc-1",
                            "input": {"path": "/tmp/file.txt"},
                        },
                    }
                },
            ),
            run_id="run-1",
        )
        d = _data(event)
        assert d["stream"] == "tool"
        assert d["phase"] == "start"
        assert d["name"] == "Read"
        assert d["toolCallId"] == "tc-1"
        assert d["args"] == {"path": "/tmp/file.txt"}

    def test_start_without_input(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                engine_type="claude_code",
                metadata={
                    "engine_frame": {
                        "stream": "tool",
                        "data": {
                            "type": "start",
                            "toolName": "Bash",
                        },
                    }
                },
            ),
            run_id="run-1",
        )
        d = _data(event)
        assert "args" not in d


class TestClaudeToolResultDict:
    def test_dict_output_passed_directly(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                engine_type="claude_code",
                metadata={
                    "engine_frame": {
                        "stream": "tool",
                        "data": {
                            "type": "result",
                            "toolName": "Search",
                            "toolCallId": "tc-2",
                            "output": {"files": ["a.txt", "b.txt"]},
                        },
                    }
                },
            ),
            run_id="run-1",
        )
        d = _data(event)
        assert d["result"] == {"files": ["a.txt", "b.txt"]}


class TestOpenclawToolInvalidPhase:
    def test_invalid_phase_dropped(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                engine_type="openclaw",
                metadata={
                    "engine_frame": {
                        "stream": "tool",
                        "data": {"phase": "unknown"},
                    }
                },
            ),
            run_id="run-1",
        )
        assert event is None

    def test_no_phase_dropped(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                engine_type="openclaw",
                metadata={
                    "engine_frame": {
                        "stream": "tool",
                        "data": {},
                    }
                },
            ),
            run_id="run-1",
        )
        assert event is None


class TestUsageChunkDropped:
    def test_usage_type_returns_none(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(type="usage", content=""),
            run_id="run-1",
        )
        assert event is None


class TestUnknownChunkTypeDropped:
    def test_unknown_type_returns_none(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(type="unknown_type"),
            run_id="run-1",
        )
        assert event is None


class TestEngineNameFallback:
    def test_engine_type_none_falls_back_to_metadata(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                metadata={
                    "engine_frame": {
                        "stream": "command_output",
                        "data": {"phase": "end", "output": "ok"},
                    },
                    "engine": "claude_code",
                },
            ),
            run_id="run-1",
        )
        assert event is not None
        assert event.event == "agent"

    def test_engine_type_empty_string_falls_back_to_metadata(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="agent",
                engine_type="",
                metadata={
                    "engine_frame": {
                        "stream": "command_output",
                        "data": {"phase": "end", "output": "ok"},
                    },
                    "engineType": "claude_code",
                },
            ),
            run_id="run-1",
        )
        assert event is not None


class TestInteractionBranch:
    def test_exec_requested_is_flat_bcn_interaction(self):
        converter = DefaultStreamConverter()
        payload = {
            "interactionId": "int-1",
            "id": "engine-id",
            "runId": "engine-run",
            "seq": 91,
            "ts": 123456,
            "status": "pending",
            "kind": "exec",
            "phase": "engine-phase-must-not-win",
            "title": "Approve command",
            "description": "The command needs approval",
            "toolCallId": "tool-1",
            "cwd": "/workspace",
            "command": "make test",
            "options": [
                {
                    "label": "Run",
                    "decision": "proceed",
                    "value": "ignored-value",
                    "description": "ignored-description",
                    "optionId": "opt-1",
                },
                {"label": "Cancel", "value": "cancel"},
            ],
        }
        event = converter.convert(
            _interaction_chunk("interaction.requested", payload),
            run_id="bcn-run",
        )
        assert event is not None
        assert event.event == "interaction"
        assert event.id == "1"
        raw_data = json.loads(event.data)
        assert isinstance(raw_data["ts"], int)
        assert raw_data["ts"] != payload["ts"]
        assert _data(event) == {
            "runId": "bcn-run",
            "seq": 1,
            "interactionId": "int-1",
            "kind": "exec",
            "phase": "requested",
            "title": "Approve command",
            "description": "The command needs approval",
            "toolCallId": "tool-1",
            "cwd": "/workspace",
            "command": "make test",
            "options": [
                {"label": "Run", "decision": "proceed"},
                {"label": "Cancel", "decision": "cancel"},
            ],
        }

    def test_exec_requested_uses_id_and_subject_tool_call_id_fallbacks(self):
        converter = DefaultStreamConverter()
        payload = {
            "id": "int-fallback",
            "kind": "exec",
            "subject": {"toolCallId": "tool-fallback"},
            "command": "pwd",
        }
        event = converter.convert(
            _interaction_chunk("interaction.requested", payload),
            run_id="run-1",
        )
        assert event is not None
        data = _data(event)
        assert data["interactionId"] == "int-fallback"
        assert data["toolCallId"] == "tool-fallback"
        assert data["phase"] == "requested"
        assert "title" not in data
        assert "description" not in data
        assert "cwd" not in data
        assert data["options"] == [
            {"label": "Allow once", "decision": "allow-once"},
            {"label": "Allow always", "decision": "allow-always"},
            {"label": "Deny", "decision": "deny"},
        ]

    def test_exec_requested_without_options_uses_bcn_default_options(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-1",
                    "kind": "exec",
                    "command": "pwd",
                },
            ),
            run_id="run-1",
        )
        assert event is not None
        assert _data(event)["options"] == [
            {"label": "Allow once", "decision": "allow-once"},
            {"label": "Allow always", "decision": "allow-always"},
            {"label": "Deny", "decision": "deny"},
        ]

    def test_phase_conflict_uses_event_phase_and_warns(self, monkeypatch, caplog):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-1",
                    "kind": "exec",
                    "phase": "resolved",
                    "command": "pwd",
                },
            ),
            run_id="run-1",
        )
        assert event is not None
        assert _data(event)["phase"] == "requested"
        assert "Interaction conversion warning" in caplog.text
        assert "field_path=phase" in caplog.text
        assert "error_type=phase_conflict" in caplog.text

    def test_exec_option_empty_decision_falls_back_to_value(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-1",
                    "kind": "exec",
                    "command": "echo ok",
                    "options": [
                        {"label": "Continue", "decision": "", "value": "proceed"}
                    ],
                },
            ),
            run_id="run-1",
        )
        assert event is not None
        assert _data(event)["options"] == [{"label": "Continue", "decision": "proceed"}]

    def test_exec_option_non_string_decision_falls_back_to_value(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-1",
                    "kind": "exec",
                    "command": "echo ok",
                    "options": [
                        {
                            "label": "Continue",
                            "decision": {"unexpected": True},
                            "value": "proceed",
                        }
                    ],
                },
            ),
            run_id="run-1",
        )
        assert event is not None
        assert _data(event)["options"] == [{"label": "Continue", "decision": "proceed"}]

    def test_exec_mixed_options_filter_and_duplicate_decision_last_wins(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-mixed",
                    "kind": "exec",
                    "command": "make test",
                    "options": [
                        "not-an-object",
                        {"label": "Once", "decision": "", "value": "allow-once"},
                        {"label": "Deny", "decision": 42, "value": "deny"},
                        {"label": "Missing decision"},
                        {"label": "Once replacement", "decision": "allow-once"},
                    ],
                },
            ),
            run_id="run-log",
        )

        assert event is not None
        assert _data(event)["options"] == [
            {"label": "Once replacement", "decision": "allow-once"},
            {"label": "Deny", "decision": "deny"},
        ]
        assert "field_path=payload.options[0]" in caplog.text
        assert "field_path=payload.options[3]" in caplog.text
        assert "field_path=payload.options[4].decision" in caplog.text
        assert "error_type=duplicate_option_decision" in caplog.text

    def test_missing_or_invalid_exec_command_is_omitted(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        for index, command in enumerate((None, 42, "", "   ")):
            converter = DefaultStreamConverter()
            payload = {
                "interactionId": f"int-command-{index}",
                "kind": "exec",
                "title": "Sensitive title",
            }
            if index:
                payload["command"] = command
            interaction = converter.convert(
                _interaction_chunk("interaction.requested", payload),
                run_id="run-log",
            )
            chat = converter.convert(
                StreamChunk(type="delta", content="after invalid exec"),
                run_id="run-log",
            )

            assert interaction is not None
            assert interaction.event == "interaction"
            interaction_data = _data(interaction)
            assert interaction_data["seq"] == 1
            assert "command" not in interaction_data
            assert interaction_data["options"] == [
                {"label": "Allow once", "decision": "allow-once"},
                {"label": "Allow always", "decision": "allow-always"},
                {"label": "Deny", "decision": "deny"},
            ]
            assert chat is not None
            assert chat.id == "2"
            assert _data(chat)["seq"] == 2

        assert "field_path=payload.command" not in caplog.text
        assert "Sensitive title" not in caplog.text

    def test_invalid_explicit_exec_options_drop_interaction_without_seq(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        cases = (
            None,
            {"label": "not-an-array"},
            [],
            ["bad", {"label": "Missing decision"}, {"decision": "missing-label"}],
        )
        for index, options in enumerate(cases):
            converter = DefaultStreamConverter()
            interaction = converter.convert(
                _interaction_chunk(
                    "interaction.requested",
                    {
                        "interactionId": f"int-options-{index}",
                        "kind": "exec",
                        "command": "make test",
                        "options": options,
                    },
                ),
                run_id="run-log",
            )
            chat = converter.convert(
                StreamChunk(type="delta", content="after invalid exec options"),
                run_id="run-log",
            )

            assert interaction is None
            assert chat is not None
            assert chat.id == "1"
            assert _data(chat)["seq"] == 1

        assert caplog.text.count("error_type=no_valid_options") == len(cases)
        assert "after invalid exec options" not in caplog.text

    def test_exec_converter_exception_is_dropped_without_consuming_seq(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)

        def _raise_sensitive_error(*args, **kwargs):
            raise RuntimeError("sensitive-message")

        monkeypatch.setattr(
            "secbaas.community.core.service.sse._default_converter."
            "_transform_exec_requested",
            _raise_sensitive_error,
            raising=False,
        )
        converter = DefaultStreamConverter()
        interaction = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-1",
                    "kind": "exec",
                    "command": "sensitive-command",
                },
            ),
            run_id="run-1",
        )
        chat = converter.convert(
            StreamChunk(type="delta", content="ok"),
            run_id="run-1",
        )

        assert interaction is None
        assert chat is not None
        assert chat.event == "chat"
        assert chat.id == "1"
        assert _data(chat)["seq"] == 1
        assert "error_type=RuntimeError" in caplog.text
        assert "sensitive-message" not in caplog.text
        assert "sensitive-command" not in caplog.text

    def test_missing_interaction_id_warns_without_business_data(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.resolved",
                {
                    "kind": "ask_user",
                    "title": "sensitive-title",
                    "description": "sensitive-description",
                    "command": "sensitive-command",
                    "answers": {"secret": "sensitive-answer"},
                },
            ),
            run_id="run-log",
        )

        assert event is None
        assert "run_id=run-log" in caplog.text
        assert "interaction_id=" in caplog.text
        assert "kind=ask_user" in caplog.text
        assert "field_path=interactionId" in caplog.text
        assert "error_type=missing_required_field" in caplog.text
        for sensitive_value in (
            "sensitive-title",
            "sensitive-description",
            "sensitive-command",
            "sensitive-answer",
        ):
            assert sensitive_value not in caplog.text

    def test_missing_kind_warns_without_business_data(self, monkeypatch, caplog):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-log",
                    "title": "sensitive-title",
                    "description": "sensitive-description",
                    "command": "sensitive-command",
                },
            ),
            run_id="run-log",
        )

        assert event is None
        assert "run_id=run-log" in caplog.text
        assert "interaction_id=int-log" in caplog.text
        assert "kind=" in caplog.text
        assert "field_path=kind" in caplog.text
        assert "error_type=missing_required_field" in caplog.text
        assert "sensitive-title" not in caplog.text
        assert "sensitive-description" not in caplog.text
        assert "sensitive-command" not in caplog.text

    def test_exec_resolved_is_flat_and_allowlisted(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.resolved",
                {
                    "interactionId": "int-1",
                    "kind": "exec",
                    "phase": "requested",
                    "decision": "proceed",
                    "idempotencyKey": "idem-1",
                    "status": "completed",
                    "runId": "engine-run",
                    "seq": 92,
                    "ts": 123457,
                },
            ),
            run_id="bcn-run",
        )
        assert event is not None
        assert event.event == "interaction"
        assert _data(event) == {
            "runId": "bcn-run",
            "seq": 1,
            "interactionId": "int-1",
            "kind": "exec",
            "phase": "resolved",
            "decision": "proceed",
            "idempotencyKey": "idem-1",
        }

    def test_ask_user_resolved_copies_action_and_answers(self):
        converter = DefaultStreamConverter()
        answers = {"environment": "staging"}
        event = converter.convert(
            _interaction_chunk(
                "interaction.resolved",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "action": "submit",
                    "answers": answers,
                },
            ),
            run_id="run-1",
        )
        assert event is not None
        data = _data(event)
        assert data["phase"] == "resolved"
        assert data["action"] == "submit"
        assert data["answers"] == answers

    def test_invalid_envelope_is_dropped(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            StreamChunk(
                type="interaction",
                metadata={
                    "event": "interaction.requested",
                    "payload": {
                        "type": "event",
                        "event": "interaction.requested",
                        "payload": [],
                    },
                },
            ),
            run_id="run-1",
        )
        assert event is None

    def test_unknown_kind_is_dropped(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {"interactionId": "int-1", "kind": "future_kind"},
            ),
            run_id="run-1",
        )
        assert event is None

    def test_dropped_interactions_do_not_consume_chat_sequence(self):
        converter = DefaultStreamConverter()
        first_chat = converter.convert(
            StreamChunk(type="delta", content="first"),
            run_id="run-1",
        )
        invalid_envelope = converter.convert(
            StreamChunk(
                type="interaction",
                metadata={
                    "event": "interaction.requested",
                    "payload": {
                        "type": "event",
                        "event": "interaction.requested",
                        "payload": [],
                    },
                },
            ),
            run_id="run-1",
        )
        unknown_kind = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {"interactionId": "int-unknown", "kind": "future_kind"},
            ),
            run_id="run-1",
        )
        second_chat = converter.convert(
            StreamChunk(type="delta", content="second"),
            run_id="run-1",
        )

        assert first_chat is not None
        assert first_chat.id == "1"
        assert invalid_envelope is None
        assert unknown_kind is None
        assert second_chat is not None
        assert second_chat.id == "2"
        assert _data(second_chat)["seq"] == 2


class TestInteractionStreamInvariants:
    def test_agent_interaction_and_chat_share_sequence_and_bcn_run_id(self):
        converter = DefaultStreamConverter()
        run_id = "bcn-run"

        agent = converter.convert(
            StreamChunk(
                type="agent",
                metadata={
                    "engine_frame": {
                        "stream": "lifecycle",
                        "data": {"phase": "start", "model": "test-model"},
                    }
                },
            ),
            run_id=run_id,
        )
        interaction = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-mode",
                    "kind": "mode_switch",
                    "options": [{"label": "Stay", "decision": "stay"}],
                },
            ),
            run_id=run_id,
        )
        chat = converter.convert(
            StreamChunk(type="delta", content="hello"),
            run_id=run_id,
        )

        assert agent is not None
        assert interaction is not None
        assert chat is not None
        assert [event.event for event in (agent, interaction, chat)] == [
            "agent",
            "interaction",
            "chat",
        ]
        assert [event.id for event in (agent, interaction, chat)] == ["1", "2", "3"]
        for expected_seq, event in enumerate((agent, interaction, chat), start=1):
            data = _data(event)
            assert data["runId"] == run_id
            assert data["seq"] == expected_seq
            assert event.id == str(data["seq"])
        assert _data(interaction) == {
            "runId": run_id,
            "seq": 2,
            "interactionId": "int-mode",
            "kind": "mode_switch",
            "phase": "requested",
            "options": [{"label": "Stay", "decision": "stay"}],
        }

    def test_unknown_interaction_between_agent_and_chat_does_not_consume_seq(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        agent = converter.convert(
            StreamChunk(
                type="agent",
                metadata={
                    "engine_frame": {
                        "stream": "lifecycle",
                        "data": {"phase": "start"},
                    }
                },
            ),
            run_id="bcn-run",
        )
        dropped = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-unknown",
                    "kind": "future_kind",
                    "title": "sensitive-title",
                },
            ),
            run_id="bcn-run",
        )
        chat = converter.convert(
            StreamChunk(type="delta", content="after interaction"),
            run_id="bcn-run",
        )

        assert agent is not None
        assert agent.id == "1"
        assert dropped is None
        assert chat is not None
        assert chat.id == "2"
        assert _data(chat)["seq"] == 2
        assert "error_type=unsupported_kind" in caplog.text
        assert "sensitive-title" not in caplog.text

    def test_mode_transition_resolved_maps_actual_engine_event_to_common_path(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "mode_transition.resolved",
                {
                    "interactionId": "int-mode",
                    "kind": "mode_switch",
                    "phase": "proceeded",
                    "decision": "proceed",
                    "status": "resolved",
                    "options": [
                        {"label": "Continue to execution", "decision": "proceed"}
                    ],
                },
            ),
            run_id="bcn-run",
        )

        assert event is not None
        assert event.event == "interaction"
        assert _data(event) == {
            "runId": "bcn-run",
            "seq": 1,
            "interactionId": "int-mode",
            "kind": "mode_switch",
            "phase": "resolved",
            "decision": "proceed",
        }

    def test_conversion_does_not_mutate_engine_envelope_or_nested_payload(self):
        converter = DefaultStreamConverter()
        payload = {
            "interactionId": "int-ask",
            "kind": "ask_user",
            "subject": {
                "toolCallId": "subject-tool-call",
                "internal": {"secret": "subject-internal"},
            },
            "questions": [
                {
                    "header": "Environment",
                    "question": "Where should this deploy?",
                    "multiSelect": False,
                    "allowOther": False,
                    "options": [
                        {
                            "label": "Staging",
                            "decision": "staging",
                            "value": "legacy-staging",
                            "internal": {"secret": "option-internal"},
                        }
                    ],
                }
            ],
            "internal": {"secret": "payload-internal"},
            "status": "pending",
        }
        chunk = _interaction_chunk("interaction.requested", payload)
        original_metadata = deepcopy(chunk.metadata)
        original_payload = deepcopy(payload)

        event = converter.convert(chunk, run_id="bcn-run")

        assert event is not None
        assert chunk.metadata == original_metadata
        assert payload == original_payload
        assert _data(event) == {
            "runId": "bcn-run",
            "seq": 1,
            "interactionId": "int-ask",
            "kind": "ask_user",
            "phase": "requested",
            "toolCallId": "subject-tool-call",
            "questions": [
                {
                    "questionId": "question_1",
                    "question": "Where should this deploy?",
                    "header": "Environment",
                    "options": [{"label": "Staging", "value": "staging"}],
                    "multiSelect": False,
                    "allowOther": False,
                }
            ],
        }


class TestAskUserInteraction:
    def test_secret_question_aliases_are_ignored_and_interaction_is_delivered(
        self,
    ):
        for secret_key, secret_value in (
            ("secret", True),
            ("secret", False),
            ("isSecret", None),
        ):
            converter = DefaultStreamConverter()
            interaction = converter.convert(
                _interaction_chunk(
                    "interaction.requested",
                    {
                        "interactionId": f"int-{secret_key}-{secret_value}",
                        "kind": "ask_user",
                        "description": "Sensitive description",
                        "questions": [
                            {
                                "header": "safe",
                                "question": "Safe-looking question",
                            },
                            {
                                "header": "secret",
                                "question": "Sensitive secret question body",
                                secret_key: secret_value,
                            },
                        ],
                    },
                ),
                run_id="run-log",
            )
            chat = converter.convert(
                StreamChunk(type="delta", content="after secret question"),
                run_id="run-log",
            )

            assert interaction is not None
            assert _data(interaction)["questions"] == [
                {
                    "questionId": "question_1",
                    "header": "safe",
                    "question": "Safe-looking question",
                },
                {
                    "questionId": "question_2",
                    "header": "secret",
                    "question": "Sensitive secret question body",
                },
            ]
            assert chat is not None
            assert interaction.id == "1"
            assert chat.id == "2"
            assert _data(chat)["seq"] == 2

    def test_maps_complete_questions_and_options(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "phase": "requested",
                    "title": "Choose deployment",
                    "description": "Select the deployment settings",
                    "toolCallId": "tool-ask",
                    "questions": [
                        {
                            "header": "Environment",
                            "questionId": "engine-question-id",
                            "question": "Where should this deploy?",
                            "allowOther": True,
                            "multiSelect": False,
                            "options": [
                                {
                                    "label": "Production",
                                    "decision": "prod",
                                    "value": "ignored-value",
                                    "description": "Deploy to production",
                                    "optionId": "engine-option-id",
                                },
                                {
                                    "label": "Staging",
                                    "value": "staging",
                                },
                            ],
                        }
                    ],
                },
            ),
            run_id="bcn-run",
        )

        assert event is not None
        assert event.event == "interaction"
        assert _data(event) == {
            "runId": "bcn-run",
            "seq": 1,
            "interactionId": "int-ask",
            "kind": "ask_user",
            "phase": "requested",
            "title": "Choose deployment",
            "description": "Select the deployment settings",
            "toolCallId": "tool-ask",
            "questions": [
                {
                    "header": "Environment",
                    "questionId": "question_1",
                    "question": "Where should this deploy?",
                    "allowOther": True,
                    "multiSelect": False,
                    "options": [
                        {
                            "label": "Production",
                            "value": "prod",
                            "description": "Deploy to production",
                        },
                        {"label": "Staging", "value": "staging"},
                    ],
                }
            ],
        }

    def test_missing_header_drops_entire_interaction_without_seq(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        interaction = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "questions": [{"question": "Sensitive optional question"}],
                },
            ),
            run_id="run-1",
        )
        chat = converter.convert(
            StreamChunk(type="delta", content="after invalid interaction"),
            run_id="run-1",
        )

        assert interaction is None
        assert chat is not None
        assert chat.id == "1"
        assert _data(chat)["seq"] == 1
        assert "field_path=payload.questions[0].header" in caplog.text
        assert "error_type=invalid_header" in caplog.text
        assert "Sensitive optional question" not in caplog.text

    def test_empty_header_drops_entire_interaction(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "questions": [
                        {
                            "header": "   ",
                            "question": "Sensitive empty-header question",
                        },
                    ],
                },
            ),
            run_id="run-log",
        )

        assert event is None
        assert "field_path=payload.questions[0].header" in caplog.text
        assert "error_type=invalid_header" in caplog.text
        assert "Sensitive empty-header question" not in caplog.text

    def test_duplicate_headers_keep_distinct_indexed_question_ids(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "questions": [
                        {
                            "header": "Sensitive duplicate header",
                            "question": "Sensitive first question",
                        },
                        {
                            "header": "Sensitive duplicate header",
                            "question": "Sensitive second question",
                        },
                    ],
                },
            ),
            run_id="run-log",
        )

        assert event is not None
        questions = _data(event)["questions"]
        assert questions == [
            {
                "header": "Sensitive duplicate header",
                "questionId": "question_1",
                "question": "Sensitive first question",
            },
            {
                "header": "Sensitive duplicate header",
                "questionId": "question_2",
                "question": "Sensitive second question",
            },
        ]
        assert "field_path=payload.questions[1].header" in caplog.text
        assert "error_type=duplicate_header" in caplog.text
        for sensitive_value in (
            "Sensitive duplicate header",
            "Sensitive first question",
            "Sensitive second question",
        ):
            assert sensitive_value not in caplog.text

    def test_engine_question_ids_do_not_override_provider_indexed_identity(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "questions": [
                        {
                            "questionId": "engine-owned-id",
                            "header": "Display header",
                            "question": "Question body",
                        },
                    ],
                },
            ),
            run_id="run-log",
        )

        assert event is not None
        assert _data(event)["questions"] == [
            {
                "header": "Display header",
                "questionId": "question_1",
                "question": "Question body",
            }
        ]

    def test_malformed_children_are_skipped_and_warnings_are_sanitized(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "questions": [
                        "Sensitive non-object question",
                        {"header": "Sensitive missing-question header"},
                        {
                            "header": "Sensitive valid header",
                            "question": "Sensitive valid question",
                            "options": [
                                "Sensitive non-object option",
                                {
                                    "decision": "sensitive-decision",
                                    "description": "Sensitive missing-label description",
                                },
                                {"label": "Sensitive missing-decision label"},
                                {
                                    "label": "Sensitive valid label",
                                    "value": "valid-value",
                                },
                            ],
                        },
                        {
                            "header": "Sensitive invalid-options header",
                            "question": "Sensitive invalid-options question",
                            "options": {"label": "Sensitive nested label"},
                        },
                    ],
                },
            ),
            run_id="run-log",
        )

        assert event is not None
        assert _data(event)["questions"] == [
            {
                "header": "Sensitive valid header",
                "questionId": "question_3",
                "question": "Sensitive valid question",
                "options": [
                    {
                        "label": "Sensitive missing-decision label",
                        "value": "Sensitive missing-decision label",
                    },
                    {"label": "Sensitive valid label", "value": "valid-value"},
                ],
            },
        ]
        for expected_warning in (
            "field_path=payload.questions[0]",
            "field_path=payload.questions[1].question",
            "field_path=payload.questions[2].options[0]",
            "field_path=payload.questions[2].options[1]",
            "field_path=payload.questions[3].options",
        ):
            assert expected_warning in caplog.text
        assert "error_type=invalid_type" in caplog.text
        assert "error_type=missing_required_field" in caplog.text
        assert "field_path=payload.questions[2].options[2]" in caplog.text
        assert "error_type=legacy_label_fallback" in caplog.text
        for sensitive_value in (
            "Sensitive non-object question",
            "Sensitive missing-question header",
            "Sensitive valid header",
            "Sensitive valid question",
            "Sensitive non-object option",
            "sensitive-decision",
            "Sensitive missing-label description",
            "Sensitive missing-decision label",
            "Sensitive valid label",
            "Sensitive invalid-options header",
            "Sensitive invalid-options question",
            "Sensitive nested label",
        ):
            assert sensitive_value not in caplog.text

    def test_real_engine_label_only_and_mixed_options_are_bcn_valid(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        interaction = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-real-engine-ask",
                    "kind": "ask_user",
                    "questions": [
                        {
                            "question": "Choose a fruit?",
                            "header": "Fruit",
                            "options": [
                                {"label": "Apple", "description": "Red"},
                                {"label": "Banana", "description": "Yellow"},
                                {
                                    "label": "New shape",
                                    "decision": "new-decision",
                                    "value": "ignored-value",
                                },
                                {"label": "Value shape", "value": "new-value"},
                                {"label": "Apple", "description": "Green"},
                            ],
                            "multiSelect": False,
                        },
                    ],
                },
            ),
            run_id="run-log",
        )
        chat = converter.convert(
            StreamChunk(type="delta", content="after ask user"),
            run_id="run-log",
        )

        assert interaction is not None
        assert _data(interaction)["questions"] == [
            {
                "header": "Fruit",
                "questionId": "question_1",
                "question": "Choose a fruit?",
                "options": [
                    {"label": "Apple", "value": "Apple", "description": "Green"},
                    {
                        "label": "Banana",
                        "value": "Banana",
                        "description": "Yellow",
                    },
                    {"label": "New shape", "value": "new-decision"},
                    {"label": "Value shape", "value": "new-value"},
                ],
                "multiSelect": False,
            }
        ]
        assert chat is not None
        assert interaction.id == "1"
        assert chat.id == "2"
        assert _data(chat)["seq"] == 2
        assert caplog.text.count("error_type=legacy_label_fallback") == 3
        assert "error_type=duplicate_option_value" in caplog.text
        for business_text in (
            "Choose a fruit?",
            "Apple",
            "Banana",
            "New shape",
            "Value shape",
            "Red",
            "Yellow",
            "Green",
        ):
            assert business_text not in caplog.text

    def test_option_decision_falls_back_to_valid_value(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "questions": [
                        {
                            "header": "Action",
                            "question": "Choose an action",
                            "options": [
                                {
                                    "label": "Empty decision",
                                    "decision": "",
                                    "value": "empty-fallback",
                                },
                                {
                                    "label": "Invalid decision",
                                    "decision": {"unexpected": True},
                                    "value": "invalid-fallback",
                                },
                            ],
                        }
                    ],
                },
            ),
            run_id="run-1",
        )

        assert event is not None
        assert _data(event)["questions"][0]["options"] == [
            {"label": "Empty decision", "value": "empty-fallback"},
            {"label": "Invalid decision", "value": "invalid-fallback"},
        ]

    def test_missing_nonlist_and_all_invalid_questions_are_dropped_without_seq(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()

        missing = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {"interactionId": "int-missing", "kind": "ask_user"},
            ),
            run_id="run-log",
        )
        invalid = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-invalid",
                    "kind": "ask_user",
                    "questions": {"question": "Sensitive hidden question"},
                },
            ),
            run_id="run-log",
        )
        all_invalid = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-all-invalid",
                    "kind": "ask_user",
                    "questions": [
                        "Sensitive invalid child",
                        {"header": "Sensitive missing body"},
                    ],
                },
            ),
            run_id="run-log",
        )
        chat = converter.convert(
            StreamChunk(type="delta", content="after invalid interactions"),
            run_id="run-log",
        )

        assert missing is None
        assert invalid is None
        assert all_invalid is None
        assert chat is not None
        assert chat.id == "1"
        assert _data(chat)["seq"] == 1
        assert caplog.text.count("field_path=payload.questions") >= 3
        assert caplog.text.count("error_type=invalid_type") >= 2
        assert "error_type=no_valid_questions" in caplog.text
        assert "Sensitive hidden question" not in caplog.text
        assert "Sensitive invalid child" not in caplog.text
        assert "Sensitive missing body" not in caplog.text

    def test_invalid_explicit_options_drop_question_and_interaction(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()

        for index, options in enumerate(
            (
                {"label": "not-an-array"},
                [],
                [
                    "not-an-object",
                    {"label": 42, "value": "invalid-label-type"},
                    {"value": "missing-label"},
                ],
            )
        ):
            event = converter.convert(
                _interaction_chunk(
                    "interaction.requested",
                    {
                        "interactionId": f"int-options-{index}",
                        "kind": "ask_user",
                        "questions": [
                            {
                                "header": "Approval",
                                "question": "Sensitive approval question",
                                "options": options,
                            }
                        ],
                    },
                ),
                run_id="run-log",
            )
            assert event is None

        chat = converter.convert(
            StreamChunk(type="delta", content="after invalid options"),
            run_id="run-log",
        )
        assert chat is not None
        assert chat.id == "1"
        assert "field_path=payload.questions[0].options" in caplog.text
        assert "error_type=no_valid_options" in caplog.text
        assert "error_type=no_valid_questions" in caplog.text
        assert "Sensitive approval question" not in caplog.text
        assert "invalid-label-type" not in caplog.text
        assert "missing-label" not in caplog.text

    def test_limits_questions_to_four_and_keeps_first_four(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        questions = [
            {"header": f"q{index}", "question": f"Question {index}"}
            for index in range(1, 6)
        ]
        questions.append({"header": "q1", "question": "Replacement question"})

        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "questions": questions,
                },
            ),
            run_id="run-1",
        )

        assert event is not None
        assert _data(event)["questions"] == [
            {
                "header": "q1",
                "questionId": "question_1",
                "question": "Question 1",
            },
            {"header": "q2", "questionId": "question_2", "question": "Question 2"},
            {"header": "q3", "questionId": "question_3", "question": "Question 3"},
            {"header": "q4", "questionId": "question_4", "question": "Question 4"},
        ]
        assert "field_path=payload.questions[4]" in caplog.text
        assert "error_type=max_items_exceeded" in caplog.text
        assert "field_path=payload.questions[5].header" in caplog.text
        assert "error_type=duplicate_header" in caplog.text

    def test_limits_unique_options_to_four_and_last_duplicate_wins(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        options = [
            {"label": f"Option {index}", "value": f"v{index}"} for index in range(1, 6)
        ]
        options.append({"label": "Replacement option", "value": "v1"})

        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "questions": [
                        {
                            "header": "Action",
                            "question": "Choose an action",
                            "options": options,
                        }
                    ],
                },
            ),
            run_id="run-log",
        )

        assert event is not None
        assert _data(event)["questions"][0]["options"] == [
            {"label": "Replacement option", "value": "v1"},
            {"label": "Option 2", "value": "v2"},
            {"label": "Option 3", "value": "v3"},
            {"label": "Option 4", "value": "v4"},
        ]
        assert "field_path=payload.questions[0].options[4]" in caplog.text
        assert "error_type=max_items_exceeded" in caplog.text
        assert "field_path=payload.questions[0].options[5].value" in caplog.text
        assert "error_type=duplicate_option_value" in caplog.text

    def test_boolean_fields_and_free_text_allow_other_follow_bcn_contract(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "questions": [
                        {
                            "header": "with-options",
                            "question": "With options",
                            "options": [{"label": "Yes", "value": "yes"}],
                            "allowOther": False,
                            "multiSelect": False,
                        },
                        {
                            "header": "invalid-bools",
                            "question": "Invalid bools",
                            "options": [{"label": "No", "value": "no"}],
                            "allowOther": "false",
                            "multiSelect": 0,
                        },
                        {
                            "header": "free-true",
                            "question": "Free text true",
                            "allowOther": True,
                            "multiSelect": False,
                        },
                        {
                            "header": "free-false",
                            "question": "Free text false",
                            "options": None,
                            "allowOther": False,
                            "multiSelect": True,
                        },
                    ],
                },
            ),
            run_id="run-log",
        )

        assert event is not None
        assert _data(event)["questions"] == [
            {
                "header": "with-options",
                "questionId": "question_1",
                "question": "With options",
                "options": [{"label": "Yes", "value": "yes"}],
                "allowOther": False,
                "multiSelect": False,
            },
            {
                "header": "invalid-bools",
                "questionId": "question_2",
                "question": "Invalid bools",
                "options": [{"label": "No", "value": "no"}],
            },
            {
                "header": "free-true",
                "questionId": "question_3",
                "question": "Free text true",
                "multiSelect": False,
            },
        ]
        assert "field_path=payload.questions[1].allowOther" in caplog.text
        assert "field_path=payload.questions[1].multiSelect" in caplog.text
        assert "error_type=invalid_type" in caplog.text
        assert "field_path=payload.questions[2].allowOther" in caplog.text
        assert "error_type=unsupported_without_options" in caplog.text
        assert "field_path=payload.questions[3].options" in caplog.text
        assert "error_type=no_valid_options" in caplog.text

    def test_null_options_drop_interaction_without_consuming_seq(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        interaction = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-null-options",
                    "kind": "ask_user",
                    "questions": [
                        {
                            "header": "Approval",
                            "question": "Sensitive approval question",
                            "options": None,
                        }
                    ],
                },
            ),
            run_id="run-log",
        )
        chat = converter.convert(
            StreamChunk(type="delta", content="after null options"),
            run_id="run-log",
        )

        assert interaction is None
        assert chat is not None
        assert chat.id == "1"
        assert _data(chat)["seq"] == 1
        assert "field_path=payload.questions[0].options" in caplog.text
        assert "error_type=invalid_type" in caplog.text
        assert "error_type=no_valid_options" in caplog.text
        assert "error_type=no_valid_questions" in caplog.text
        assert "Sensitive approval question" not in caplog.text

    def test_whitespace_fields_use_only_valid_label_fallbacks(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-ask",
                    "kind": "ask_user",
                    "questions": [
                        {"header": "blank-question", "question": "   "},
                        {
                            "header": "valid-question",
                            "question": "Choose",
                            "options": [
                                {"label": "   ", "value": "invalid-label"},
                                {"label": "Invalid value", "value": "\t"},
                                {"label": "Valid", "value": " valid "},
                            ],
                        },
                    ],
                },
            ),
            run_id="run-1",
        )

        assert event is not None
        assert _data(event)["questions"] == [
            {
                "header": "valid-question",
                "questionId": "question_2",
                "question": "Choose",
                "options": [
                    {"label": "Invalid value", "value": "Invalid value"},
                    {"label": "Valid", "value": " valid "},
                ],
            }
        ]


_CAPTURED_MODE_SWITCH_PAYLOAD = {
    "interactionId": "int:14136f21-01a0-42ed-8000-14136f2102ed",
    "id": "int:14136f21-01a0-42ed-8000-14136f2102ed",
    "runId": "a007d8cedca74d1dac159139d46af06f",
    "sessionKey": "engine-session-1",
    "kind": "mode_switch",
    "interactionType": "mode_switch",
    "phase": "requested",
    "status": "pending",
    "title": "Plan mode transition",
    "description": "Transition from plan to execute",
    "subject": {
        "type": "mode",
        "toolName": "ExitPlanMode",
        "toolCallId": "subject-tool-call",
        "fromMode": "subject-plan",
        "toMode": "subject-execute",
    },
    "toolName": "ExitPlanMode",
    "toolCallId": "top-level-tool-call",
    "options": [
        {
            "value": "legacy-proceed",
            "decision": "proceed",
            "label": "Continue to execution",
            "recommended": True,
            "targetMode": "execute",
            "optionId": "opt-0",
        },
        {
            "value": "stay",
            "label": "Stay in planning",
            "optionId": "opt-1",
        },
    ],
    "fromMode": "plan",
    "toMode": "execute",
    "inputSchema": {"type": "choices", "multiSelect": False},
    "uiHints": {"variant": "plan", "severity": "info"},
    "createdAtMs": 1787043213089,
    "expiresAtMs": 1787043513089,
    "schemaVersion": 2,
    "actionable": True,
    "lifecycleState": "pending",
    "deliveryStatus": "not_dispatched",
    "stateVersion": 1,
    "sessionStateVersion": 16,
    "updatedAtMs": 1787043213089,
    "notifyPersisted": True,
    "seq": 7279,
    "ts": 1787043219471,
}


class TestModeSwitchInteraction:
    def test_maps_captured_engine_payload_to_flat_bcn_event(self):
        converter = DefaultStreamConverter()
        payload = deepcopy(_CAPTURED_MODE_SWITCH_PAYLOAD)

        event = converter.convert(
            _interaction_chunk("interaction.requested", payload),
            run_id="bcn-run",
        )

        assert event is not None
        assert event.event == "interaction"
        assert event.id == "1"
        raw_data = json.loads(event.data)
        assert isinstance(raw_data["ts"], int)
        assert raw_data["ts"] != payload["ts"]
        assert _data(event) == {
            "runId": "bcn-run",
            "seq": 1,
            "interactionId": "int:14136f21-01a0-42ed-8000-14136f2102ed",
            "kind": "mode_switch",
            "phase": "requested",
            "title": "Plan mode transition",
            "description": "Transition from plan to execute",
            "toolCallId": "top-level-tool-call",
            "fromMode": "plan",
            "targetMode": "execute",
            "options": [
                {
                    "label": "Continue to execution",
                    "decision": "proceed",
                    "targetMode": "execute",
                    "recommended": True,
                },
                {"label": "Stay in planning", "decision": "stay"},
            ],
        }

    def test_uses_subject_fields_for_old_engine_payload(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-old-mode",
                    "kind": "mode_switch",
                    "subject": {
                        "toolCallId": "subject-tool-call",
                        "fromMode": "plan",
                        "toMode": "execute",
                    },
                    "options": [{"label": "Continue", "value": "proceed"}],
                },
            ),
            run_id="bcn-run",
        )

        assert event is not None
        assert _data(event) == {
            "runId": "bcn-run",
            "seq": 1,
            "interactionId": "int-old-mode",
            "kind": "mode_switch",
            "phase": "requested",
            "toolCallId": "subject-tool-call",
            "fromMode": "plan",
            "targetMode": "execute",
            "options": [{"label": "Continue", "decision": "proceed"}],
        }

    def test_invalid_top_level_fields_fall_back_to_subject(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-mode",
                    "kind": "mode_switch",
                    "toolCallId": 42,
                    "fromMode": " ",
                    "toMode": {"invalid": True},
                    "subject": {
                        "toolCallId": "subject-tool-call",
                        "fromMode": "plan",
                        "toMode": "execute",
                    },
                    "options": [{"label": "Continue", "decision": "proceed"}],
                },
            ),
            run_id="bcn-run",
        )

        assert event is not None
        data = _data(event)
        assert data["toolCallId"] == "subject-tool-call"
        assert data["fromMode"] == "plan"
        assert data["targetMode"] == "execute"

    def test_mode_option_optional_fields_are_validated(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-mode",
                    "kind": "mode_switch",
                    "options": [
                        {
                            "label": "Stay",
                            "decision": "stay",
                            "recommended": False,
                        },
                        {
                            "label": "Continue",
                            "decision": "proceed",
                            "recommended": "yes",
                            "targetMode": {"invalid": True},
                        },
                    ],
                },
            ),
            run_id="bcn-run",
        )

        assert event is not None
        assert _data(event)["options"] == [
            {"label": "Stay", "decision": "stay", "recommended": False},
            {"label": "Continue", "decision": "proceed"},
        ]
        assert "field_path=payload.options[1].recommended" in caplog.text
        assert "field_path=payload.options[1].targetMode" in caplog.text
        assert caplog.text.count("error_type=invalid_type") == 2

    def test_invalid_options_drop_only_current_interaction_and_preserve_seq(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        cases = [
            {},
            {"options": None},
            {"options": {"not": "a list"}},
            {"options": []},
            {
                "options": [
                    {"label": "Missing decision"},
                    {"decision": "missing-label"},
                ]
            },
        ]

        for index, extra_payload in enumerate(cases):
            converter = DefaultStreamConverter()
            payload = {
                "interactionId": f"int-invalid-mode-{index}",
                "kind": "mode_switch",
                **extra_payload,
            }
            interaction = converter.convert(
                _interaction_chunk("interaction.requested", payload),
                run_id="bcn-run",
            )
            chat = converter.convert(
                StreamChunk(type="delta", content="after invalid mode switch"),
                run_id="bcn-run",
            )

            assert interaction is None
            assert chat is not None
            assert chat.id == "1"
            assert _data(chat)["seq"] == 1

        assert caplog.text.count("error_type=no_valid_options") == len(cases)
        assert "after invalid mode switch" not in caplog.text

    def test_duplicate_decisions_replace_in_place_and_warn(
        self,
        monkeypatch,
        caplog,
    ):
        _capture_interaction_logs(monkeypatch, caplog)
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-mode",
                    "kind": "mode_switch",
                    "options": [
                        {"label": "Old", "decision": "same"},
                        {"label": "Other", "decision": "other"},
                        {
                            "label": "New",
                            "decision": "same",
                            "recommended": True,
                        },
                    ],
                },
            ),
            run_id="bcn-run",
        )

        assert event is not None
        assert _data(event)["options"] == [
            {"label": "New", "decision": "same", "recommended": True},
            {"label": "Other", "decision": "other"},
        ]
        assert "field_path=payload.options[2].decision" in caplog.text
        assert "error_type=duplicate_option_decision" in caplog.text

    def test_invalid_decision_falls_back_to_value(self):
        converter = DefaultStreamConverter()
        event = converter.convert(
            _interaction_chunk(
                "interaction.requested",
                {
                    "interactionId": "int-mode",
                    "kind": "mode_switch",
                    "options": [
                        {"label": "Continue", "decision": "", "value": "proceed"},
                        {"label": "Stay", "decision": 42, "value": "stay"},
                    ],
                },
            ),
            run_id="bcn-run",
        )

        assert event is not None
        assert _data(event)["options"] == [
            {"label": "Continue", "decision": "proceed"},
            {"label": "Stay", "decision": "stay"},
        ]
