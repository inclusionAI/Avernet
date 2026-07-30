from __future__ import annotations

import json
from pathlib import Path

from engine.community.core.skills.layout_planner import (
    MAPPING_CONTRACT_VERSION,
)
from engine.community.plugins.aicoding.layout_pool import (
    PoolActivationStatus,
    RuntimeLayoutInspectionStatus,
    SkillMapping,
    activate_aicoding_pool,
    inspect_aicoding_runtime_layout,
    publish_aicoding_pool_mappings,
    rollback_aicoding_pool,
    verify_aicoding_pool_mappings,
)
from engine.community.plugins.skills_pool.layout_activation import (
    ActiveRepoRetirementError,
    mapping_sources_use_pool,
)

PREPARATION_ID = "2a958f59-8cf4-4413-a267-7d56d3382f23"


def _prepared_home(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    home = tmp_path / "home" / "admin"
    workspace = home / ".aicoding" / "workspace"
    legacy_local = workspace / "skills" / "skills-local"
    active_root = home / ".claude" / "skills"
    local_bridge = active_root / "skills-local"
    repo_bridge = home / ".aicoding" / "skills-repo"
    pool_root = workspace / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"

    (legacy_local / "handmade").mkdir(parents=True)
    (legacy_local / "handmade" / "SKILL.md").write_text("latest")
    (pool_local / "handmade").mkdir(parents=True)
    (pool_local / "handmade" / "SKILL.md").write_text("prepared")
    (pool_repo / "business" / "shared").mkdir(parents=True)
    (pool_repo / "business" / "shared" / "SKILL.md").write_text("repo")
    active_root.mkdir(parents=True, exist_ok=True)
    local_bridge.symlink_to(legacy_local, target_is_directory=True)
    repo_bridge.symlink_to(pool_repo, target_is_directory=True)

    marker = {
        "engine": "aicoding",
        "layout_contract_version": "skills-pool-p3-v1",
        "preparation_id": PREPARATION_ID,
        "prepared_at": "2026-07-24T00:00:00Z",
        "pool_local_root": str(pool_local),
        "pool_repo_root": str(pool_repo),
        "validation_summary": {
            "all_valid": True,
            "pool_local": {"path": str(pool_local), "valid": True},
            "pool_repo": {
                "path": str(pool_repo),
                "readable_mount": True,
                "valid": True,
            },
            "structural_bridges": [
                {
                    "name": "stable_local_bridge",
                    "path": str(local_bridge),
                    "target": str(legacy_local),
                    "valid": True,
                },
                {
                    "name": "stable_repo_bridge",
                    "path": str(repo_bridge),
                    "target": str(pool_repo),
                    "valid": True,
                },
            ],
            "managed_active_entries": [],
            "external_active_entry_count": 0,
        },
    }
    (pool_root / ".pool-ready").write_text(json.dumps(marker))
    return (
        home,
        legacy_local,
        local_bridge,
        repo_bridge,
        pool_local,
        pool_repo,
    )


def test_aicoding_probe_uses_own_pool_and_stable_bridges(tmp_path: Path) -> None:
    home, _, _, repo_bridge, _, pool_repo = _prepared_home(tmp_path)

    ready = inspect_aicoding_runtime_layout(
        home=home,
        mapping_contract_version=MAPPING_CONTRACT_VERSION,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert ready.status is RuntimeLayoutInspectionStatus.READY
    assert ready.engine == "aicoding"
    assert ready.preparation_id == PREPARATION_ID
    assert ready.evidence["checks"]["stable_local_bridge_valid"] is True
    assert ready.evidence["checks"]["stable_repo_bridge_valid"] is True
    assert (
        ready.evidence["mapping_contract_version"]
        == MAPPING_CONTRACT_VERSION
    )
    assert ready.evidence["resolved_layout"]["active_root"] == str(
        home / ".claude/skills"
    )

    legacy_consumer = inspect_aicoding_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert "mapping_contract_version" not in legacy_consumer.evidence

    repo_bridge.unlink()
    repo_bridge.symlink_to(home / "wrong", target_is_directory=True)
    invalid = inspect_aicoding_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert invalid.status is RuntimeLayoutInspectionStatus.INVALID
    assert invalid.evidence["reason"] == "stable_repo_bridge_invalid"


def test_aicoding_stable_repo_authority_follows_active_marker(
    tmp_path: Path,
) -> None:
    home, _, _, repo_bridge, _, _ = _prepared_home(tmp_path)
    source = repo_bridge / "business" / "shared"

    assert not mapping_sources_use_pool(
        engine="aicoding",
        sources=[source],
        home=home,
    )

    active_marker = (
        home / ".aicoding" / "workspace" / "skills-pool" / ".pool-active"
    )
    active_marker.write_text(
        json.dumps(
            {
                "engine": "aicoding",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": PREPARATION_ID,
                "migration_generation": "generation-1",
                "activation_state": "active",
            }
        )
    )

    assert mapping_sources_use_pool(
        engine="aicoding",
        sources=[source],
        home=home,
    )


def test_aicoding_activation_switches_local_and_keeps_repo_namespace(
    tmp_path: Path,
) -> None:
    (
        home,
        legacy_local,
        local_bridge,
        repo_bridge,
        pool_local,
        pool_repo,
    ) = _prepared_home(tmp_path)
    mappings = [
        SkillMapping(
            source=str(pool_local / "handmade"),
            target=str(local_bridge.parent / "handmade"),
        ),
        SkillMapping(
            source=str(pool_repo / "business" / "shared"),
            target=str(local_bridge.parent / "shared"),
        ),
    ]

    result = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=mappings,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert not legacy_local.exists()
    assert not legacy_local.is_symlink()
    assert not local_bridge.exists()
    assert not local_bridge.is_symlink()
    assert repo_bridge.is_symlink()
    assert repo_bridge.resolve() == pool_repo.resolve()
    assert (pool_local / "handmade" / "SKILL.md").read_text() == "latest"

    ready = inspect_aicoding_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert ready.status is RuntimeLayoutInspectionStatus.READY
    assert ready.evidence["checks"]["stable_repo_bridge_valid"] is True


def test_aicoding_activation_retires_full_corpus_from_active_root(
    tmp_path: Path,
) -> None:
    (
        home,
        _,
        local_bridge,
        _,
        pool_local,
        pool_repo,
    ) = _prepared_home(tmp_path)
    active_repo = local_bridge.parent / "skills-repo"
    (active_repo / "business" / "unactivated").mkdir(parents=True)
    calls: list[tuple[str, str]] = []

    def retire_active_repo(generation: str, preparation_id: str):
        calls.append((generation, preparation_id))
        marker = json.loads(
            (
                home / ".aicoding" / "workspace" / "skills-pool" / ".pool-active"
            ).read_text()
        )
        assert marker["activation_state"] == "finalizing"
        (active_repo / "business" / "unactivated").rmdir()
        (active_repo / "business").rmdir()
        active_repo.rmdir()
        return {"status": "retired"}

    result = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "handmade"),
                target=str(local_bridge.parent / "handmade"),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        retire_active_repo=retire_active_repo,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert calls == [("generation-1", PREPARATION_ID)]
    assert not active_repo.exists()


def test_aicoding_activation_reserves_active_repo_corpus_name(
    tmp_path: Path,
) -> None:
    home, legacy_local, local_bridge, _, pool_local, pool_repo = _prepared_home(
        tmp_path
    )
    (legacy_local / "skills-repo").mkdir()
    (legacy_local / "skills-repo" / "SKILL.md").write_text("reserved")

    result = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["skills-repo"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "skills-repo"),
                target=str(local_bridge.parent / "skills-repo"),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.INVALID
    assert result.evidence["reason"] == "mapping_source_invalid"
    assert result.evidence["failures"][0]["reason"] == "target_invalid"


def test_aicoding_activation_without_retirement_capability_stays_finalizing(
    tmp_path: Path,
) -> None:
    home, _, local_bridge, _, _, pool_repo = _prepared_home(tmp_path)
    active_repo = local_bridge.parent / "skills-repo"
    active_repo.mkdir()

    result = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.POST_CUTOVER_SYNC_PENDING
    assert result.evidence["reason"] == "active_repo_retirement_required"
    marker = json.loads(
        (home / ".aicoding" / "workspace" / "skills-pool" / ".pool-active").read_text()
    )
    assert marker["activation_state"] == "finalizing"


def test_aicoding_retirement_failure_stays_retryable_finalizing(
    tmp_path: Path,
) -> None:
    home, _, local_bridge, _, _, pool_repo = _prepared_home(tmp_path)
    active_repo = local_bridge.parent / "skills-repo"
    active_repo.mkdir()

    def reject_retirement(_generation: str, _preparation_id: str):
        raise ActiveRepoRetirementError(
            "active_repo_unmount_failed",
            error_type="BusyMount",
        )

    result = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        retire_active_repo=reject_retirement,
    )

    assert result.status is PoolActivationStatus.POST_CUTOVER_SYNC_PENDING
    assert result.evidence["reason"] == "active_repo_retirement_failed"
    assert result.evidence["retirement_reason"] == "active_repo_unmount_failed"
    assert result.evidence["error_type"] == "BusyMount"
    marker = json.loads(
        (home / ".aicoding" / "workspace" / "skills-pool" / ".pool-active").read_text()
    )
    assert marker["activation_state"] == "finalizing"


