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
from agentclaw.community.core.session_resources.types import (
    SessionResourceStatus,
    TransferApiVersion,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterStreamResponse,
)
from agentclaw.community.adapters.http.session_resources.router import _domain_error


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
    def __init__(self, *, provider="baas", conn_info=None) -> None:
        self._provider = provider
        self._conn_info = conn_info or {
            "tenant": "tenant-1",
            "bot_uuid": "bot-uuid-1",
        }
        self.bot_calls = []
        self.binding_calls = []

    def resolve_for_bot(self, bot_id, owner_id):
        self.bot_calls.append((bot_id, owner_id))
        return self._context()

    def resolve_for_binding(self, binding_id, operator_id, *, bot_id):
        self.binding_calls.append((binding_id, operator_id, bot_id))
        return self._context()

    def _context(self):
        return type(
            "Context",
            (),
            {
                "provider": self._provider,
                "conn_info": self._conn_info,
            },
        )()


class _BotRepository:
    def __init__(self, binding_id=42) -> None:
        self.binding_id = binding_id
        self.calls = []

    def get_by_id_and_owner(self, bot_id, owner_id):
        self.calls.append((bot_id, owner_id))
        if self.binding_id is None:
            return None
        return {"binding_id": self.binding_id}


class _BotFriendRepository:
    def __init__(self, relation=None) -> None:
        self.relation = relation
        self.calls = []

    def get_by_entity_ids(
        self,
        requester_entity_id,
        target_entity_id,
        target_bot_id,
    ):
        self.calls.append(
            (requester_entity_id, target_entity_id, target_bot_id)
        )
        return self.relation


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


