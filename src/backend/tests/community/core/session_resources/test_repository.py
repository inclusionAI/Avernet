from __future__ import annotations

import pytest
import pytest_asyncio

from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
)
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests
from agentclaw.community.plugins.session_resource_repository import (
    SessionResourceRepository,
)


def _record() -> SessionResourceRecord:
    return SessionResourceRecord(
        resource_id="sr_001",
        owner_id="owner-1",
        bot_id="bot-1",
        scope_type="personal_bot_chat",
        scope_key_hash="scope-hash",
        session_key_hash="session-hash",
        engine_type="claude_code",
        tenant="tenant-1",
        bot_uuid="bot-uuid-1",
        display_name="report.txt",
        filename="report.txt",
        device_path=(
            "workspace/.teamclaw/session-files/scope-hash/session-hash/"
            "sr_001/report.txt"
        ),
        workspace_relative_path=(
            ".teamclaw/session-files/scope-hash/session-hash/sr_001/report.txt"
        ),
        transfer_id="transfer-1",
        status=SessionResourceStatus.UPLOAD_URL_ISSUED,
    )


@pytest_asyncio.fixture
async def repo():
    reset_for_tests()
    db = SqliteDB()
    await db.bootstrap()
    yield SessionResourceRepository(db)
    reset_for_tests()


@pytest.mark.asyncio
async def test_owned_lookup_requires_owner_bot_and_session(repo):
    repo.create(_record())

    assert repo.get_owned("sr_001", "owner-1", "bot-1", "session-hash") is not None
    assert repo.get_owned("sr_001", "owner-2", "bot-1", "session-hash") is None
    assert repo.get_owned("sr_001", "owner-1", "bot-2", "session-hash") is None
    assert repo.get_owned("sr_001", "owner-1", "bot-1", "other-session") is None


@pytest.mark.asyncio
async def test_task_version_cas_rejects_stale_callback(repo):
    repo.create(_record())
    started = repo.cas_start_materialization(
        resource_id="sr_001",
        owner_id="owner-1",
        bot_id="bot-1",
        session_key_hash="session-hash",
        transfer_id="transfer-1",
        task_id="task-1",
    )
    assert started is not None
    assert started.status is SessionResourceStatus.DEVICE_SYNCING
    assert started.task_version == 1

    stale = repo.cas_finish_materialization(
        resource_id="sr_001",
        transfer_id="transfer-1",
        task_id="task-1",
        task_version=0,
        ready=True,
        materialized_ref={"path_hash": "old"},
        error_code=None,
    )
    assert stale is None
    current = repo.get_by_resource_id("sr_001")
    assert current.status is SessionResourceStatus.DEVICE_SYNCING

    finished = repo.cas_finish_materialization(
        resource_id="sr_001",
        transfer_id="transfer-1",
        task_id="task-1",
        task_version=1,
        ready=True,
        materialized_ref={"path_hash": "new"},
        error_code=None,
    )
    assert finished.status is SessionResourceStatus.READY
    assert finished.materialized_ref == {"path_hash": "new"}


@pytest.mark.asyncio
async def test_deleted_resource_cannot_be_finished_by_callback(repo):
    repo.create(_record())
    started = repo.cas_start_materialization(
        resource_id="sr_001",
        owner_id="owner-1",
        bot_id="bot-1",
        session_key_hash="session-hash",
        transfer_id="transfer-1",
        task_id="task-1",
    )
    repo.soft_delete("sr_001", "owner-1", "bot-1", "session-hash")

    result = repo.cas_finish_materialization(
        resource_id="sr_001",
        transfer_id="transfer-1",
        task_id="task-1",
        task_version=started.task_version,
        ready=True,
        materialized_ref={"path_hash": "late"},
        error_code=None,
    )

    assert result is None
    assert repo.get_by_resource_id("sr_001").status is SessionResourceStatus.DELETED


@pytest.mark.asyncio
async def test_soft_delete_is_idempotent_without_nested_session(repo):
    repo.create(_record())

    first = repo.soft_delete("sr_001", "owner-1", "bot-1", "session-hash")
    second = repo.soft_delete("sr_001", "owner-1", "bot-1", "session-hash")

    assert first.status is SessionResourceStatus.DELETED
    assert second.status is SessionResourceStatus.DELETED
