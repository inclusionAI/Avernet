from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from agentclaw.community.core.bot_management.token_vault import TokenVault
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


def _record() -> SessionResourceRecord:
    return SessionResourceRecord(
        resource_id="sr_001",
        owner_id="user-1",
        bot_id="bot-1",
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


def _service(record: SessionResourceRecord | None) -> TcResourceContextService:
    return TcResourceContextService(_Repository(record), TokenVault(""))


def test_context_snapshot_maps_authoritative_direct_session_state() -> None:
    snapshot = _service(_record()).resolve("sr_001")

    assert snapshot.as_payload() == {
        "resource_id": "sr_001",
        "status": "ready",
        "deleted": False,
        "transfer_id": "transfer-1",
        "tenant": "tenant-1",
        "bot_uuid": "bot-uuid-1",
        "user_id": "user-1",
        "bot_id": "bot-1",
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
