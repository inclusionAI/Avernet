from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.community.api.transport.ws_server import EngineWebSocketServer
from engine.community.core.engine.context import AuthContext
from engine.community.core.resource_references.models import ResolvedResourceContext
from engine.community.core.resource_references.service import ResourceReferenceError
from engine.community.kernel.frames import EventFrame
from engine.community.manager import EngineManager


@pytest.fixture
def fake_engine():
    engine = MagicMock()
    engine.chat = MagicMock()
    EngineManager.reset_instance()
    manager = EngineManager("fake")
    manager._active_engine = engine
    EngineManager._instance = manager
    yield engine
    EngineManager.reset_instance()


@pytest.mark.asyncio
async def test_stream_rewrites_resources_before_adapter(fake_engine):
    reference_service = MagicMock()
    reference_service.rewrite.return_value = ResolvedResourceContext(
        prompt='read <file-ref name="a.txt" path="/bot/work/a.txt"></file-ref>',
        resource_references=[{"resource_id": "r1", "insert_id": "i1"}],
        materialized_files=[
            {"resource_id": "r1", "canonical_bot_absolute_path": "/bot/work/a.txt"}
        ],
    )
    server = EngineWebSocketServer(resource_reference_service=reference_service)
    server._conn_auth["conn-1"] = AuthContext(token="token")
    websocket = SimpleNamespace(send_text=AsyncMock())
    captured = {}

    async def stream(request, auth):
        captured["request"] = request
        captured["auth"] = auth
        yield EventFrame(event="chat", payload={"state": "final"})

    fake_engine.chat.stream = stream
    refs = [{"resource_id": "r1", "insert_id": "i1"}]

    await server._stream_chat_events(
        websocket,
        "conn-1",
        "session-1",
        '<file-ref insert_id="i1"></file-ref>',
        None,
        resource_references=refs,
        prompt_file_refs=refs,
    )

    assert captured["request"].query.startswith("read <file-ref")
    extra = captured["request"].extraParams
    assert extra["resourceReferences"] == refs
    assert extra["promptFileRefs"] == refs
    assert extra["materializedFiles"][0]["canonical_bot_absolute_path"] == "/bot/work/a.txt"
    reference_service.rewrite.assert_called_once()


@pytest.mark.asyncio
async def test_invalid_reference_emits_error_without_starting_adapter(fake_engine):
    reference_service = MagicMock()
    reference_service.rewrite.side_effect = ResourceReferenceError(
        "cross_session_resource"
    )
    server = EngineWebSocketServer(resource_reference_service=reference_service)
    websocket = SimpleNamespace(send_text=AsyncMock())
    fake_engine.chat.stream = MagicMock()

    await server._stream_chat_events(
        websocket,
        "conn-1",
        "session-1",
        '<file-ref insert_id="i1"></file-ref>',
        None,
        resource_references=[{"resource_id": "r1", "insert_id": "i1"}],
    )

    fake_engine.chat.stream.assert_not_called()
    payload = websocket.send_text.await_args.args[0]
    assert "cross_session_resource" in payload
