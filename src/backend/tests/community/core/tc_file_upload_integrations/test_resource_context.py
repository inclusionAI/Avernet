from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
)
from agentclaw.community.core.tc_file_upload_integrations.resource_context import (
    TcResourceContextService,
)


class _Repository:
    def __init__(self, record: SessionResourceRecord | None) -> None:
        self.record = record

    def get_by_resource_id(self, resource_id: str) -> SessionResourceRecord | None:
        if self.record is not None and self.record.resource_id == resource_id:
            return self.record
        return None


class _DeviceBindingRepository:
    def __init__(self, binding: DeviceBindingRecord | None) -> None:
        self.binding = binding

    def get_by_id(self, binding_id: int) -> DeviceBindingRecord | None:
        if self.binding is not None and self.binding.id == binding_id:
            return self.binding
        return None


def _binding() -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=11,
        entity_id="owner-490906",
        entity_type="staff",
        device_id="bot-uuid-1",
        device_provider="baas",
        env="dev",
        device_props={},
        status="RELEASED",
        apply_reason=None,
        applied_by="user-437240",
        release_reason="released after upload",
        released_by="user-437240",
        released_at=None,
        last_alive_at=None,
        gmt_create=None,
        gmt_modified=None,
    )


def _record() -> SessionResourceRecord:
    return SessionResourceRecord(
        resource_id="sr_001",
        owner_id="user-437240",
        bot_id="bot-1",
        binding_id=11,
        scope_type="session",
        scope_key_hash="scope-hash",
        session_key_hash="session-hash",
        engine_type="openclaw",
        tenant="tenant-1",
        bot_uuid="bot-uuid-1",
        display_name="note.md",
        filename="note.md",
        device_path="workspace/note.md",
        workspace_relative_path="note.md",
        transfer_id="transfer-1",
        status=SessionResourceStatus.READY,
        session_key_ciphertext="session-1",
        task_version=8,
        size_bytes=7,
        client_content_hash="A" * 64,
    )


def _service(
    record: SessionResourceRecord | None,
    binding: DeviceBindingRecord | None = None,
) -> TcResourceContextService:
    return TcResourceContextService(
        _Repository(record),
        TokenVault(""),
        _DeviceBindingRepository(_binding() if binding is None else binding),
    )


def test_context_snapshot_maps_uploader_and_bot_owner_independently() -> None:
    snapshot = _service(_record()).resolve("sr_001")

    assert snapshot.as_payload() == {
        "resource_id": "sr_001",
        "status": "ready",
        "deleted": False,
        "transfer_id": "transfer-1",
        "tenant": "tenant-1",
        "bot_uuid": "bot-uuid-1",
        "user_id": "user-437240",
        "bot_id": "bot-1",
        "owner_id": "owner-490906",
        "filename": "note.md",
        "size_bytes": 7,
        "content_sha256": "a" * 64,
        "session_namespace": "tc",
        "session_id": "session-1",
        "conversation_id": "session-1",
        "scope_type": "direct",
        "group_id": None,
        "members": [],
        "session_revision": 8,
        "session_active": True,
    }


@pytest.mark.parametrize("binding_id", [None, 0, -1, True, "11", 11.0])
def test_context_snapshot_rejects_invalid_binding_id(binding_id) -> None:
    record = replace(_record(), binding_id=binding_id)

    with pytest.raises(ValueError, match="resource_context_incomplete"):
        _service(record).resolve("sr_001")


def test_context_snapshot_rejects_missing_binding() -> None:
    service = TcResourceContextService(
        _Repository(_record()),
        TokenVault(""),
        _DeviceBindingRepository(None),
    )

    with pytest.raises(ValueError, match="resource_context_incomplete"):
        service.resolve("sr_001")


@pytest.mark.parametrize("entity_id", ["", "   "])
def test_context_snapshot_rejects_empty_binding_entity_id(entity_id: str) -> None:
    binding = replace(_binding(), entity_id=entity_id)

    with pytest.raises(ValueError, match="resource_context_incomplete"):
        _service(_record(), binding).resolve("sr_001")


def test_context_snapshot_rejects_binding_device_mismatch() -> None:
    binding = replace(_binding(), device_id="other-bot-uuid")

    with pytest.raises(ValueError, match="resource_context_incomplete"):
        _service(_record(), binding).resolve("sr_001")


def test_context_snapshot_accepts_personal_bot_chat_as_direct_scope() -> None:
    record = replace(_record(), scope_type="personal_bot_chat")

    snapshot = _service(record).resolve("sr_001")

    assert snapshot.scope_type == "direct"
    assert snapshot.group_id is None
    assert snapshot.members == ()


def test_context_snapshot_marks_deleted_state_inactive() -> None:
    record = replace(
        _record(),
        status=SessionResourceStatus.DELETED,
        deleted_at=datetime.now(timezone.utc),
    )

    snapshot = _service(record).resolve("sr_001")

    assert snapshot.deleted is True
    assert snapshot.session_active is False


@pytest.mark.parametrize(
    ("record", "error"),
    [
        (None, "resource_not_found"),
        (
            replace(_record(), session_key_ciphertext=None),
            "resource_context_incomplete",
        ),
        (replace(_record(), scope_type="group"), "resource_context_unsupported_scope"),
    ],
)
def test_context_snapshot_rejects_missing_or_incomplete_authority(
    record, error
) -> None:
    with pytest.raises(ValueError, match=error):
        _service(record).resolve("sr_001")