def _service(
    queue=None,
    *,
    complete_status="DONE",
    transport=None,
    resolver=None,
    default_tenant="configured-tenant",
    bot_repository=None,
    bot_friend_repository=None,
):
    repo = _Repo()
    http = _HttpClient(complete_status=complete_status)
    service = SessionResourceService(
        repository=repo,
        baas_client=SessionResourceBaasClient(http),
        task_queue=queue or _Queue(),
        device_context_resolver=resolver or _Resolver(),
        token_vault=TokenVault(master_key="test-master-key"),
        adapter_transport=transport or _Transport(),
        default_tenant=default_tenant,
        bot_repository=bot_repository or _BotRepository(),
        bot_friend_repository=bot_friend_repository or _BotFriendRepository(),
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
    assert repo.value.binding_id == 42


def test_upload_intent_routes_friend_request_to_target_bot_binding():
    resolver = _Resolver()
    bot_repository = _BotRepository(binding_id=77)
    friend_repository = _BotFriendRepository({"status": "ACCEPTED"})
    service, repo, http = _service(
        resolver=resolver,
        bot_repository=bot_repository,
        bot_friend_repository=friend_repository,
    )

    intent = service.create_upload_intent(
        owner_id="requester-1",
        bot_id="default",
        session_key="friend-session",
        scope_type="friend_bot_chat",
        engine_type="openclaw",
        filename="report.txt",
        target_entity_id="target-owner-1",
        size_bytes=4,
    )

    assert intent.resource.owner_id == "requester-1"
    assert intent.resource.binding_id == 77
    assert friend_repository.calls == [
        ("requester-1", "target-owner-1", "default")
    ]
    assert bot_repository.calls == [("default", "target-owner-1")]
    assert resolver.binding_calls == [(77, "requester-1", "default")]
    assert resolver.bot_calls == []
    assert http.calls[0][1]["json"]["operator"] == "requester-1"
    assert repo.value == intent.resource


def test_upload_intent_uses_frontend_binding_without_target_lookup():
    resolver = _Resolver()
    bot_repository = _BotRepository(binding_id=None)
    friend_repository = _BotFriendRepository({"status": "PENDING"})
    service, repo, http = _service(
        resolver=resolver,
        bot_repository=bot_repository,
        bot_friend_repository=friend_repository,
    )

    intent = service.create_upload_intent(
        owner_id="requester-1",
        bot_id="default",
        session_key="friend-session",
        scope_type="friend_bot_chat",
        engine_type="openclaw",
        filename="report.txt",
        target_entity_id="target-owner-1",
        binding_id=91,
        size_bytes=4,
    )

    assert intent.resource.binding_id == 91
    assert resolver.binding_calls == [(91, "requester-1", "default")]
    assert bot_repository.calls == []
    assert friend_repository.calls == []
    assert repo.value == intent.resource
    assert len(http.calls) == 1


def test_upload_intent_rejects_unapproved_target_before_baas_call():
    bot_repository = _BotRepository(binding_id=77)
    friend_repository = _BotFriendRepository({"status": "PENDING"})
    service, repo, http = _service(
        bot_repository=bot_repository,
        bot_friend_repository=friend_repository,
    )

    with pytest.raises(ValueError, match="target_bot_access_denied"):
        service.create_upload_intent(
            owner_id="requester-1",
            bot_id="default",
            session_key="friend-session",
            scope_type="friend_bot_chat",
            engine_type="openclaw",
            filename="report.txt",
            target_entity_id="target-owner-1",
            size_bytes=4,
        )

    assert repo.value is None
    assert http.calls == []
    assert bot_repository.calls == []


def test_target_routing_errors_use_client_actionable_http_statuses():
    assert _domain_error(ValueError("target_bot_access_denied")).status_code == 403
    assert _domain_error(ValueError("bot_device_unavailable")).status_code == 409


def test_friend_upload_without_target_entity_uses_requester_binding_and_logs_warning(caplog):
    caplog.set_level("WARNING", logger="session_resource.service")
    resolver = _Resolver()
    service, _, _ = _service(resolver=resolver)

    _intent = service.create_upload_intent(
        owner_id="requester-1",
        bot_id="default",
        session_key="friend-session",
        scope_type="friend_bot_chat",
        engine_type="openclaw",
        filename="report.txt",
        size_bytes=4,
    )

    assert resolver.binding_calls == [(42, "requester-1", "default")]
    assert "target.fallback" in caplog.text
    assert "requester-1" not in caplog.text
    assert "friend-session" not in caplog.text


def test_upload_intent_defaults_missing_arca_identity_without_logging_raw_values(caplog):
    caplog.set_level("INFO", logger="session_resource.service")
    resolver = _Resolver(
        provider="arca",
        conn_info={
            "proxypass_url": "https://proxypass.example/internal",
            "x-proxypass-token": "secret-token",
        },
    )
    service, repo, http = _service(resolver=resolver)

    intent = _intent(service)
    service_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "session_resource.service"
    )

    assert http.calls[0][0] == (
        "/api/v1/sessions/configured-tenant/session%2Fraw%20value/files/upload-url"
    )
    assert intent.resource.tenant == "configured-tenant"
    assert repo.value.bot_uuid == ""
    assert "provider=arca" in service_logs
    assert "tenant_source=configured_default" in service_logs
    assert "bot_uuid_present=False" in service_logs
    assert "tenant_type=NoneType" in service_logs
    assert "bot_uuid_type=NoneType" in service_logs
    for raw_value in (
        "owner-1",
        "bot-1",
        "session/raw value",
        "report.txt",
        "secret-token",
        "https://proxypass.example/internal",
    ):
        assert raw_value not in service_logs
    for forbidden_field in (
        "resource_id=",
        "session_key_hash=",
        "file_ext=",
        "size_bytes=",
        "upload_type=",
    ):
        assert forbidden_field not in service_logs


def test_upload_intent_defaults_empty_tenant():
    resolver = _Resolver(
        provider="arca",
        conn_info={"tenant": "", "bot_uuid": "upstream-bot-uuid"},
    )
    service, repo, http = _service(resolver=resolver)

    intent = _intent(service)

    assert http.calls[0][0] == (
        "/api/v1/sessions/configured-tenant/session%2Fraw%20value/files/upload-url"
    )
    assert intent.resource.tenant == "configured-tenant"
    assert repo.value.bot_uuid == "upstream-bot-uuid"


def test_upload_intent_normalizes_nonstring_bot_uuid_without_leaking_value(caplog):
    caplog.set_level("INFO", logger="session_resource.service")
    resolver = _Resolver(
        provider="arca",
        conn_info={"tenant": "tenant-1", "bot_uuid": {"private": "value"}},
    )
    service, repo, _ = _service(resolver=resolver)

    intent = _intent(service)

    assert intent.resource.bot_uuid == ""
    assert repo.value.bot_uuid == ""
    assert "bot_uuid_present=False" in caplog.text
    assert "bot_uuid_type=dict" in caplog.text
    assert "private" not in caplog.text
    assert "value" not in caplog.text


