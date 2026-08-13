from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from engine.community.api.transport.ws_server import (
    _ENGINE_LOCAL_CHAT_SCOPE_KEY_HASH,
    EngineWebSocketServer,
    _materialized_path_redaction_targets,
    _parse_chat_file_materializations,
    _redact_materialized_paths,
    get_server,
    reset_server,
)
from engine.community.core.engine.context import AuthContext
from engine.community.core.resource_materialization.models import (
    ChatAttachmentMaterializationRequest,
    MaterializationResult,
)
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


def test_redact_materialized_paths_redacts_workspace_root_and_file():
    payload = {
        "cwd": "/bot/work",
        "message": "read /bot/work/a.txt",
        "nested": ["/bot/work/.teamclaw/session-files/a.txt"],
    }

    redacted = _redact_materialized_paths(
        payload,
        ("/bot/work/a.txt", "/bot/work"),
    )

    assert "/bot/work" not in str(redacted)
    assert redacted["message"] == "read [materialized-file]"
    assert redacted["cwd"] == "[materialized-file]"


def test_materialized_path_redaction_targets_include_workspace_root():
    path = "/bot/work/.teamclaw/session-files/a.txt"

    assert _materialized_path_redaction_targets((path,)) == (path, "/bot/work")


def test_parse_remote_file_uses_engine_scope_without_materialization_context():
    attachment = {
        "attachment_id": "att-1",
        "type": "file",
        "file_name": "design.pdf",
        "url": "https://files.example/object",
    }

    requests = _parse_chat_file_materializations(
        session_key="session-1",
        attachments=[attachment],
        materialization_context=None,
    )

    assert requests[0].attachment_id == "att-1"
    assert requests[0].scope_key_hash == _ENGINE_LOCAL_CHAT_SCOPE_KEY_HASH


def test_parse_remote_file_uses_valid_materialization_context_scope():
    attachment = {
        "attachment_id": "att-1",
        "type": "file",
        "file_name": "design.pdf",
        "url": "https://files.example/object",
    }

    requests = _parse_chat_file_materializations(
        session_key="session-1",
        attachments=[attachment],
        materialization_context={
            "layout_version": "session_file_v1",
            "scope_key_hash": "a" * 64,
        },
    )
    assert requests[0].attachment_id == "att-1"
    assert requests[0].scope_key_hash == "a" * 64


@pytest.mark.parametrize(
    ("materialization_context", "error"),
    [
        ("invalid", "must be an object"),
        ({}, "layout_version"),
        (
            {"layout_version": "session_file_v2", "scope_key_hash": "a" * 64},
            "layout_version",
        ),
        ({"layout_version": "session_file_v1"}, "scope_key_hash"),
        (
            {"layout_version": "session_file_v1", "scope_key_hash": "not-a-hash"},
            "invalid remote file attachment",
        ),
    ],
)
def test_parse_remote_file_rejects_invalid_materialization_context(
    materialization_context,
    error,
):
    attachment = {
        "attachment_id": "att-1",
        "type": "file",
        "file_name": "design.pdf",
        "url": "https://files.example/object",
    }

    with pytest.raises(ValueError, match=error):
        _parse_chat_file_materializations(
            session_key="session-1",
            attachments=[attachment],
            materialization_context=materialization_context,
        )


def test_get_server_attaches_late_materialization_dependency():
    reset_server()
    first = get_server()
    materialization_service = MagicMock()

    second = get_server(materialization_service)

    assert second is first
    assert second._resource_materialization_service is materialization_service
    reset_server()


@pytest.mark.asyncio
async def test_stream_materializes_remote_file_before_starting_adapter(fake_engine):
    reference_service = MagicMock()
    reference_service.rewrite.return_value = ResolvedResourceContext(
        prompt='<file-ref name="design.pdf" path="/bot/work/design.pdf"></file-ref>',
        resource_references=[
            {"insert_id": "chat_file_0_123456789012", "resource_id": "sr_123456789012"}
        ],
        materialized_files=[
            {
                "resource_id": "sr_123456789012",
                "canonical_bot_absolute_path": "/bot/work/design.pdf",
            }
        ],
    )
    materialization_service = MagicMock()
    materialization_service.materialize_chat_attachment = AsyncMock(
        return_value=MaterializationResult(
            resource_id="sr_123456789012",
            transfer_id="tmp_hash",
            task_id="chat_task",
            task_version=1,
            ready=True,
            canonical_bot_absolute_path="/bot/work/design.pdf",
        )
    )
    materialization_service.remove_chat_materialization = AsyncMock()
    server = EngineWebSocketServer(
        resource_reference_service=reference_service,
        resource_materialization_service=materialization_service,
    )
    websocket = SimpleNamespace(send_text=AsyncMock())
    captured = {}

    async def stream(request, auth):
        captured["request"] = request
        yield EventFrame(event="chat", payload={"state": "final", "runId": "run-1"})

    fake_engine.chat.stream = stream
    chat_request = ChatAttachmentMaterializationRequest(
        attachment_id="att-1",
        session_key="session-1",
        filename="design.pdf",
        temporary_url="https://files.example/object?token=secret",
        scope_key_hash="a" * 64,
    )
    await server._stream_chat_events(
        websocket,
        "conn-1",
        "session-1",
        "",
        None,
        attachments=[
            {
                "attachment_id": "att-1",
                "type": "file",
                "file_name": "design.pdf",
                "url": chat_request.temporary_url,
            }
        ],
        chat_attachment_requests=[chat_request],
    )

    request = captured["request"]
    assert request.query.startswith('<file-ref name="design.pdf"')
    assert request.extraParams["attachments"] == []
    assert chat_request.temporary_url not in str(request.extraParams)
    materialization_service.materialize_chat_attachment.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_rolls_back_batch_and_emits_safe_terminal_error(fake_engine):
    materialization_service = MagicMock()
    materialization_service.materialize_chat_attachment = AsyncMock(
        side_effect=[
            MaterializationResult(
                resource_id="sr_ready",
                transfer_id="tmp_one",
                task_id="chat_one",
                task_version=1,
                ready=True,
            ),
            MaterializationResult(
                resource_id="sr_failed",
                transfer_id="tmp_two",
                task_id="chat_two",
                task_version=1,
                ready=False,
                error_code="hash_mismatch",
            ),
        ]
    )
    materialization_service.remove_chat_materialization = AsyncMock()
    server = EngineWebSocketServer(
        resource_materialization_service=materialization_service
    )
    websocket = SimpleNamespace(send_text=AsyncMock())
    fake_engine.chat.stream = MagicMock()
    requests = [
        ChatAttachmentMaterializationRequest(
            attachment_id=f"att-{index}",
            session_key="session-1",
            filename=f"file-{index}.txt",
            temporary_url=f"https://files.example/object-{index}?token=secret",
            scope_key_hash="a" * 64,
        )
        for index in (1, 2)
    ]

    await server._stream_chat_events(
        websocket,
        "conn-1",
        "session-1",
        "",
        None,
        chat_attachment_requests=requests,
    )

    fake_engine.chat.stream.assert_not_called()
    materialization_service.remove_chat_materialization.assert_awaited_once_with(
        "sr_ready"
    )
    payload = websocket.send_text.await_args.args[0]
    assert "ATTACHMENT_MATERIALIZATION_HASH_MISMATCH" in payload
    assert "token=secret" not in payload


