import json
from unittest.mock import MagicMock

import pytest

from secbaas.community.adapters.web.routers.bcn_downlink.bcn_model import (
    ChatSendRequest,
)
from secbaas.community.adapters.web.routers.bcn_downlink.bcn_router import (
    _dispatch_chat_send,
    _dispatch_chat_send_stream,
)
from secbaas.community.api.bcn import (
    Attachment as DomainAttachment,
)
from secbaas.community.api.bcn import (
    BcnInvalidRequestError,
)
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.sse import DefaultStreamConverter


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
        assert name == "default"
        return DefaultStreamConverter()


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
async def test_stream_dispatch_skips_dropped_converter_events():
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


@pytest.mark.asyncio
async def test_stream_dispatch_error_yields_error_sse():
    """When chunk_iter raises, on_error produces an error SSE event."""

    class _ErrorStreamService:
        async def handle_chat_send_stream(self, _input):
            async def _chunks():
                yield StreamChunk(type="delta", content="hi")
                raise RuntimeError("chunk boom")

            return _chunks()

    response = await _dispatch_chat_send_stream(
        _chat_send_request(),
        _ErrorStreamService(),
        _ConverterFactory(),
    )

    items = []
    async for item in response.body_iterator:
        items.append(item)

    # delta + error
    assert len(items) == 2
    assert items[0].startswith("id: 1\nevent: chat\n")
    assert "error" in items[1]
    assert "INTERNAL_ERROR" in items[1]


# ── attachment passthrough tests ──


@pytest.mark.asyncio
async def test_dispatch_chat_send_passes_attachments():
    """_dispatch_chat_send constructs ChatSendInput with attachments list."""
    # Record the ChatSendInput that handle_chat_send receives
    captured = {}

    class _CapturingService:
        async def handle_chat_send(self, input_):
            captured["input"] = input_
            from secbaas.community.api.bcn import ChatSendResult

            return ChatSendResult(ok=True)

    req = ChatSendRequest.model_validate(
        {
            "id": "run-1",
            "session_id": "session-1",
            "bcn_group_id": "group-1",
            "to_bot": {"provider_id": "baas", "provider_bot_ref": "bot-1"},
            "from": {"kind": "human", "id": "user-1"},
            "message": {"role": "user", "content": "hi"},
            "attachments": [
                {
                    "attachment_id": "att_1",
                    "type": "image",
                    "file_name": "photo.png",
                    "url": "https://cdn.example.com/att_1",
                },
            ],
        }
    )

    await _dispatch_chat_send(req, _CapturingService())

    input_ = captured["input"]
    assert input_.attachments is not None, "ChatSendInput.attachments must not be None"
    assert len(input_.attachments) == 1
    assert input_.attachments[0].attachment_id == "att_1"
    assert isinstance(input_.attachments[0], DomainAttachment), (
        "attachments must be domain dataclass instances, not Pydantic models"
    )


@pytest.mark.asyncio
async def test_dispatch_chat_send_passes_attachments_none():
    """_dispatch_chat_send constructs ChatSendInput with attachments=None when absent."""
    captured = {}

    class _CapturingService:
        async def handle_chat_send(self, input_):
            captured["input"] = input_
            from secbaas.community.api.bcn import ChatSendResult

            return ChatSendResult(ok=True)

    req = ChatSendRequest.model_validate(
        {
            "id": "run-1",
            "session_id": "session-1",
            "bcn_group_id": "group-1",
            "to_bot": {"provider_id": "baas", "provider_bot_ref": "bot-1"},
            "from": {"kind": "human", "id": "user-1"},
            "message": {"role": "user", "content": "hi"},
            # no "attachments" key
        }
    )

    await _dispatch_chat_send(req, _CapturingService())

    input_ = captured["input"]
    assert input_.attachments is None, (
        "ChatSendInput.attachments must be None when absent"
    )


@pytest.mark.asyncio
async def test_dispatch_chat_send_value_error_wraps_to_bcn_invalid_request():
    """When handle_chat_send raises ValueError, it's wrapped in BcnInvalidRequestError."""

    class _ValueErrorService:
        async def handle_chat_send(self, input_):
            raise ValueError("invalid timeout value")

    with pytest.raises(BcnInvalidRequestError) as exc_info:
        await _dispatch_chat_send(_chat_send_request(), _ValueErrorService())
    assert "invalid timeout value" in str(exc_info.value)
