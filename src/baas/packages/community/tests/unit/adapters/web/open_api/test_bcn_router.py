import json

import pytest

from secbaas.adapters.web.routers.bcn_downlink.bcn_model import ChatSendRequest
from secbaas.adapters.web.routers.bcn_downlink.bcn_router import (
    _dispatch_chat_send_stream,
)
from secbaas.api.sse import StreamChunk
from secbaas.core.service.sse import BcnStreamConverter


class _StreamService:
    async def handle_chat_send_stream(self, _input):
        async def _chunks():
            # "assistant" stream is noise — converter drops it (returns None)
            yield StreamChunk(
                type="agent",
                metadata={
                    "engine_frame": {
                        "stream": "assistant",
                        "data": {"text": "mirror"},
                    }
                },
            )
            # delta chunk is kept — converter produces a chat event
            yield StreamChunk(type="delta", content="hello")

        return _chunks()


class _ConverterFactory:
    def create(self, name):
        assert name == BcnStreamConverter.name()
        return BcnStreamConverter()


def _chat_send_request() -> ChatSendRequest:
    return ChatSendRequest.model_validate(
        {
            "id": "run-1",
            "session_id": "session-1",
            "bcn_group_id": "group-1",
            "to_bot": {
                "provider_id": "baas",
                "provider_bot_ref": "bot-1",
            },
            "from": {"kind": "human", "id": "user-1"},
            "message": {"role": "user", "content": "hi"},
            "extensions": {"response_mode": "stream"},
        }
    )


@pytest.mark.asyncio
async def test_stream_dispatch_skips_dropped_converter_events(caplog):
    caplog.set_level("INFO")
    response = await _dispatch_chat_send_stream(
        _chat_send_request(),
        _StreamService(),
        _ConverterFactory(),
    )

    chunks = []
    async for item in response.body_iterator:
        chunks.append(item)

    assert len(chunks) == 1
    assert chunks[0].startswith("id: 1\nevent: chat\n")
    data_line = next(
        line for line in chunks[0].splitlines() if line.startswith("data: ")
    )
    data = json.loads(data_line.removeprefix("data: "))
    data.pop("ts", None)  # ts is stamped at conversion time, not asserted
    assert data == {
        "runId": "run-1",
        "seq": 1,
        "state": "delta",
        "deltaText": "hello",
    }
    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    assert any(
        "[chat.send.stream] response body started: "
        "run_id=run-1 media_type=text/event-stream" in message
        for message in caplog.messages
    )
    assert any(
        "[chat.send.stream] first SSE frame: "
        "run_id=run-1 event=chat chunk_type=delta" in message
        for message in caplog.messages
    )
    assert any(
        "[chat.send.stream] response body closed: "
        "run_id=run-1 reason=eof frame_count=1 last_chunk_type=delta" in message
        for message in caplog.messages
    )


@pytest.mark.asyncio
async def test_stream_dispatch_logs_generator_exit_when_consumer_closes(caplog):
    caplog.set_level("INFO")
    response = await _dispatch_chat_send_stream(
        _chat_send_request(),
        _StreamService(),
        _ConverterFactory(),
    )

    first = await anext(response.body_iterator)
    assert first.startswith("id: 1\nevent: chat\n")
    await response.body_iterator.aclose()

    assert any(
        "[chat.send.stream] response body closed: "
        "run_id=run-1 reason=generator_exit frame_count=1 last_chunk_type=delta"
        in message
        for message in caplog.messages
    )
