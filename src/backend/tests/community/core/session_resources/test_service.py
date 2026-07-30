from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

import httpx
import pytest

from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.session_resources.baas_client import (
    SessionResourceBaasClient,
)
from agentclaw.community.core.session_resources.service import SessionResourceService
from agentclaw.community.core.session_resources.types import SessionResourceStatus
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterStreamResponse,
)


class _Repo:
    def __init__(self) -> None:
        self.value = None

    def create(self, value):
        self.value = value
        return value

    def get_owned(self, resource_id, owner_id, bot_id, session_key_hash):
        value = self.value
        if value is None:
            return None
        if (
            value.resource_id,
            value.owner_id,
            value.bot_id,
            value.session_key_hash,
        ) != (resource_id, owner_id, bot_id, session_key_hash):
            return None
        return value

    def get_by_resource_id(self, resource_id):
        return self.value if self.value and self.value.resource_id == resource_id else None

    def list_owned(self, owner_id, bot_id, session_key_hash):
        value = self.value
        if value and self.get_owned(value.resource_id, owner_id, bot_id, session_key_hash):
            return [value]
        return []

    def cas_start_materialization(self, **kwargs):
        allowed = {
            SessionResourceStatus.UPLOAD_URL_ISSUED,
            SessionResourceStatus.DEVICE_SYNC_FAILED,
        }
        if kwargs.get("allow_ready"):
            allowed.add(SessionResourceStatus.READY)
        if self.value.status not in allowed:
            return None
        self.value = replace(
            self.value,
            status=SessionResourceStatus.DEVICE_SYNCING,
            task_id=kwargs["task_id"],
            task_version=self.value.task_version + 1,
            error_code=None,
            materialized_ref=None,
        )
        return self.value

    def cas_finish_materialization(self, **kwargs):
        if (
            self.value.status is not SessionResourceStatus.DEVICE_SYNCING
            or self.value.task_id != kwargs["task_id"]
            or self.value.task_version != kwargs["task_version"]
        ):
            return None
        self.value = replace(
            self.value,
            status=(
                SessionResourceStatus.READY
                if kwargs["ready"]
                else SessionResourceStatus.DEVICE_SYNC_FAILED
            ),
            materialized_ref=kwargs["materialized_ref"],
            error_code=kwargs["error_code"],
        )
        return self.value

    def soft_delete(self, resource_id, owner_id, bot_id, session_key_hash):
        self.value = replace(self.value, status=SessionResourceStatus.DELETED)
        return self.value


class _HttpClient:
    def __init__(self, complete_status: str = "DONE") -> None:
        self.calls = []
        self.complete_status = complete_status

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        request = httpx.Request("POST", f"https://baas.example{path}")
        if path.endswith("upload-url"):
            data = {
                "upload_url": "https://oss.example/object?Signature=secret",
                "transfer_id": "transfer-1",
                "expires_at": "2026-07-13T00:00:00Z",
                "type": "SINGLE",
                "http_method": "PUT",
            }
        else:
            data = {"transfer_id": "transfer-1", "status": self.complete_status}
        return httpx.Response(200, request=request, json={"code": 0, "data": data})


class _Resolver:
    def resolve_for_bot(self, bot_id, owner_id):
        return type(
            "Context",
            (),
            {
                "provider": "baas",
                "conn_info": {"tenant": "tenant-1", "bot_uuid": "bot-uuid-1"},
            },
        )()


class _Queue:
    def __init__(self, error=None) -> None:
        self.calls = []
        self.error = error

    def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
        if self.error:
            raise self.error
        self.calls.append((task_type, payload, deadline_seconds, kwargs))


class _Transport:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls = []
        self.closed = False

    async def stream(self, conn_info, method, path, body=None, params=None, *, timeout=None):
        self.calls.append((conn_info, method, path, body, params, timeout))

        async def chunks() -> AsyncIterator[bytes]:
            yield b"materialized bytes"

        async def close() -> None:
            self.closed = True

        return DeviceAdapterStreamResponse(
            status_code=self.status_code,
            headers={"content-type": "text/plain", "x-internal": "hidden"},
            body=chunks(),
            close=close,
        )


def _service(queue=None, *, complete_status="DONE", transport=None):
    repo = _Repo()
    http = _HttpClient(complete_status=complete_status)
    service = SessionResourceService(
        repository=repo,
        baas_client=SessionResourceBaasClient(http),
        task_queue=queue or _Queue(),
        device_context_resolver=_Resolver(),
        token_vault=TokenVault(master_key="test-master-key"),
        adapter_transport=transport or _Transport(),
    )
    return service, repo, http


def _intent(service):
    return service.create_upload_intent(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session/raw value",
        scope_type="personal_bot_chat",
        engine_type="claude_code",
        filename="report.txt",
        size_bytes=4,
    )


