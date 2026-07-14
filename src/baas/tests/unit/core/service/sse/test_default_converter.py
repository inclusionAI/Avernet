"""Coverage tests for DefaultStreamConverter — targets untested branches.

Covers: heartbeat SSE comment frame, error with errorKind, aborted,
agent with non-dict data, command_output non-claude, lifecycle invalid
phase, claude tool invalid type, claude tool start with args,
_claude_tool_result with dict, openclaw tool invalid phase,
delta with empty content, final with empty content.
"""

import json

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.sse import DefaultStreamConverter


def _data(event) -> dict:
    d = json.loads(event.data)
    d.pop("ts", None)
    return d


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
