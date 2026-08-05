"""Conformance coverage for durable Local Skill cleanup persistence."""

from hashlib import sha256
from pathlib import Path

import pytest

from agentclaw.community.core.skill_center.local_skill_cleanup import LocalSkillCleanupWorkModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.local_skill_cleanup import LocalSkillCleanupRepository


def test_cleanup_repository_persists_and_progresses_one_bot_scoped_work_item(world) -> None:
    repo = world.get(LocalSkillCleanupRepository)
    assert repo.record_pending(
        env="dev", owner_id="cleanup-owner", bot_id="cleanup-bot",
        skill_id="7", package_locator="pool/local/obsolete-version",
        requires_runtime_restore=False,
    )
    pending = repo.list_pending(env="dev", owner_id="cleanup-owner", bot_id="cleanup-bot")
    assert len(pending) == 1
    assert pending[0]["package_locator"] == "pool/local/obsolete-version"
    scope = {"env": "dev", "owner_id": "cleanup-owner", "bot_id": "cleanup-bot"}
    assert repo.mark_failed(
        work_id=pending[0]["id"], error="obsolete package cleanup failed", **scope
    )
    assert repo.mark_cleaned(work_id=pending[0]["id"], **scope)
    assert repo.list_pending(env="dev", owner_id="cleanup-owner", bot_id="cleanup-bot") == []


def test_cleanup_preparation_is_durable_but_not_purgeable_until_committed(world) -> None:
    repo = world.get(LocalSkillCleanupRepository)
    scope = {"env": "dev", "owner_id": "owner", "bot_id": "bot"}
    work_id = repo.record_preparing(
        **scope, skill_id="9", package_locator="pool/local/delete-quarantine"
    )
    assert work_id is not None
    assert repo.list_pending(**scope) == []
    with world.get(DatabasePlugin).orm_session() as db:
        row = db.query(LocalSkillCleanupWorkModel).one()
        assert row.status == "preparing"
    assert repo.cancel_pending(work_id=work_id, **scope)


def test_cleanup_repository_isolated_by_the_full_deployment_wide_bot_scope(world) -> None:
    repo = world.get(LocalSkillCleanupRepository)
    values = {
        "env": "dev",
        "bot_id": "shared-bot-id",
        "skill_id": "8",
        "package_locator": "pool/local/obsolete-version",
        "requires_runtime_restore": False,
    }
    assert repo.record_pending(owner_id="owner-a", **values)
    assert repo.record_pending(owner_id="owner-a", **values)  # idempotent retry enqueue
    assert repo.record_pending(owner_id="owner-b", **values)

    owner_a = repo.list_pending(env="dev", owner_id="owner-a", bot_id="shared-bot-id")
    assert len(owner_a) == 1
    assert len(repo.list_pending(env="dev", owner_id="owner-b", bot_id="shared-bot-id")) == 1
    assert not repo.mark_cleaned(
        work_id=owner_a[0]["id"], env="dev", owner_id="owner-b", bot_id="shared-bot-id"
    )


def test_cleanup_upsert_reopens_work_and_monotonically_requires_runtime_restore(world) -> None:
    repo = world.get(LocalSkillCleanupRepository)
    scope = {"env": "dev", "owner_id": "owner", "bot_id": "bot"}
    locator = "pool/local/replacement-version"
    assert repo.record_pending(
        **scope, skill_id="9", package_locator=locator,
        requires_runtime_restore=False,
    )
    pending = repo.list_pending(**scope)
    assert repo.mark_failed(
        work_id=pending[0]["id"], error="cleanup failed", **scope
    )
    assert repo.record_pending(
        **scope, skill_id="9", package_locator=locator,
        requires_runtime_restore=True,
    )
    pending = repo.list_pending(**scope)
    assert len(pending) == 1
    assert pending[0]["requires_runtime_restore"] is True
    with world.get(DatabasePlugin).orm_session() as db:
        row = db.query(LocalSkillCleanupWorkModel).one()
        assert row.status == "pending"
        assert row.last_error is None


def test_cleanup_uses_full_locator_hash_and_rejects_a_hash_collision(world, monkeypatch) -> None:
    repo = world.get(LocalSkillCleanupRepository)
    scope = {"env": "dev", "owner_id": "owner", "bot_id": "bot"}
    locator = "pool/" + "a" * 1019
    assert repo.record_pending(
        **scope, skill_id="9", package_locator=locator,
        requires_runtime_restore=False,
    )
    with world.get(DatabasePlugin).orm_session() as db:
        row = db.query(LocalSkillCleanupWorkModel).one()
        assert row.package_locator_hash == sha256(locator.encode("utf-8")).hexdigest()

    monkeypatch.setattr(repo, "_locator_hash", lambda _locator: "f" * 64)
    first = "pool/first"
    second = "pool/second"
    assert repo.record_pending(
        **scope, skill_id="9", package_locator=first,
        requires_runtime_restore=False,
    )
    with pytest.raises(ValueError, match="hash collision"):
        repo.record_pending(
            **scope, skill_id="9", package_locator=second,
            requires_runtime_restore=False,
        )


def test_cleanup_repair_required_retains_the_quarantine_outside_purge_retries(world) -> None:
    repo = world.get(LocalSkillCleanupRepository)
    scope = {"env": "dev", "owner_id": "owner", "bot_id": "bot"}
    assert repo.record_repair_required(
        **scope, skill_id="9", package_locator="pool/local/delete-quarantine"
    )
    assert repo.list_pending(**scope) == []
    with world.get(DatabasePlugin).orm_session() as db:
        row = db.query(LocalSkillCleanupWorkModel).one()
        assert row.status == "repair_required"
        assert row.last_error == "authoritative package repair required"


def test_cleanup_lists_repair_required_work_only_for_the_affected_skill(world) -> None:
    repo = world.get(LocalSkillCleanupRepository)
    scope = {"env": "dev", "owner_id": "owner", "bot_id": "bot"}
    assert repo.record_repair_required(
        **scope, skill_id="9", package_locator="pool/local/delete-quarantine"
    )
    assert repo.record_repair_required(
        **scope, skill_id="10", package_locator="pool/local/other-quarantine"
    )

    repair_work = repo.list_repair_required(**scope, skill_id="9")

    assert len(repair_work) == 1
    assert repair_work[0]["package_locator"] == "pool/local/delete-quarantine"


def test_cleanup_ddl_uses_a_bounded_full_locator_digest_as_its_unique_key() -> None:
    sql = (
        Path(__file__).parents[3]
        / "src/agentclaw/community/core/skill_center/sql/2026_08_04_local_skill_cleanup_work.sql"
    ).read_text()
    assert "`package_locator_hash` CHAR(64) NOT NULL" in sql
    assert "`uk_local_skill_cleanup_scope_locator_hash` (`env`, `owner_id`, `bot_id`, `package_locator_hash`)" in sql
    assert "`uk_local_skill_cleanup_scope_locator` (`env`, `owner_id`, `bot_id`, `package_locator`)" not in sql