def test_upload_intent_preserves_nonempty_upstream_identity():
    resolver = _Resolver(
        provider="arca",
        conn_info={"tenant": "upstream-tenant", "bot_uuid": "upstream-bot-uuid"},
    )
    service, repo, http = _service(resolver=resolver)

    intent = _intent(service)

    assert http.calls[0][0].startswith("/api/v1/sessions/upstream-tenant/")
    assert intent.resource.tenant == "upstream-tenant"
    assert repo.value.bot_uuid == "upstream-bot-uuid"


def test_upload_intent_rejects_nonstring_tenant_without_leaking_identity(caplog):
    caplog.set_level("WARNING", logger="session_resource.service")
    resolver = _Resolver(
        provider="arca",
        conn_info={"tenant": {"private-tenant": "hidden"}, "bot_uuid": "bot-uuid"},
    )
    service, repo, http = _service(resolver=resolver)

    with pytest.raises(ValueError, match="BaaS device identity is unavailable"):
        _intent(service)

    assert repo.value is None
    assert http.calls == []
    assert "provider=arca" in caplog.text
    assert "tenant_source=invalid" in caplog.text
    assert "tenant_type=dict" in caplog.text
    assert "bot_uuid_type=str" in caplog.text
    assert "private-tenant" not in caplog.text
    assert "hidden" not in caplog.text
    assert "bot-uuid" not in caplog.text


def test_upload_intent_rejects_missing_tenant_without_configured_default(caplog):
    caplog.set_level("WARNING", logger="session_resource.service")
    resolver = _Resolver(
        provider="arca",
        conn_info={"proxypass_url": "https://proxypass.example/internal"},
    )
    service, repo, http = _service(resolver=resolver, default_tenant="")

    with pytest.raises(ValueError, match="BaaS device identity is unavailable"):
        _intent(service)

    assert repo.value is None
    assert http.calls == []
    assert "provider=arca" in caplog.text
    assert "tenant_source=unconfigured_default" in caplog.text


def test_legacy_complete_uses_the_recorded_bot_uuid():
    service, repo, http = _service()
    intent = _intent(service)
    repo.value = replace(
        intent.resource,
        transfer_api_version=TransferApiVersion.BOT_DEVICE_V1,
        bot_uuid="legacy-bot-uuid",
    )

    service.complete_upload(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session/raw value",
        resource_id=intent.resource.resource_id,
        transfer_id="transfer-1",
    )

    assert (
        http.calls[1][0]
        == "/api/v1/bots/tenant-1/legacy-bot-uuid/files/upload-url/transfer-1/complete"
    )


def test_upload_intent_allows_unicode_filename_within_filesystem_limit():
    service, repo, http = _service()
    filename = "\u4e2d\u6587 \u62a5\u544a (final)\uff08\u5df2\u5ba1\uff09.txt"

    intent = service.create_upload_intent(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session/raw value",
        scope_type="personal_bot_chat",
        engine_type="openclaw",
        filename=filename,
        size_bytes=4,
    )

    assert http.calls[0][1]["json"]["filename"] == filename
    assert repo.value.filename == filename
    assert intent.resource.workspace_relative_path.endswith(f"/{filename}")


def test_upload_intent_rejects_filename_exceeding_utf8_segment_limit():
    service, _, _ = _service()

    with pytest.raises(ValueError, match="invalid_filename"):
        service.create_upload_intent(
            owner_id="owner-1",
            bot_id="bot-1",
            session_key="session/raw value",
            scope_type="personal_bot_chat",
            engine_type="openclaw",
            filename=f"{'\u4e2d' * 86}.txt",
            size_bytes=4,
        )


@pytest.mark.parametrize(
    "filename",
    [".", "..", "folder/report.txt", r"folder\report.txt", "report?.txt", "report\n.txt"],
)
def test_upload_intent_rejects_unsafe_filename_characters(filename):
    service, _, http = _service()

    with pytest.raises(ValueError, match="invalid_filename"):
        service.create_upload_intent(
            owner_id="owner-1",
            bot_id="bot-1",
            session_key="session/raw value",
            scope_type="personal_bot_chat",
            engine_type="openclaw",
            filename=filename,
            size_bytes=4,
        )

    assert http.calls == []


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
    resolver = _Resolver()
    service, repo, http = _service(transport=transport, resolver=resolver)
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
    assert resolver.binding_calls[-1] == (42, "owner-1", "bot-1")
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
