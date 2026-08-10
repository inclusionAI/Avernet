import json

import pytest

from secbaas.community.adapters.web.routers.bcn_downlink.bcn_model import (
    ChatSendRequest,
)
from secbaas.community.adapters.web.routers.bcn_downlink.bcn_router import (
    _dispatch_chat_send_stream,
    validate_bcn_token,
)
from secbaas.community.api.bcn import BcnUnauthorizedError
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


class _MissingSecretStore:
    def get_secret(self, _name):
        raise RuntimeError("not configured")


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


def test_bcn_downlink_env_credential_overrides_empty_local_secret_store(monkeypatch):
    monkeypatch.setenv("BCS_BAAS_DOWNLINK_TOKEN", "bridge-token")

    assert validate_bcn_token("Bearer bridge-token", _MissingSecretStore()) == "bridge-token"
    with pytest.raises(BcnUnauthorizedError):
        validate_bcn_token("Bearer wrong-token", _MissingSecretStore())
