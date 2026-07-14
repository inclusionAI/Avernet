import json

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.sse import BcnStreamConverter


def _data(event) -> dict:
    d = json.loads(event.data)
    d.pop("ts", None)  # ts is stamped at conversion time, not asserted
    return d


def test_chat_delta_normalizes_claude_delta_to_bcn_delta_text():
    converter = BcnStreamConverter()
    event = converter.convert(
        StreamChunk(type="delta", content="你好", engine_type="claude_code"),
        run_id="run-1",
    )

    assert event.event == "chat"
    assert event.id == "1"
    assert _data(event) == {
        "runId": "run-1",
        "seq": 1,
        "state": "delta",
        "deltaText": "你好",
    }


def test_chat_final_keeps_message_and_stop_reason():
    converter = BcnStreamConverter()
    event = converter.convert(
        StreamChunk(
            type="final",
            content="done",
            metadata={"stopReason": "end_turn", "usage": {"input": 10}},
        ),
        run_id="run-1",
    )

    assert event.event == "chat"
    d = _data(event)
    d["message"].pop("timestamp", None)
    assert d == {
        "runId": "run-1",
        "seq": 1,
        "state": "final",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
        },
        "stopReason": "end_turn",
        "usage": {"input": 10},
    }


def test_openclaw_tool_preserves_flat_bcn_fields():
    converter = BcnStreamConverter()
    event = converter.convert(
        StreamChunk(
            type="agent",
            engine_type="openclaw",
            metadata={
                "engine_frame": {
                    "stream": "tool",
                    "data": {
                        "phase": "result",
                        "name": "read",
                        "toolCallId": "tool-1",
                        "result": {"content": [{"type": "text", "text": "done"}]},
                        "isError": False,
                        "durationMs": 12,
                    },
                }
            },
        ),
        run_id="run-1",
    )

    assert event.event == "agent"
    assert _data(event) == {
        "runId": "run-1",
        "seq": 1,
        "stream": "tool",
        "phase": "result",
        "name": "read",
        "toolCallId": "tool-1",
        "result": {"content": [{"type": "text", "text": "done"}]},
        "isError": False,
        "durationMs": 12,
    }


def test_claude_tool_result_wraps_string_output_as_text_content():
    converter = BcnStreamConverter()
    event = converter.convert(
        StreamChunk(
            type="agent",
            engine_type="claude_code",
            metadata={
                "engine_frame": {
                    "stream": "tool",
                    "data": {
                        "type": "result",
                        "toolName": "Bash",
                        "toolCallId": "tool-2",
                        "output": "ok",
                        "isError": True,
                    },
                }
            },
        ),
        run_id="run-1",
    )

    assert event.event == "agent"
    assert _data(event) == {
        "runId": "run-1",
        "seq": 1,
        "stream": "tool",
        "phase": "result",
        "name": "Bash",
        "toolCallId": "tool-2",
        "result": {"content": [{"type": "text", "text": "ok"}]},
        "isError": True,
    }


def test_thinking_and_lifecycle_are_forwarded_with_bcn_envelope():
    converter = BcnStreamConverter()
    thinking = converter.convert(
        StreamChunk(
            type="agent",
            metadata={
                "engine_frame": {
                    "stream": "thinking",
                    "data": {"delta": "h", "text": "hi"},
                }
            },
        ),
        run_id="run-1",
    )
    lifecycle = converter.convert(
        StreamChunk(
            type="agent",
            metadata={
                "engine_frame": {
                    "stream": "lifecycle",
                    "data": {"phase": "start", "model": "glm"},
                }
            },
        ),
        run_id="run-1",
    )

    assert _data(thinking) == {
        "runId": "run-1",
        "seq": 1,
        "stream": "thinking",
        "delta": "h",
        "text": "hi",
    }
    assert _data(lifecycle) == {
        "runId": "run-1",
        "seq": 2,
        "stream": "lifecycle",
        "phase": "start",
        "model": "glm",
    }


def test_command_output_end_converts_to_tool_result():
    converter = BcnStreamConverter()
    event = converter.convert(
        StreamChunk(
            type="agent",
            engine_type="claude_code",
            metadata={
                "engine_frame": {
                    "stream": "command_output",
                    "data": {
                        "phase": "end",
                        "toolCallId": "tool-3",
                        "output": "hello world",
                        "exitCode": 0,
                        "durationMs": 42,
                        "cwd": "/tmp",
                    },
                }
            },
        ),
        run_id="run-1",
    )

    assert event.event == "agent"
    assert _data(event) == {
        "runId": "run-1",
        "seq": 1,
        "stream": "tool",
        "phase": "result",
        "toolCallId": "tool-3",
        "result": {"content": [{"type": "text", "text": "hello world"}]},
        "isError": False,
        "exitCode": 0,
        "durationMs": 42,
        "cwd": "/tmp",
    }


def test_command_output_non_end_phase_is_dropped():
    converter = BcnStreamConverter()
    event = converter.convert(
        StreamChunk(
            type="agent",
            engine_type="claude_code",
            metadata={
                "engine_frame": {
                    "stream": "command_output",
                    "data": {"phase": "start", "toolCallId": "tool-3"},
                }
            },
        ),
        run_id="run-1",
    )
    assert event is None


def test_command_output_nonzero_exit_sets_is_error():
    converter = BcnStreamConverter()
    event = converter.convert(
        StreamChunk(
            type="agent",
            engine_type="claude_code",
            metadata={
                "engine_frame": {
                    "stream": "command_output",
                    "data": {
                        "phase": "end",
                        "output": "permission denied",
                        "exitCode": 1,
                    },
                }
            },
        ),
        run_id="run-1",
    )

    assert _data(event)["isError"] is True


def test_noise_engine_events_are_dropped_without_advancing_seq():
    converter = BcnStreamConverter()

    dropped = converter.convert(
        StreamChunk(
            type="agent",
            metadata={
                "engine_frame": {
                    "stream": "assistant",
                    "data": {"text": "mirror"},
                }
            },
        ),
        run_id="run-1",
    )
    kept = converter.convert(
        StreamChunk(type="delta", content="next"),
        run_id="run-1",
    )

    assert dropped is None
    assert kept.id == "1"
    assert _data(kept)["seq"] == 1


def test_plain_stream_chunk_falls_back_to_bcn_chat_event():
    converter = BcnStreamConverter()

    delta = converter.convert(StreamChunk(type="delta", content="hi"), run_id="run-1")
    final = converter.convert(StreamChunk(type="final", content="done"), run_id="run-1")

    assert delta.event == "chat"
    assert _data(delta) == {
        "runId": "run-1",
        "seq": 1,
        "state": "delta",
        "deltaText": "hi",
    }
    fd = _data(final)
    fd["message"].pop("timestamp", None)
    assert fd == {
        "runId": "run-1",
        "seq": 2,
        "state": "final",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
        },
    }
