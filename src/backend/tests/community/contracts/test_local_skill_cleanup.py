"""Conformance coverage for durable Local Skill cleanup persistence."""

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