def test_aicoding_finalizing_retry_retires_active_repo_and_commits(
    tmp_path: Path,
) -> None:
    home, _, local_bridge, _, _, pool_repo = _prepared_home(tmp_path)
    active_repo = local_bridge.parent / "skills-repo"
    active_repo.mkdir()

    def fail_once(_generation: str, _preparation_id: str):
        raise ActiveRepoRetirementError("active_repo_unmount_failed")

    first = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        retire_active_repo=fail_once,
    )
    assert first.status is PoolActivationStatus.POST_CUTOVER_SYNC_PENDING

    calls = 0

    def retire_on_retry(_generation: str, _preparation_id: str):
        nonlocal calls
        calls += 1
        active_repo.rmdir()
        return {"status": "retired"}

    retry = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        retire_active_repo=retire_on_retry,
    )

    assert retry.status is PoolActivationStatus.ALREADY_COMMITTED
    assert calls == 1
    marker = json.loads(
        (home / ".aicoding" / "workspace" / "skills-pool" / ".pool-active").read_text()
    )
    assert marker["activation_state"] == "active"


def test_aicoding_retirement_revalidates_stable_repo_before_commit(
    tmp_path: Path,
) -> None:
    home, _, local_bridge, repo_bridge, _, pool_repo = _prepared_home(tmp_path)
    active_repo = local_bridge.parent / "skills-repo"
    active_repo.mkdir()

    def retire_and_break_bridge(_generation: str, _preparation_id: str):
        active_repo.rmdir()
        repo_bridge.unlink()
        return {"status": "retired"}

    result = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        retire_active_repo=retire_and_break_bridge,
    )

    assert result.status is PoolActivationStatus.POST_CUTOVER_SYNC_PENDING
    assert result.evidence["reason"] == "stable_repo_bridge_invalid"
    marker = json.loads(
        (home / ".aicoding" / "workspace" / "skills-pool" / ".pool-active").read_text()
    )
    assert marker["activation_state"] == "finalizing"


