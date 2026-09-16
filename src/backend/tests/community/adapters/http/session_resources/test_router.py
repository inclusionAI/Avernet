from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.session_resources.router import (
    create_upload_intents,
    list_pending_session_resources,
    materialize_status,
    materialized_callback,
    stream_content,
    upload_complete,
)
from agentclaw.community.adapters.http.session_resources.schemas import (
    MaterializedCallbackRequest,
    UploadCompleteRequest,
    UploadIntentRequest,
)
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
    SessionUploadIntent,
    UploadGrant,
)
from agentclaw.community.core.tc_file_upload_integrations.coordinator import (
    TcResourceReadyCoordinator,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterStreamResponse,
)


def _record(**overrides):
    values = {
        "resource_id": "sr_001",
        "owner_id": "owner-1",
        "bot_id": "bot-1",
        "scope_type": "personal_bot_chat",
        "scope_key_hash": "scope-hash",
        "session_key_hash": "session-hash",
        "engine_type": "claude_code",
        "tenant": "tenant",
        "bot_uuid": "uuid",
        "display_name": "a.txt",
        "filename": "a.txt",
        "device_path": "workspace/.teamclaw/session-files/scope/session/sr_001/a.txt",
        "workspace_relative_path": ".teamclaw/session-files/scope/session/sr_001/a.txt",
        "transfer_id": "transfer-1",
        "status": SessionResourceStatus.DEVICE_SYNCING,
        "task_id": "task-1",
        "task_version": 1,
    }
    values.update(overrides)
    return SessionResourceRecord(**values)


class _RecordingObserver:
    def __init__(self) -> None:
        self.records: list[SessionResourceRecord] = []

    def notify_in_background(self, resource: SessionResourceRecord) -> None:
        self.records.append(resource)


class _FailingPublisher:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event) -> None:
        self.events.append(event)
        raise RuntimeError("downstream unavailable")


class _Service:
    def __init__(self) -> None:
        self.callback_kwargs = None
        self.complete_result = _record()
        self.status_result = _record()

    def create_upload_intent(self, **kwargs):
        self.intent_kwargs = kwargs
        return SessionUploadIntent(
            resource=_record(),
            grant=UploadGrant(transfer_id="transfer-1", upload_type="SINGLE"),
        )

    def complete_upload(self, **kwargs):
        self.complete_kwargs = kwargs
        return self.complete_result

    def get_status(self, **kwargs):
        self.status_kwargs = kwargs
        return self.status_result

    def list_pending(self, **kwargs):
        self.pending_kwargs = kwargs
        return [_record()]

    def materialized_callback(self, **kwargs):
        self.callback_kwargs = kwargs
        return replace(_record(), status=SessionResourceStatus.READY)

    async def open_content(self, **kwargs):
        self.content_kwargs = kwargs

        async def chunks() -> AsyncIterator[bytes]:
            yield b"hello"

        async def close() -> None:
            self.content_closed = True

        self.content_closed = False
        return (
            replace(_record(), status=SessionResourceStatus.READY),
            DeviceAdapterStreamResponse(
                status_code=200,
                headers={
                    "content-type": "text/plain",
                    "content-length": "5",
                    "content-disposition": 'inline; filename="a.txt"',
                    "x-internal-token": "hidden",
                },
                body=chunks(),
                close=close,
            ),
        )


@pytest.mark.asyncio
async def test_upload_intent_keeps_the_primary_service_contract_only():
    service = _Service()
    body = UploadIntentRequest(
        bot_id="bot-1",
        session_key="session-raw",
        scope_type="friend_bot_chat",
        engine_type="openclaw",
        files=[
            {
                "filename": "report.pdf",
                "size_bytes": 12,
                "content_hash": "hash-1",
            }
        ],
    )

    result = await create_upload_intents(
        body=body,
        user=AuthenticatedUser("id", "owner-1", "owner-1"),
        service=service,
    )

    assert service.intent_kwargs == {
        "owner_id": "owner-1",
        "bot_id": "bot-1",
        "session_key": "session-raw",
        "scope_type": "friend_bot_chat",
        "engine_type": "openclaw",
        "filename": "report.pdf",
        "target_entity_id": None,
        "binding_id": None,
        "size_bytes": 12,
        "content_hash": "hash-1",
    }
    assert result["files"][0]["resource_id"] == "sr_001"
    assert "mime_type" not in result["files"][0]


def test_upload_intent_ignores_sidecar_authority_and_legacy_extra_fields():
    body = UploadIntentRequest.model_validate(
        {
            "bot_id": "bot-1",
            "session_key": "session-raw",
            "scope_type": "friend_bot_chat",
            "engine_type": "openclaw",
            "files": [{"filename": "report.txt"}],
            "conversation_id": "untrusted",
            "group_id": "untrusted",
            "members": ["untrusted"],
            "mime_type": "text/plain",
            "client_version": "legacy",
        }
    )

    assert body.model_dump() == {
        "bot_id": "bot-1",
        "session_key": "session-raw",
        "scope_type": "friend_bot_chat",
        "engine_type": "openclaw",
        "target_entity_id": None,
        "binding_id": None,
        "files": [
            {
                "filename": "report.txt",
                "size_bytes": None,
                "content_hash": None,
            }
        ],
    }


