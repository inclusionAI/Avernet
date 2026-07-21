from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from agentclaw.community.core.session_resources.baas_client import (
    SessionResourceBaasClient,
)
from agentclaw.community.core.session_resources.service import SessionResourceService
from agentclaw.community.core.session_resources.types import (
    SessionResourceStatus,
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
        return [value] if value and self.get_owned(value.resource_id, owner_id, bot_id, session_key_hash) else []

    def cas_start_materialization(self, **kwargs):
        if self.value.status not in {
            SessionResourceStatus.UPLOAD_URL_ISSUED,
            SessionResourceStatus.DEVICE_SYNC_FAILED,
        }:
            return None
        self.value = replace(
            self.value,
            status=SessionResourceStatus.DEVICE_SYNCING,
            task_id=kwargs["task_id"],
            task_version=self.value.task_version + 1,
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
    def __init__(self) -> None:
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        request = httpx.Request("POST", f"https://baas.example{path}")
        if path.endswith("upload-url"):
            data = {
                "upload_url": "https://oss.example/object?Signature=secret",
                "transfer_id": "transfer-1",
                "expires_at": "2026-07-13T00:00:00Z",
            }
        else:
            data = {
                "download_url": "https://oss.example/download?Signature=secret",
                "filename": "report.txt",
                "file_size": 4,
                "expires_at": "2026-07-13T00:00:00Z",
            }
        return httpx.Response(200, request=request, json={"data": data, "error": None})


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


def _service(queue=None):
    repo = _Repo()
    http = _HttpClient()
    service = SessionResourceService(
        repository=repo,
        baas_client=SessionResourceBaasClient(http),
        task_queue=queue or _Queue(),
        device_context_resolver=_Resolver(),
    )
    return service, repo, http


def test_upload_complete_persists_syncing_before_durable_dispatch():
    queue = _Queue()
    service, repo, _ = _service(queue)
    intent = service.create_upload_intent(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session-raw",
        scope_type="personal_bot_chat",
        engine_type="claude_code",
        filename="report.txt",
    )

    result = service.complete_upload(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session-raw",
        resource_id=intent.resource.resource_id,
        transfer_id="transfer-1",
    )

    assert result.status is SessionResourceStatus.DEVICE_SYNCING
    assert repo.value.status is SessionResourceStatus.DEVICE_SYNCING
    assert queue.calls[0][0] == "session_resource.materialize"
    assert queue.calls[0][1]["task_version"] == 1


def test_upload_complete_enqueue_failure_compensates_to_failed():
    service, repo, _ = _service(_Queue(RuntimeError("queue unavailable")))
    intent = service.create_upload_intent(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session-raw",
        scope_type="personal_bot_chat",
        engine_type="claude_code",
        filename="report.txt",
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        service.complete_upload(
            owner_id="owner-1",
            bot_id="bot-1",
            session_key="session-raw",
            resource_id=intent.resource.resource_id,
            transfer_id="transfer-1",
        )

    assert repo.value.status is SessionResourceStatus.DEVICE_SYNC_FAILED
    assert repo.value.error_code == "dispatch_failed"


def test_download_requires_ready_before_calling_baas():
    service, _, http = _service()
    intent = service.create_upload_intent(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session-raw",
        scope_type="personal_bot_chat",
        engine_type="claude_code",
        filename="report.txt",
    )

    with pytest.raises(ValueError, match="resource_not_ready"):
        service.create_download_grant(
            owner_id="owner-1",
            bot_id="bot-1",
            session_key="session-raw",
            resource_id=intent.resource.resource_id,
        )

    assert len(http.calls) == 1


def test_ready_callback_rejects_relative_path_different_from_backend_path():
    service, repo, _ = _service()
    intent = service.create_upload_intent(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session-raw",
        scope_type="personal_bot_chat",
        engine_type="claude_code",
        filename="report.txt",
    )
    syncing = service.complete_upload(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session-raw",
        resource_id=intent.resource.resource_id,
        transfer_id="transfer-1",
    )

    with pytest.raises(ValueError, match="materialized_ref_mismatch"):
        service.materialized_callback(
            resource_id=syncing.resource_id,
            transfer_id=syncing.transfer_id,
            task_id=syncing.task_id,
            task_version=syncing.task_version,
            ready=True,
            materialized_ref={"relative_path": ".teamclaw/session-files/other.txt"},
            error_code=None,
        )

    assert repo.value.status is SessionResourceStatus.DEVICE_SYNCING


def test_polling_becomes_ready_only_after_matching_callback():
    service, _, _ = _service()
    intent = service.create_upload_intent(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session-raw",
        scope_type="personal_bot_chat",
        engine_type="claude_code",
        filename="report.txt",
    )
    syncing = service.complete_upload(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session-raw",
        resource_id=intent.resource.resource_id,
        transfer_id="transfer-1",
    )

    before = service.get_status(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session-raw",
        resource_id=syncing.resource_id,
    )
    assert before.status is SessionResourceStatus.DEVICE_SYNCING

    applied = service.materialized_callback(
        resource_id=syncing.resource_id,
        transfer_id=syncing.transfer_id,
        task_id=syncing.task_id,
        task_version=syncing.task_version,
        ready=True,
        materialized_ref={
            "relative_path": syncing.workspace_relative_path,
            "path_hash": "hashed-only",
        },
        error_code=None,
    )
    after = service.get_status(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session-raw",
        resource_id=syncing.resource_id,
    )

    assert applied.status is SessionResourceStatus.READY
    assert after.status is SessionResourceStatus.READY


def test_upload_logs_do_not_expose_signed_url_or_raw_session(caplog):
    caplog.set_level("INFO")
    service, _, _ = _service()

    service.create_upload_intent(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="raw-session-secret",
        scope_type="personal_bot_chat",
        engine_type="claude_code",
        filename="report.txt",
    )

    logs = caplog.text
    assert "Signature=secret" not in logs
    assert "https://oss.example" not in logs
    assert "raw-session-secret" not in logs