def test_aicoding_retirement_revalidates_active_mappings_before_commit(
    tmp_path: Path,
) -> None:
    home, legacy_local, local_bridge, _, pool_local, pool_repo = _prepared_home(
        tmp_path
    )
    active_repo = local_bridge.parent / "skills-repo"
    active_repo.mkdir()
    target = local_bridge.parent / "handmade"

    def retire_and_repoint_mapping(_generation: str, _preparation_id: str):
        active_repo.rmdir()
        target.unlink()
        target.symlink_to(legacy_local / "handmade", target_is_directory=True)
        return {"status": "retired"}

    result = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "handmade"),
                target=str(target),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        retire_active_repo=retire_and_repoint_mapping,
    )

    assert result.status is PoolActivationStatus.POST_CUTOVER_SYNC_PENDING
    assert result.evidence["reason"] == "post_retirement_mapping_verify_failed"
    marker = json.loads(
        (home / ".aicoding" / "workspace" / "skills-pool" / ".pool-active").read_text()
    )
    assert marker["activation_state"] == "finalizing"


def test_aicoding_active_probe_rejects_full_corpus_in_active_root(
    tmp_path: Path,
) -> None:
    home, _, local_bridge, _, _, pool_repo = _prepared_home(tmp_path)
    activated = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert activated.committed
    (local_bridge.parent / "skills-repo").mkdir()

    inspection = inspect_aicoding_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert inspection.status is RuntimeLayoutInspectionStatus.INVALID
    assert inspection.evidence["reason"] == "active_repo_corpus_present"