def test_upload_intent_request_accepts_positive_binding_id_only():
    body = UploadIntentRequest(
        bot_id="bot-1",
        session_key="session-raw",
        scope_type="friend_bot_chat",
        engine_type="openclaw",
        binding_id=91,
        files=[{"filename": "report.txt"}],
    )

    assert body.binding_id == 91

    with pytest.raises(ValidationError, match="binding_id"):
        UploadIntentRequest(
            bot_id="bot-1",
            session_key="session-raw",
            scope_type="friend_bot_chat",
            engine_type="openclaw",
            binding_id=True,
            files=[{"filename": "report.txt"}],
        )


@pytest.mark.asyncio
async def test_upload_complete_invokes_the_shared_observer():
    service = _Service()
    service.complete_result = replace(_record(), status=SessionResourceStatus.READY)
    observer = _RecordingObserver()

    result = await upload_complete(
        UploadCompleteRequest(
            bot_id="bot-1",
            session_key="session-raw",
            resource_id="sr_001",
            transfer_id="transfer-1",
        ),
        user=AuthenticatedUser("id", "owner-1", "owner-1"),
        service=service,
        observer=observer,
    )

    assert result["status"] == "ready"
    assert observer.records == [service.complete_result]


@pytest.mark.asyncio
async def test_polling_invokes_the_shared_observer_even_before_ready():
    service = _Service()
    observer = _RecordingObserver()

    result = await materialize_status(
        "sr_001",
        "bot-1",
        "session-raw",
        user=AuthenticatedUser("id", "owner-1", "owner-1"),
        service=service,
        observer=observer,
    )

    assert result["status"] == "device_syncing"
    assert observer.records == [service.status_result]
    assert service.status_kwargs == {
        "owner_id": "owner-1",
        "bot_id": "bot-1",
        "session_key": "session-raw",
        "resource_id": "sr_001",
    }


@pytest.mark.asyncio
async def test_ready_status_response_survives_async_publisher_failure():
    service = _Service()
    service.status_result = replace(_record(), status=SessionResourceStatus.READY)
    publisher = _FailingPublisher()
    coordinator = TcResourceReadyCoordinator(publisher=publisher)

    result = await materialize_status(
        "sr_001",
        "bot-1",
        "session-raw",
        user=AuthenticatedUser("id", "owner-1", "owner-1"),
        service=service,
        observer=coordinator,
    )
    while coordinator._tasks:
        await asyncio.gather(*tuple(coordinator._tasks), return_exceptions=True)

    assert result["status"] == "ready"
    assert len(publisher.events) == 1


@pytest.mark.asyncio
async def test_pending_lists_only_control_plane_records():
    service = _Service()

    result = await list_pending_session_resources(
        "bot-1",
        "session-raw",
        user=AuthenticatedUser("id", "owner-1", "owner-1"),
        service=service,
    )

    assert result["files"][0]["resource_id"] == "sr_001"
    assert service.pending_kwargs == {
        "owner_id": "owner-1",
        "bot_id": "bot-1",
        "session_key": "session-raw",
    }


@pytest.mark.asyncio
async def test_callback_uses_task_capability_and_observes_the_applied_record():
    service = _Service()
    observer = _RecordingObserver()
    body = MaterializedCallbackRequest(
        transfer_id="transfer-1",
        task_id="task-1",
        task_version=1,
        ready=True,
        canonical_bot_absolute_path="/home/admin/private/workspace/a.txt",
        relative_path=".teamclaw/session-files/scope/session/sr_001/a.txt",
        size_bytes=1,
        content_hash="hash",
    )

    result = await materialized_callback(
        "sr_001",
        body,
        x_materialization_task_id="task-1",
        service=service,
        observer=observer,
    )

    assert result == {"applied": True, "status": "ready"}
    stored = service.callback_kwargs["materialized_ref"]
    assert "canonical_bot_absolute_path" not in stored
    assert stored["path_hash"]
    assert [record.status for record in observer.records] == [
        SessionResourceStatus.READY
    ]


@pytest.mark.asyncio
async def test_callback_rejects_wrong_task_capability_before_observation():
    observer = _RecordingObserver()
    with pytest.raises(HTTPException) as exc:
        await materialized_callback(
            "sr_001",
            MaterializedCallbackRequest(
                transfer_id="transfer-1",
                task_id="task-1",
                task_version=1,
                ready=False,
                error_code="pull_failed",
            ),
            x_materialization_task_id="wrong",
            service=_Service(),
            observer=observer,
        )

    assert exc.value.status_code == 401
    assert observer.records == []


@pytest.mark.asyncio
async def test_content_proxies_only_safe_headers_and_closes_upstream():
    service = _Service()

    response = await stream_content(
        "sr_001",
        "bot-1",
        "session-raw",
        disposition="inline",
        user=AuthenticatedUser("id", "owner-1", "owner-1"),
        service=service,
    )

    assert service.content_kwargs["disposition"] == "inline"
    assert response.headers["content-type"] == "text/plain"
    assert response.headers["content-length"] == "5"
    assert "x-internal-token" not in response.headers
    assert [chunk async for chunk in response.body_iterator] == [b"hello"]
    assert service.content_closed is True
