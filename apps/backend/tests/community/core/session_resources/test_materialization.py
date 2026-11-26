from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.session_resources.materialization import (
    SessionResourceMaterializeHandler,
)
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
    TransferApiVersion,
)
from agentclaw.community.core.session_resources.types import hash_identifier
from agentclaw.community.core.task_queue.types import Complete, Retry


class _Resolver:
    def __init__(self, conn_info=None, *, provider="baas") -> None:
        self._conn_info = conn_info or {"target": "engine"}
        self._provider = provider
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
            {"conn_info": self._conn_info, "provider": self._provider},
        )()


class _Transport:
    def __init__(self, accepted=True) -> None:
        self.accepted = accepted
        self.calls = []

    async def invoke(self, conn_info, method, path, body=None, params=None):
        self.calls.append((conn_info, method, path, body))
        return {"accepted": self.accepted}


class _Repo:
    def __init__(self, record) -> None:
        self.record = record

    def get_by_resource_id(self, resource_id):
        if self.record.resource_id == resource_id:
            return self.record
        return None


def _record(vault: TokenVault) -> SessionResourceRecord:
    session_key = "session-raw"
    return SessionResourceRecord(
        resource_id="sr_001",
        owner_id="owner-1",
        bot_id="bot-1",
        scope_type="personal_bot_chat",
        scope_key_hash="scope-hash",
        session_key_hash=hash_identifier(session_key),
        engine_type="claude_code",
        tenant="tenant-1",
        bot_uuid="bot-uuid-1",
        display_name="a.txt",
        creator_name="上传者",
        filename="a.txt",
        device_path="workspace/.teamclaw/session-files/scope-hash/session-hash/sr_001/a.txt",
        workspace_relative_path=".teamclaw/session-files/scope-hash/session-hash/sr_001/a.txt",
        transfer_id="transfer-1",
        status=SessionResourceStatus.DEVICE_SYNCING,
        transfer_api_version=TransferApiVersion.SESSION_V2,
        session_key_ciphertext=vault.encrypt(session_key),
        task_id="task-1",
        task_version=1,
        size_bytes=1,
        client_content_hash="hash",
        gmt_create=datetime(2026, 8, 3, 10, 20, 30, tzinfo=UTC),
    )


def _payload():
    return {"resource_id": "sr_001", "task_id": "task-1", "task_version": 1}


def test_handler_reloads_decrypts_and_dispatches_to_shared_engine_endpoint():
    vault = TokenVault(master_key="test-master-key")
    transport = _Transport()
    handler = SessionResourceMaterializeHandler(
        _Resolver(),
        transport,
        _Repo(_record(vault)),
        vault,
    )

    result = handler.handle(_payload())

    assert isinstance(result, Complete)
    body = transport.calls[0][3]
    assert transport.calls[0][1:3] == ("POST", "/api/resource-materializations")
    assert body["session_id"] == "session-raw"
    assert body["transfer_api_version"] == "session_v2"
    assert body["workspace_relative_path"].startswith(".teamclaw/")
    assert body["uploaded_at"] == "2026-08-03T10:20:30Z"
    assert body["creator_name"] == "上传者"
    assert "owner_id" not in body


def test_session_v2_empty_bot_uuid_uses_the_given_arca_proxypass_context():
    vault = TokenVault(master_key="test-master-key")
    arca_context = {
        "url": "https://proxypass.example/proxypass/ARCA_test",
        "headers": {"x-proxypass-token": "secret-token"},
        "use_proxy": True,
    }
    transport = _Transport()
    handler = SessionResourceMaterializeHandler(
        _Resolver(arca_context, provider="arca"),
        transport,
        _Repo(replace(_record(vault), tenant="team_claw", bot_uuid="")),
        vault,
    )

    result = handler.handle(_payload())

    assert isinstance(result, Complete)
    conn_info, method, path, body = transport.calls[0]
    assert conn_info is arca_context
    assert (method, path) == ("POST", "/api/resource-materializations")
    assert body["tenant"] == "team_claw"
    assert "bot_uuid" not in body
    assert "bot_uuid" not in path


def test_handler_routes_new_resource_with_persisted_binding_to_target_engine():
    vault = TokenVault(master_key="test-master-key")
    resolver = _Resolver()
    transport = _Transport()
    handler = SessionResourceMaterializeHandler(
        resolver,
        transport,
        _Repo(replace(_record(vault), binding_id=77)),
        vault,
    )

    result = handler.handle(_payload())

    assert isinstance(result, Complete)
    assert resolver.binding_calls == [(77, "owner-1", "bot-1")]
    assert resolver.bot_calls == []