def test_aicoding_rollback_rebuilds_legacy_from_current_pool(
    tmp_path: Path,
) -> None:
    home, legacy_local, _, _, pool_local, pool_repo = _prepared_home(tmp_path)
    activated = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert activated.committed
    (pool_local / "handmade" / "SKILL.md").write_text("after-activation")
    (pool_local / "new-local").mkdir()
    (pool_local / "new-local" / "SKILL.md").write_text("new")
    active_repo = home / ".claude" / "skills" / "skills-repo"
    restore_calls: list[tuple[str, str]] = []

    def restore_active_repo(generation: str, preparation_id: str):
        restore_calls.append((generation, preparation_id))
        active_repo.mkdir()
        return {"restored": True}

    rolled_back = rollback_aicoding_pool(
        rollback_generation="rollback-1",
        registered_local_names=["handmade"],
        home=home,
        restore_active_repo=restore_active_repo,
    )

    assert rolled_back.status is PoolActivationStatus.COMMITTED
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()
    assert (legacy_local / "handmade" / "SKILL.md").read_text() == (
        "after-activation"
    )
    assert (legacy_local / "new-local" / "SKILL.md").read_text() == "new"
    assert (pool_local / "handmade" / "SKILL.md").read_text() == (
        "after-activation"
    )
    assert restore_calls == [("generation-1", PREPARATION_ID)]
    assert active_repo.is_dir()


def test_aicoding_rollback_restoration_io_failure_stays_pending(
    tmp_path: Path,
) -> None:
    home, _, _, _, _, pool_repo = _prepared_home(tmp_path)
    activated = activate_aicoding_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert activated.committed

    def restore_active_repo(_generation: str, _preparation_id: str):
        raise OSError(5, "mount failed")

    rolled_back = rollback_aicoding_pool(
        rollback_generation="rollback-1",
        registered_local_names=["handmade"],
        home=home,
        restore_active_repo=restore_active_repo,
    )

    assert rolled_back.status is PoolActivationStatus.POST_CUTOVER_SYNC_PENDING
    assert rolled_back.evidence["reason"] == "active_repo_restoration_failed"
    assert rolled_back.evidence["restoration_reason"] == (
        "active_repo_restoration_io_error"
    )
    assert (
        home / ".aicoding" / "workspace" / "skills-pool" / ".pool-active"
    ).is_file()


def test_aicoding_publishes_and_verifies_only_its_pool_sources(
    tmp_path: Path,
) -> None:
    home, _, local_bridge, _, pool_local, _ = _prepared_home(tmp_path)
    target = local_bridge.parent / "handmade"
    mapping = SkillMapping(
        source=str(pool_local / "handmade"),
        target=str(target),
    )

    published = publish_aicoding_pool_mappings(
        mappings=[mapping],
        home=home,
    )
    verified = verify_aicoding_pool_mappings(
        mappings=[mapping],
        home=home,
    )

    assert published.published is True
    assert target.resolve() == (pool_local / "handmade").resolve()
    assert verified.valid is True

    wrong_source = (
        home
        / ".openclaw"
        / "workspace"
        / "skills-pool"
        / "skills-local"
        / "handmade"
    )
    wrong_source.mkdir(parents=True)
    rejected = publish_aicoding_pool_mappings(
        mappings=[
            SkillMapping(
                source=str(wrong_source),
                target=str(target),
            )
        ],
        home=home,
    )

    assert rejected.published is False
    assert rejected.evidence["failures"][0]["reason"] == "source_outside_pool"