def test_upload_intent_uses_session_api_and_persists_encrypted_session_key():
    service, repo, http = _service()

    intent = _intent(service)

    path, kwargs = http.calls[0]
    assert path == "/api/v1/sessions/tenant-1/session%2Fraw%20value/files/upload-url"
    assert kwargs["json"] == {
        "filename": "report.txt",
        "file_size": 4,
        "operator": "owner-1",
        "expire_seconds": 3600,
    }
    assert intent.grant.upload_type == "SINGLE"
    assert repo.value.session_key_ciphertext != "session/raw value"
    assert repo.value.session_key_hash != "session/raw value"
    assert repo.value.transfer_api_version.value == "session_v2"


def test_upload_complete_requires_baas_done_then_queues_identity_only():
    queue = _Queue()
    service, repo, http = _service(queue)
    intent = _intent(service)

    result = service.complete_upload(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session/raw value",
        resource_id=intent.resource.resource_id,
        transfer_id="transfer-1",
    )

    assert result.status is SessionResourceStatus.DEVICE_SYNCING
    assert http.calls[1][0].endswith("/upload-url/transfer-1/complete")
    payload = queue.calls[0][1]
    assert set(payload) == {"resource_id", "task_id", "task_version"}
    assert "session/raw value" not in repr(payload)
    assert repo.value.status is SessionResourceStatus.DEVICE_SYNCING


def test_upload_complete_does_not_dispatch_when_baas_is_not_done():
    queue = _Queue()
    service, repo, _ = _service(queue, complete_status="UPLOADING")
    intent = _intent(service)

    with pytest.raises(ValueError, match="transfer_not_done"):
        service.complete_upload(
            owner_id="owner-1",
            bot_id="bot-1",
            session_key="session/raw value",
            resource_id=intent.resource.resource_id,
            transfer_id="transfer-1",
        )

    assert not queue.calls
    assert repo.value.status is SessionResourceStatus.UPLOAD_URL_ISSUED


def test_upload_and_completion_logs_do_not_expose_signed_url_or_raw_session(caplog):
    caplog.set_level("INFO")
    service, _, _ = _service()
    intent = _intent(service)

    service.complete_upload(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session/raw value",
        resource_id=intent.resource.resource_id,
        transfer_id="transfer-1",
    )

    assert "Signature=secret" not in caplog.text
    assert "https://oss.example" not in caplog.text
    assert "session/raw value" not in caplog.text


def test_upload_complete_enqueue_failure_compensates_to_failed():
    service, repo, _ = _service(_Queue(RuntimeError("queue unavailable")))
    intent = _intent(service)

    with pytest.raises(RuntimeError, match="queue unavailable"):
        service.complete_upload(
            owner_id="owner-1",
            bot_id="bot-1",
            session_key="session/raw value",
            resource_id=intent.resource.resource_id,
            transfer_id="transfer-1",
        )

    assert repo.value.status is SessionResourceStatus.DEVICE_SYNC_FAILED
    assert repo.value.error_code == "dispatch_failed"


@pytest.mark.asyncio
async def test_content_streams_from_engine_without_baas_download_call():
    transport = _Transport()
    service, repo, http = _service(transport=transport)
    intent = _intent(service)
    repo.value = replace(intent.resource, status=SessionResourceStatus.READY)

    record, response = await service.open_content(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session/raw value",
        resource_id=intent.resource.resource_id,
        disposition="inline",
    )

    assert record.resource_id == intent.resource.resource_id
    assert transport.calls[0][2].endswith(f"/{intent.resource.resource_id}/content")
    assert len(http.calls) == 1
    assert [chunk async for chunk in response.body] == [b"materialized bytes"]
    await response.close()
    assert transport.closed is True


@pytest.mark.asyncio
async def test_missing_engine_file_requires_reupload_without_rematerialization():
    queue = _Queue()
    transport = _Transport(status_code=409)
    service, repo, http = _service(queue, transport=transport)
    intent = _intent(service)
    repo.value = replace(intent.resource, status=SessionResourceStatus.READY)

    with pytest.raises(ValueError, match="resource_missing"):
        await service.open_content(
            owner_id="owner-1",
            bot_id="bot-1",
            session_key="session/raw value",
            resource_id=intent.resource.resource_id,
            disposition="attachment",
        )

    assert transport.closed is True
    assert repo.value.status is SessionResourceStatus.READY
    assert not queue.calls
    assert len(http.calls) == 1


def test_list_pending_excludes_ready_and_deleted_resources():
    service, repo, _ = _service()
    intent = _intent(service)

    pending = service.list_pending(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session/raw value",
    )

    assert [record.resource_id for record in pending] == [intent.resource.resource_id]
    repo.value = replace(intent.resource, status=SessionResourceStatus.READY)
    assert not service.list_pending(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session/raw value",
    )
