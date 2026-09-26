"""Tests for SSE data models (StreamChunk, SseEvent)."""

from secbaas.community.api.sse import SseEvent, StreamChunk


class TestStreamChunk:
    def test_defaults(self):
        chunk = StreamChunk(type="delta")
        assert chunk.content == ""
        assert chunk.usage is None
        assert chunk.metadata is None
        assert chunk.engine_type is None

    def test_full_construction(self):
        chunk = StreamChunk(
            type="final",
            content="done",
            usage={"input": 10},
            metadata={"stopReason": "end_turn"},
            engine_type="claude_code",
        )
        assert chunk.type == "final"
        assert chunk.content == "done"
        assert chunk.usage == {"input": 10}
        assert chunk.metadata == {"stopReason": "end_turn"}
        assert chunk.engine_type == "claude_code"

    def test_frozen(self):
        chunk = StreamChunk(type="delta")
        try:
            chunk.content = "modified"
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass


class TestSseEventToSse:
    def test_basic_event(self):
        event = SseEvent(event="chat", data='{"seq":1}')
        result = event.to_sse()
        assert "event: chat" in result
        assert 'data: {"seq":1}' in result
        assert result.endswith("\n\n")

    def test_event_with_id(self):
        event = SseEvent(event="chat", data="{}", id="42")
        result = event.to_sse()
        assert "id: 42" in result
        assert "event: chat" in result

    def test_event_with_retry(self):
        event = SseEvent(event="chat", data="{}", retry=5000)
        result = event.to_sse()
        assert "retry: 5000" in result

    def test_event_with_id_and_retry(self):
        event = SseEvent(event="chat", data="{}", id="7", retry=3000)
        result = event.to_sse()
        lines = result.strip().split("\n")
        assert lines[0] == "id: 7"
        assert lines[1] == "retry: 3000"
        assert lines[2] == "event: chat"
        assert lines[3] == "data: {}"

    def test_comment_frame(self):
        event = SseEvent(event=": heartbeat", data="")
        result = event.to_sse()
        assert result == ": heartbeat\n\n"

    def test_comment_frame_ignores_id_and_retry(self):
        event = SseEvent(event=": heartbeat", data="", id="99", retry=1000)
        result = event.to_sse()
        assert result == ": heartbeat\n\n"
        assert "id:" not in result
        assert "retry:" not in result