def test_handler_leaves_upload_time_empty_for_legacy_record():
    vault = TokenVault(master_key="test-master-key")
    transport = _Transport()
    handler = SessionResourceMaterializeHandler(
        _Resolver(),
        transport,
        _Repo(replace(_record(vault), gmt_create=None, creator_name=None)),
        vault,
    )

    result = handler.handle(_payload())

    assert isinstance(result, Complete)
    assert transport.calls[0][3]["uploaded_at"] is None
    assert transport.calls[0][3]["creator_name"] is None


def test_handler_ignores_stale_task_without_calling_engine():
    vault = TokenVault(master_key="test-master-key")
    transport = _Transport()
    handler = SessionResourceMaterializeHandler(
        _Resolver(),
        transport,
        _Repo(replace(_record(vault), task_version=2)),
        vault,
    )

    result = handler.handle(_payload())

    assert isinstance(result, Complete)
    assert transport.calls == []


def test_handler_retries_when_engine_does_not_accept():
    vault = TokenVault(master_key="test-master-key")
    handler = SessionResourceMaterializeHandler(
        _Resolver(),
        _Transport(False),
        _Repo(_record(vault)),
        vault,
    )

    result = handler.handle(_payload())

    assert isinstance(result, Retry)


@pytest.mark.parametrize(
    ("stored_time", "expected"),
    [
        (datetime(2026, 11, 10, 17, 48, 35), "2026-11-10T09:48:35Z"),
        (datetime(2026, 1, 1, 0, 5, 0), "2025-12-31T16:05:00Z"),
        (datetime(2026, 11, 10, 17, 48, 35, 123456), "2026-11-10T09:48:35.123456Z"),
        (datetime(2026, 11, 10, 9, 48, 35, tzinfo=UTC), "2026-11-10T09:48:35Z"),
        (
            datetime(2026, 11, 10, 17, 48, 35, tzinfo=timezone(timedelta(hours=8))),
            "2026-11-10T09:48:35Z",
        ),
        (
            datetime(2026, 11, 10, 4, 48, 35, tzinfo=timezone(timedelta(hours=-5))),
            "2026-11-10T09:48:35Z",
        ),
        (None, None),
    ],
)
def test_handler_serializes_database_upload_time_as_utc(stored_time, expected, caplog):
    vault = TokenVault(master_key="test-master-key")
    transport = _Transport()
    handler = SessionResourceMaterializeHandler(
        _Resolver({"headers": {"Authorization": "Bearer private-token"}}),
        transport,
        _Repo(replace(_record(vault), gmt_create=stored_time)),
        vault,
    )
    with caplog.at_level(logging.INFO, logger="session_resource.materialization"):
        result = handler.handle(_payload())

    assert isinstance(result, Complete)
    assert transport.calls[0][1:3] == ("POST", "/api/resource-materializations")
    assert transport.calls[0][3]["uploaded_at"] == expected
    if stored_time is not None and stored_time.tzinfo is None:
        assert "session_resource.materialize.upload_time.normalized" in caplog.text
        assert "resource_id=sr_001" in caplog.text
        assert "source_timezone=Asia/Shanghai" in caplog.text
        assert f"uploaded_at={expected}" in caplog.text
    assert "session_resource.materialize.dispatch.accepted" in caplog.text
    assert "private-token" not in caplog.text
    assert "test-master-key" not in caplog.text


def test_handler_preserves_retry_after_normalizing_upload_time(caplog):
    vault = TokenVault(master_key="test-master-key")
    transport = _Transport(accepted=False)
    handler = SessionResourceMaterializeHandler(
        _Resolver(), transport,
        _Repo(replace(_record(vault), gmt_create=datetime(2026, 11, 10, 17, 48, 35))),
        vault,
    )
    with caplog.at_level(logging.INFO, logger="session_resource.materialization"):
        result = handler.handle(_payload())

    assert isinstance(result, Retry)
    assert transport.calls[0][3]["uploaded_at"] == "2026-11-10T09:48:35Z"
    assert "session_resource.materialize.upload_time.normalized" in caplog.text
    assert "session_resource.materialize.dispatch.retry" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