@pytest.mark.asyncio
async def test_stream_rolls_back_materialized_file_when_reference_rewrite_fails(
    fake_engine,
):
    reference_service = MagicMock()
    reference_service.rewrite.side_effect = ResourceReferenceError(
        "cross_session_resource"
    )
    materialization_service = MagicMock()
    materialization_service.materialize_chat_attachment = AsyncMock(
        return_value=MaterializationResult(
            resource_id="sr_ready",
            transfer_id="tmp_one",
            task_id="chat_one",
            task_version=1,
            ready=True,
        )
    )
    materialization_service.remove_chat_materialization = AsyncMock()
    server = EngineWebSocketServer(
        resource_reference_service=reference_service,
        resource_materialization_service=materialization_service,
    )
    websocket = SimpleNamespace(send_text=AsyncMock())
    fake_engine.chat.stream = MagicMock()
    request = ChatAttachmentMaterializationRequest(
        attachment_id="att-1",
        session_key="session-1",
        filename="file.txt",
        temporary_url="https://files.example/object?token=secret",
        scope_key_hash="a" * 64,
    )

    await server._stream_chat_events(
        websocket,
        "conn-1",
        "session-1",
        "",
        None,
        chat_attachment_requests=[request],
    )

    fake_engine.chat.stream.assert_not_called()
    materialization_service.remove_chat_materialization.assert_awaited_once_with(
        "sr_ready"
    )
    payload = websocket.send_text.await_args.args[0]
    assert "cross_session_resource" in payload
    assert "token=secret" not in payload


@pytest.mark.asyncio
async def test_stream_rewrites_resources_before_adapter(fake_engine, monkeypatch):
    monkeypatch.delenv("OPENCLAW_WORKSPACE_DIR", raising=False)
    reference_service = MagicMock()
    reference_service.rewrite.return_value = ResolvedResourceContext(
        prompt=(
            'read <file-ref name="a.txt" '
            'path="/bot/work/.teamclaw/session-files/a.txt"></file-ref>'
        ),
        resource_references=[{"resource_id": "r1", "insert_id": "i1"}],
        materialized_files=[
            {
                "resource_id": "r1",
                "canonical_bot_absolute_path": "/bot/work/.teamclaw/session-files/a.txt",
            }
        ],
    )
    server = EngineWebSocketServer(resource_reference_service=reference_service)
    server._conn_auth["conn-1"] = AuthContext(token="token")
    websocket = SimpleNamespace(send_text=AsyncMock())
    captured = {}

    async def stream(request, auth):
        captured["request"] = request
        captured["auth"] = auth
        yield EventFrame(
            event="chat",
            payload={
                "state": "final",
                "message": "read /bot/work/.teamclaw/session-files/a.txt",
                "nested": {"pathEcho": "/bot/work/.teamclaw/session-files/a.txt"},
                "cwd": "/bot/work",
            },
        )

    fake_engine.chat.stream = stream
    refs = [{"resource_id": "r1", "insert_id": "i1"}]
    threaded_functions = []

    async def run_in_worker_thread(function, /, *args, **kwargs):
        threaded_functions.append(function)
        return function(*args, **kwargs)

    with patch(
        "engine.community.api.transport.ws_server.asyncio.to_thread",
        new=run_in_worker_thread,
    ):
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
    assert (
        extra["materializedFiles"][0]["canonical_bot_absolute_path"]
        == "/bot/work/.teamclaw/session-files/a.txt"
    )
    reference_service.rewrite.assert_called_once()
    assert threaded_functions == [reference_service.rewrite]
    outbound = websocket.send_text.await_args.args[0]
    assert "/bot/work" not in outbound
    assert "[materialized-file]" in outbound


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
