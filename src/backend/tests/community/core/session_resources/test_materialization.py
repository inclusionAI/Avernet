from __future__ import annotations

from dataclasses import replace

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
    def resolve_for_bot(self, bot_id, owner_id):
        return type(
            "Context",
            (),
            {"conn_info": {"target": "engine"}, "provider": "baas"},
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
    assert "owner_id" not in body


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
