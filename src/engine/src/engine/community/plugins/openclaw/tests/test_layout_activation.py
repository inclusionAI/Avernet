from __future__ import annotations

import ctypes
import errno
import json
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.community.core.adapters.openclaw.skills import OpenClawSkillsAdapter
from engine.community.core.skills.models import (
    PoolLayoutActivateRequest,
    PoolLayoutProbeRequest,
    SymlinkItem,
)
from engine.community.plugins.openclaw.layout_activation import (
    MappingPublishResult,
    MappingSourceLayout,
    MappingVerificationResult,
    PoolActivationResult,
    PoolActivationStatus,
    SkillMapping,
    activate_openclaw_pool,
    atomic_exchange_paths,
    publish_pool_mappings,
    rollback_openclaw_pool,
    verify_skill_mappings,
)
from engine.community.plugins.openclaw.layout_probe import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutInspection,
    RuntimeLayoutInspectionStatus,
)
from engine.community.plugins.openclaw.layout_sync import (
    write_baseline_manifest,
)
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from engine.community.plugins.skills_pool import layout_atomic

PREPARATION_ID = "2a958f59-8cf4-4413-a267-7d56d3382f23"


def _prepared_home(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home" / "admin"
    workspace = home / ".openclaw" / "workspace"
    legacy_root = workspace / "skills"
    legacy_local = legacy_root / "skills-local"
    pool_root = workspace / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"
    legacy_repo = legacy_root / "skills-repo"

    (legacy_local / "handmade").mkdir(parents=True)
    (legacy_local / "handmade" / "SKILL.md").write_text("latest")
    (pool_local / "handmade").mkdir(parents=True)
    (pool_local / "handmade" / "SKILL.md").write_text("prepared-old")
    (pool_local / "handmade" / "stale.txt").write_text("delete-me")
    (pool_repo / "business" / "repo-skill").mkdir(parents=True)
    (pool_repo / "business" / "repo-skill" / "SKILL.md").write_text("repo")
    legacy_repo.symlink_to(pool_repo, target_is_directory=True)

    marker = {
        "engine": "openclaw",
        "layout_contract_version": LAYOUT_CONTRACT_VERSION,
        "preparation_id": PREPARATION_ID,
        "prepared_at": "2026-07-23T06:00:00Z",
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
            "legacy_repo_bridge": {
                "path": str(legacy_repo),
                "target": str(pool_repo),
                "valid": True,
            },
            "managed_active_entries": [],
            "external_active_entry_count": 0,
        },
    }
    (pool_root / ".pool-ready").write_text(json.dumps(marker))
    return home, legacy_local, pool_local, pool_repo


def test_registered_local_cutover_syncs_latest_content_and_atomically_bridges(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    mappings = [
        SkillMapping(
            source=str(pool_local / "handmade"),
            target=str(legacy_local.parent / "handmade"),
        ),
        SkillMapping(
            source=str(pool_repo / "business" / "repo-skill"),
            target=str(legacy_local.parent / "repo-skill"),
        ),
    ]

    result = activate_openclaw_pool(
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
    assert (pool_local / "handmade" / "SKILL.md").read_text() == "latest"
    assert not (pool_local / "handmade" / "stale.txt").exists()
    quarantine = (
        pool_local.parent
        / ".migration-quarantine"
        / "generation-1"
        / "skills-local"
    )
    assert (quarantine / "handmade" / "SKILL.md").read_text() == "latest"

    external_source = tmp_path / "external"
    external_source.mkdir()
    external_entry = legacy_local.parent / "external"
    external_entry.symlink_to(external_source, target_is_directory=True)
    unclassified_entry = legacy_local.parent / "unclassified"
    unclassified_entry.symlink_to(
        pool_local / "handmade", target_is_directory=True
    )
    published = publish_pool_mappings(mappings=mappings, home=home)
    assert published.published
    assert external_entry.is_symlink()
    assert unclassified_entry.is_symlink()
    assert not legacy_local.exists()
    assert not legacy_local.is_symlink()
    assert not (legacy_local.parent / "skills-repo").exists()
    assert not (legacy_local.parent / "skills-repo").is_symlink()
    active_marker = json.loads(
        (pool_local.parent / ".pool-active").read_text()
    )
    assert active_marker["activation_state"] == "active"
    assert active_marker["mappings"] == []
    verification = verify_skill_mappings(mappings=mappings, home=home)
    assert verification.valid
    assert (legacy_local.parent / "handmade" / "SKILL.md").read_text() == "latest"
    assert (legacy_local.parent / "repo-skill" / "SKILL.md").read_text() == "repo"

    repeated = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=mappings,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert repeated.status is PoolActivationStatus.ALREADY_COMMITTED


def test_explicit_rollback_rebuilds_legacy_from_current_pool_content(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    mappings = [
        SkillMapping(
            source=str(pool_local / "handmade"),
            target=str(legacy_local.parent / "handmade"),
        )
    ]
    activated = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=mappings,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert activated.committed

    # These writes happened after Pool became authoritative and must survive.
    (pool_local / "handmade" / "SKILL.md").write_text("pool-new-write")
    (pool_local / "created-after-activation").mkdir()
    (pool_local / "created-after-activation" / "SKILL.md").write_text("new")
    external = legacy_local.parent / "external-unmanaged"
    external.symlink_to(tmp_path / "external")

    result = rollback_openclaw_pool(
        rollback_generation="rollback-1",
        registered_local_names=["handmade", "created-after-activation"],
        home=home,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()
    assert (legacy_local / "handmade" / "SKILL.md").read_text() == (
        "pool-new-write"
    )
    assert (
        legacy_local / "created-after-activation" / "SKILL.md"
    ).read_text() == "new"
    assert (pool_local / "handmade" / "SKILL.md").read_text() == (
        "pool-new-write"
    )
    assert external.is_symlink()

    repeated = rollback_openclaw_pool(
        rollback_generation="rollback-1",
        registered_local_names=["handmade", "created-after-activation"],
        home=home,
    )
    assert repeated.status is PoolActivationStatus.ALREADY_COMMITTED


def test_legacy_mapping_can_replace_pool_mapping_during_explicit_rollback(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    target = legacy_local.parent / "handmade"
    pool_mapping = SkillMapping(source=str(pool_local / "handmade"), target=str(target))
    activated = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[pool_mapping],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert activated.committed
    assert publish_pool_mappings(mappings=[pool_mapping], home=home).published
    rolled_back = rollback_openclaw_pool(
        rollback_generation="rollback-1",
        registered_local_names=["handmade"],
        home=home,
    )
    assert rolled_back.committed

    legacy_mapping = SkillMapping(
        source=str(legacy_local / "handmade"),
        target=str(target),
    )
    published = publish_pool_mappings(
        mappings=[legacy_mapping],
        source_layout=MappingSourceLayout.LEGACY,
        home=home,
    )
    verified = verify_skill_mappings(
        mappings=[legacy_mapping],
        source_layout=MappingSourceLayout.LEGACY,
        home=home,
    )

    assert published.published
    assert target.readlink() == legacy_local / "handmade"
    assert verified.valid


def test_cutover_rejects_missing_pool_mapping_source_before_bridge(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "missing"),
                target=str(legacy_local.parent / "missing"),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.INVALID
    assert result.evidence["reason"] == "mapping_source_invalid"
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()


def test_registered_local_missing_is_structured_data_inconsistency(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    prepared_content = (pool_local / "handmade" / "SKILL.md").read_text()

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["missing"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.DATA_INCONSISTENT
    assert result.evidence == {
        "reason": "registered_local_source_missing",
        "registered_name": "missing",
        "source": str(legacy_local / "missing"),
    }
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()
    assert (pool_local / "handmade" / "SKILL.md").read_text() == prepared_content


def test_registered_local_unreadable_is_structured_data_inconsistency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    skill_file = legacy_local / "handmade" / "SKILL.md"
    prepared_content = (pool_local / "handmade" / "SKILL.md").read_text()
    real_access = os.access

    def access_with_unreadable_skill(path: os.PathLike[str] | str, mode: int) -> bool:
        if Path(path) == skill_file and mode & os.R_OK:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(
        "engine.community.plugins.skills_pool.layout_activation.os.access",
        access_with_unreadable_skill,
    )

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.DATA_INCONSISTENT
    assert result.evidence["reason"] == "registered_local_source_unreadable"
    assert result.evidence["registered_name"] == "handmade"
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()
    assert (pool_local / "handmade" / "SKILL.md").read_text() == prepared_content


def test_final_sync_materializes_skill_created_after_preparation(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    late_legacy = legacy_local / "late-skill"
    late_legacy.mkdir()
    (late_legacy / "SKILL.md").write_text("late")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade", "late-skill"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "late-skill"),
                target=str(legacy_local.parent / "late-skill"),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.committed
    assert (pool_local / "late-skill" / "SKILL.md").read_text() == "late"


def test_unregistered_local_and_managed_entry_follow_filesystem_truth(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    unregistered = legacy_local / "agent-created"
    unregistered.mkdir()
    (unregistered / "SKILL.md").write_text("filesystem-only")
    active_entry = legacy_local.parent / "agent-created"
    active_entry.symlink_to(unregistered, target_is_directory=True)

    activated = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    published = publish_pool_mappings(mappings=[], home=home)
    verified = verify_skill_mappings(mappings=[], home=home)

    assert activated.status is PoolActivationStatus.COMMITTED
    assert (
        pool_local / "agent-created" / "SKILL.md"
    ).read_text() == "filesystem-only"
    assert activated.evidence["local_inventory"] == {
        "registered": 1,
        "unregistered": 1,
        "total": 2,
    }
    assert activated.evidence["active_inventory"] == {
        "managed": 1,
        "external": 0,
    }
    assert published.published
    assert active_entry.is_symlink()
    assert active_entry.readlink() == pool_local / "agent-created"
    assert verified.valid
    assert verified.evidence["managed_checked"] == 1


def test_external_active_entry_is_ignored_and_never_rewritten(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    external_source = tmp_path / "external-skill"
    external_source.mkdir()
    (external_source / "SKILL.md").write_text("external")
    external_entry = legacy_local.parent / "external-skill"
    external_entry.symlink_to(external_source, target_is_directory=True)
    requested = SkillMapping(
        source=str(pool_local / "handmade"),
        target=str(external_entry),
    )

    activated = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[requested],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    published = publish_pool_mappings(mappings=[requested], home=home)
    verified = verify_skill_mappings(mappings=[requested], home=home)

    assert activated.status is PoolActivationStatus.COMMITTED
    assert activated.evidence["active_inventory"] == {
        "managed": 0,
        "external": 1,
    }
    assert published.published
    assert published.evidence["external_ignored"] == [str(external_entry)]
    assert verified.valid
    assert verified.evidence["external_ignored"] == 1
    assert external_entry.readlink() == external_source


def test_competing_managed_sources_block_before_atomic_cutover(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    other = legacy_local / "other"
    other.mkdir()
    (other / "SKILL.md").write_text("other")
    active_entry = legacy_local.parent / "shared-name"
    active_entry.symlink_to(other, target_is_directory=True)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "handmade"),
                target=str(active_entry),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.ACTIVE_ENTRY_CONFLICT
    assert result.evidence == {
        "reason": "managed_active_entry_conflict",
        "conflicts": [
            {
                "target": str(active_entry),
                "requested_source": str(pool_local / "handmade"),
                "existing_source": str(pool_local / "other"),
            }
        ],
    }
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()
    assert active_entry.readlink() == other


def test_occupied_managed_target_blocks_before_atomic_cutover(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    occupied = legacy_local.parent / "handmade"
    occupied.mkdir()

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "handmade"),
                target=str(occupied),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.ACTIVE_ENTRY_CONFLICT
    assert result.evidence == {
        "reason": "managed_active_entry_conflict",
        "conflicts": [
            {
                "target": str(occupied),
                "requested_source": str(pool_local / "handmade"),
                "existing_source": "<occupied-non-symlink>",
            }
        ],
    }
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()
    assert occupied.is_dir()


def test_broken_managed_active_source_blocks_as_data_inconsistent(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    active_entry = legacy_local.parent / "missing-managed"
    active_entry.symlink_to(
        pool_local / "missing-managed",
        target_is_directory=True,
    )

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.DATA_INCONSISTENT
    assert result.evidence["reason"] == "managed_active_source_invalid"
    assert result.evidence["failures"] == [
        {
            "source": str(pool_local / "missing-managed"),
            "target": str(active_entry),
            "reason": "managed_source_missing",
        }
    ]
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()


@pytest.mark.parametrize("source_kind", ["traversal", "symlink_escape"])
def test_mapping_source_cannot_escape_canonical_pool_root(
    tmp_path: Path,
    source_kind: str,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    external = pool_local.parent.parent / "external"
    external.mkdir()
    if source_kind == "traversal":
        source = pool_local / ".." / ".." / "external"
        expected_reason = "source_outside_pool"
    else:
        source = pool_repo / "escape"
        source.symlink_to(external, target_is_directory=True)
        expected_reason = "source_escapes_pool"

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[
            SkillMapping(
                source=str(source),
                target=str(legacy_local.parent / "escape"),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.INVALID
    assert result.evidence["reason"] == "mapping_source_invalid"
    assert result.evidence["failures"][0]["reason"] == expected_reason
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()


def test_post_exchange_sync_captures_write_after_initial_copy(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def write_then_exchange(left: Path, right: Path) -> bool:
        (left / "handmade" / "created-during-cutover.txt").write_text(
            "must-survive"
        )
        return atomic_exchange_paths(left, right)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=write_then_exchange,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (
        pool_local / "handmade" / "created-during-cutover.txt"
    ).read_text() == "must-survive"


def test_post_exchange_sync_captures_new_unregistered_skill_root(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def create_skill_then_exchange(left: Path, right: Path) -> bool:
        late_skill = left / "created-during-cutover"
        late_skill.mkdir()
        (late_skill / "SKILL.md").write_text("must-survive")
        return atomic_exchange_paths(left, right)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=create_skill_then_exchange,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (
        pool_local / "created-during-cutover" / "SKILL.md"
    ).read_text() == "must-survive"
    assert result.evidence["post_sync"]["applied"] == [
        "created-during-cutover",
        "created-during-cutover/SKILL.md",
    ]


def test_post_exchange_sync_captures_existing_file_update_before_exchange(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def update_then_exchange(left: Path, right: Path) -> bool:
        (left / "handmade" / "SKILL.md").write_text("updated-before-exchange")
        return atomic_exchange_paths(left, right)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=update_then_exchange,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (pool_local / "handmade" / "SKILL.md").read_text() == (
        "updated-before-exchange"
    )
    assert result.evidence["post_sync"]["applied"] == ["handmade/SKILL.md"]


def test_post_exchange_sync_preserves_new_pool_write_on_same_file(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def update_legacy_then_exchange(left: Path, right: Path) -> bool:
        (left / "handmade" / "SKILL.md").write_text("pre-exchange")
        return atomic_exchange_paths(left, right)

    def update_pool_after_exchange() -> None:
        (legacy_local / "handmade" / "SKILL.md").write_text("post-exchange")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=update_legacy_then_exchange,
        before_post_sync=update_pool_after_exchange,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (pool_local / "handmade" / "SKILL.md").read_text() == "post-exchange"
    assert result.evidence["post_sync"]["conflicts_preserved_in_pool"] == [
        "handmade/SKILL.md"
    ]


def test_atomic_file_exchange_preserves_write_at_replacement_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.openclaw import layout_sync

    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def update_then_exchange(left: Path, right: Path) -> bool:
        (left / "handmade" / "SKILL.md").write_text("legacy-window")
        return atomic_exchange_paths(left, right)

    real_exchange = layout_sync.atomic_exchange_paths
    exchange_count = 0

    def write_pool_at_exchange(left: Path, right: Path) -> bool:
        nonlocal exchange_count
        if exchange_count == 0:
            right.write_text("pool-at-exchange")
        exchange_count += 1
        return real_exchange(left, right)

    monkeypatch.setattr(
        layout_sync,
        "atomic_exchange_paths",
        write_pool_at_exchange,
    )
    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=update_then_exchange,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (pool_local / "handmade" / "SKILL.md").read_text() == (
        "pool-at-exchange"
    )
    assert result.evidence["post_sync"][
        "conflicts_preserved_in_pool"
    ] == ["handmade/SKILL.md"]


def test_post_exchange_new_file_uses_atomic_no_clobber(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from engine.community.plugins.openclaw import layout_sync

    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def add_legacy_file_then_exchange(left: Path, right: Path) -> bool:
        (left / "handmade" / "raced.txt").write_text("legacy-window")
        return atomic_exchange_paths(left, right)

    real_link = os.link

    def race_link(source: Path, target: Path) -> None:
        if Path(target).name == "raced.txt":
            Path(target).write_text("pool-after-exchange")
        real_link(source, target)

    monkeypatch.setattr(layout_sync.os, "link", race_link)
    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=add_legacy_file_then_exchange,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (pool_local / "handmade" / "raced.txt").read_text() == (
        "pool-after-exchange"
    )
    assert result.evidence["post_sync"]["conflicts_preserved_in_pool"] == [
        "handmade/raced.txt"
    ]


def test_post_exchange_pool_parent_deletion_is_not_resurrected(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def add_child_then_exchange(left: Path, right: Path) -> bool:
        (left / "handmade" / "late-child.txt").write_text("legacy-window")
        return atomic_exchange_paths(left, right)

    def delete_pool_parent() -> None:
        shutil.rmtree(legacy_local / "handmade")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=add_child_then_exchange,
        before_post_sync=delete_pool_parent,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert not (pool_local / "handmade").exists()
    assert result.evidence["post_sync"][
        "conflicts_preserved_in_pool"
    ] == ["handmade/late-child.txt"]


def test_post_exchange_deleted_parent_does_not_stall_new_subdirectory(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def add_subtree_then_exchange(left: Path, right: Path) -> bool:
        new_dir = left / "handmade" / "late-directory"
        new_dir.mkdir()
        (new_dir / "child.txt").write_text("legacy-window")
        return atomic_exchange_paths(left, right)

    def delete_pool_parent() -> None:
        shutil.rmtree(legacy_local / "handmade")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=add_subtree_then_exchange,
        before_post_sync=delete_pool_parent,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert not (pool_local / "handmade").exists()
    assert set(
        result.evidence["post_sync"]["conflicts_preserved_in_pool"]
    ) == {
        "handmade/late-directory",
        "handmade/late-directory/child.txt",
    }


def test_retry_after_exchange_finishes_quarantine_move(tmp_path: Path) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    write_baseline_manifest(
        pool_local=pool_local,
        local_names=["handmade"],
        manifest_path=pool_local.parent
        / ".cutover-baseline-generation-1.json",
    )
    temporary = legacy_local.parent / ".skills-local.pool-cutover-generation-1"
    legacy_local.rename(temporary)
    legacy_local.symlink_to(pool_local, target_is_directory=True)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    quarantine = (
        pool_local.parent
        / ".migration-quarantine"
        / "generation-1"
        / "skills-local"
    )
    assert result.status is PoolActivationStatus.ALREADY_COMMITTED
    assert quarantine.is_dir()
    assert not temporary.exists()


def test_cutover_stays_legacy_when_atomic_exchange_is_unavailable(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "handmade"),
                target=str(legacy_local.parent / "handmade"),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=lambda _left, _right: False,
    )

    assert result.status is PoolActivationStatus.NOT_ATOMIC
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()


def test_atomic_exchange_treats_einval_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRenameAt2:
        argtypes: list[object] = []
        restype: object | None = None

        def __call__(self, *_args: object) -> int:
            ctypes.set_errno(errno.EINVAL)
            return -1

    class FakeLibc:
        renameat2 = FakeRenameAt2()

    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.symlink_to(left, target_is_directory=True)
    monkeypatch.setattr(layout_atomic.sys, "platform", "linux")
    monkeypatch.setattr(
        layout_atomic.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: FakeLibc(),
    )

    assert layout_atomic.atomic_exchange_paths(left, right) is False


def test_cutover_retry_recovers_owned_temporary_symlink(tmp_path: Path) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    temporary = legacy_local.parent / ".skills-local.pool-cutover-generation-1"
    temporary.symlink_to(pool_local, target_is_directory=True)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=lambda _left, _right: False,
    )

    assert result.status is PoolActivationStatus.NOT_ATOMIC
    assert result.evidence["reason"] == "atomic_exchange_unavailable"
    assert result.evidence["recovered_temporary"] is True
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()
    assert not temporary.exists()
    assert not temporary.is_symlink()


def test_cutover_retry_rejects_noncanonical_temporary_symlink(
    tmp_path: Path,
) -> None:
    home, legacy_local, _pool_local, pool_repo = _prepared_home(tmp_path)
    temporary = legacy_local.parent / ".skills-local.pool-cutover-generation-1"
    external = tmp_path / "external"
    external.mkdir()
    temporary.symlink_to(external, target_is_directory=True)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.INVALID
    assert result.evidence["reason"] == "cutover_temporary_path_occupied"
    assert temporary.is_symlink()
    assert temporary.readlink() == external
    assert legacy_local.is_dir()


@pytest.mark.parametrize(
    ("generation", "preparation_id", "names", "reason"),
    [
        ("bad generation", PREPARATION_ID, ["handmade"], "migration_generation_invalid"),
        ("generation-1", "stale-preparation", ["handmade"], "runtime_layout_not_ready"),
        ("generation-1", PREPARATION_ID, ["../escape"], "registered_local_name_invalid"),
    ],
)
def test_cutover_rejects_invalid_inputs(
    tmp_path: Path,
    generation: str,
    preparation_id: str,
    names: list[str],
    reason: str,
) -> None:
    home, legacy_local, _pool_local, pool_repo = _prepared_home(tmp_path)

    result = activate_openclaw_pool(
        migration_generation=generation,
        preparation_id=preparation_id,
        registered_local_names=names,
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.INVALID
    assert result.evidence["reason"] == reason
    assert legacy_local.is_dir()


def test_cutover_rejects_non_directory_legacy_local(tmp_path: Path) -> None:
    home, legacy_local, _pool_local, pool_repo = _prepared_home(tmp_path)
    shutil.rmtree(legacy_local)
    legacy_local.write_text("occupied")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.evidence["reason"] == "legacy_local_not_directory"


def test_cutover_cleans_stale_staging_and_replaces_pool_symlink(
    tmp_path: Path,
) -> None:
    home, _legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    staging = pool_local.parent / ".final-sync-generation-1"
    staging.symlink_to(tmp_path / "unused", target_is_directory=True)
    shutil.rmtree(pool_local / "handmade")
    (pool_local / "handmade").symlink_to(
        tmp_path / "old", target_is_directory=True
    )

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade", "handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.committed
    assert (pool_local / "handmade" / "SKILL.md").read_text() == "latest"


def test_cutover_rejects_occupied_temporary_and_ambiguous_exchange(
    tmp_path: Path,
) -> None:
    home, legacy_local, _pool_local, pool_repo = _prepared_home(tmp_path)
    temporary = legacy_local.parent / ".skills-local.pool-cutover-generation-1"
    temporary.write_text("occupied")

    occupied = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert occupied.evidence["reason"] == "cutover_temporary_path_occupied"

    temporary.unlink()
    ambiguous = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=lambda _left, _right: True,
    )
    assert ambiguous.evidence["reason"] == "cutover_result_ambiguous"


def test_cutover_reports_transient_filesystem_failure(tmp_path: Path) -> None:
    home, legacy_local, _pool_local, pool_repo = _prepared_home(tmp_path)
    temporary = legacy_local.parent / ".skills-local.pool-cutover-generation-1"

    def fail_exchange(_left: Path, _right: Path) -> bool:
        raise OSError(5, "io")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=fail_exchange,
    )

    assert result.status is PoolActivationStatus.TRANSIENT_ERROR
    assert result.evidence["reason"] == "filesystem_operation_failed"
    assert legacy_local.is_dir()
    assert not temporary.exists()
    assert not temporary.is_symlink()

    retry = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        exchange_paths=lambda _left, _right: False,
    )
    assert retry.status is PoolActivationStatus.NOT_ATOMIC
    assert retry.evidence["reason"] == "atomic_exchange_unavailable"


def test_mapping_verifier_reports_each_drift_class(tmp_path: Path) -> None:
    home, legacy_local, pool_local, _pool_repo = _prepared_home(tmp_path)
    valid_source = pool_local / "handmade"
    mismatch_source = pool_local / "other"
    mismatch_source.mkdir()
    mismatch = legacy_local.parent / "mismatch"
    mismatch.symlink_to(valid_source, target_is_directory=True)
    duplicate = legacy_local.parent / "duplicate"
    duplicate.symlink_to(valid_source, target_is_directory=True)
    not_link = legacy_local.parent / "not-link"
    not_link.mkdir()

    result = verify_skill_mappings(
        home=home,
        mappings=[
            SkillMapping("relative", str(legacy_local.parent / "outside")),
            SkillMapping(
                str(pool_local / "missing"),
                str(legacy_local.parent / "missing"),
            ),
            SkillMapping(str(valid_source), str(tmp_path / "wrong-parent")),
            SkillMapping(str(valid_source), str(not_link)),
            SkillMapping(str(mismatch_source), str(mismatch)),
            SkillMapping(str(valid_source), str(duplicate)),
            SkillMapping(str(valid_source), str(duplicate)),
        ],
    )

    assert not result.valid
    assert {
        failure["reason"] for failure in result.evidence["failures"]
    } == {
        "source_outside_pool",
        "source_missing",
        "target_invalid",
        "target_not_symlink",
        "managed_source_conflict",
    }


def test_mapping_publisher_validates_updates_and_occupied_targets(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, _pool_repo = _prepared_home(tmp_path)
    source = pool_local / "handmade"
    target = legacy_local.parent / "handmade"

    invalid = publish_pool_mappings(
        home=home,
        mappings=[
            SkillMapping("relative", str(target)),
            SkillMapping(str(source), str(legacy_local)),
        ],
    )
    assert not invalid.published
    assert invalid.evidence["reason"] == "mapping_invalid"

    target.symlink_to(tmp_path / "old", target_is_directory=True)
    updated = publish_pool_mappings(
        home=home,
        mappings=[SkillMapping(str(source), str(target))],
    )
    assert updated.published
    assert updated.evidence["external_ignored"] == [str(target)]
    assert target.readlink() == tmp_path / "old"

    target.unlink()
    target.symlink_to(
        legacy_local / "handmade",
        target_is_directory=True,
    )
    updated = publish_pool_mappings(
        home=home,
        mappings=[SkillMapping(str(source), str(target))],
    )
    assert updated.published
    assert updated.evidence["updated"] == [str(target)]

    kept = publish_pool_mappings(
        home=home,
        mappings=[SkillMapping(str(source), str(target))],
    )
    assert kept.published
    assert kept.evidence["kept"] == [str(target)]

    target.unlink()
    target.mkdir()
    occupied = publish_pool_mappings(
        home=home,
        mappings=[SkillMapping(str(source), str(target))],
    )
    assert not occupied.published
    assert occupied.evidence["reason"] == "managed_active_entry_conflict"


@pytest.mark.asyncio
async def test_openclaw_service_api_translates_pool_port_contract() -> None:
    port = MagicMock()
    port.activate_pool_layout = AsyncMock(
        return_value={"committed": True, "status": "COMMITTED"}
    )
    port.probe_pool_layout = AsyncMock(
        return_value={
            "status": "READY",
            "engine": "openclaw",
            "layout_contract_version": LAYOUT_CONTRACT_VERSION,
            "preparation_id": PREPARATION_ID,
            "evidence": {},
        }
    )
    port.publish_pool_mappings = AsyncMock(
        return_value={"published": True, "total": 1}
    )
    port.verify_pool_mappings = AsyncMock(
        return_value={"valid": True, "checked": 1}
    )
    service = OpenClawSkillsAdapter(port)
    mapping = SymlinkItem(source="/pool/a", target="/skills/a")

    activated = await service.activate_pool_layout(
        PoolLayoutActivateRequest(
            migration_generation="generation-1",
            preparation_id=PREPARATION_ID,
            registered_local_names=["a"],
            mappings=[mapping],
        )
    )
    probed = await service.probe_pool_layout(
        PoolLayoutProbeRequest(
            engine="openclaw",
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
        )
    )
    published = await service.publish_pool_mappings([mapping])
    verified = await service.verify_pool_mappings([mapping])

    assert activated.committed
    assert probed.status.value == "READY"
    assert published.published
    assert verified.valid
    port.activate_pool_layout.assert_awaited_once_with(
        {
            "migration_generation": "generation-1",
            "preparation_id": PREPARATION_ID,
            "registered_local_names": ["a"],
            "mappings": [{"source": "/pool/a", "target": "/skills/a"}],
        }
    )
    port.probe_pool_layout.assert_awaited_once_with(
        {
            "engine": "openclaw",
            "layout_contract_version": LAYOUT_CONTRACT_VERSION,
        }
    )


@pytest.mark.asyncio
async def test_openclaw_port_runs_pool_filesystem_operations_off_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.openclaw import _skills

    monkeypatch.setattr(
        _skills,
        "activate_openclaw_pool",
        lambda **_kwargs: PoolActivationResult(
            PoolActivationStatus.COMMITTED, {"bridge": "valid"}
        ),
    )
    monkeypatch.setattr(
        _skills,
        "inspect_runtime_layout",
        lambda **_kwargs: RuntimeLayoutInspection(
            status=RuntimeLayoutInspectionStatus.READY,
            engine="openclaw",
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id=PREPARATION_ID,
            evidence={},
        ),
    )
    monkeypatch.setattr(
        _skills,
        "publish_pool_mappings",
        lambda **_kwargs: MappingPublishResult(True, {"total": 1}),
    )
    monkeypatch.setattr(
        _skills,
        "verify_skill_mappings",
        lambda **_kwargs: MappingVerificationResult(True, {"checked": 1}),
    )
    port = OpenClawPluginImpl()
    params = {
        "migration_generation": "generation-1",
        "preparation_id": PREPARATION_ID,
        "registered_local_names": ["a"],
        "mappings": [{"source": "/pool/a", "target": "/skills/a"}],
    }

    assert (await port.activate_pool_layout(params))["committed"] is True
    assert (
        await port.probe_pool_layout(
            {
                "engine": "openclaw",
                "layout_contract_version": LAYOUT_CONTRACT_VERSION,
            }
        )
    )["status"] == "READY"
    assert (await port.publish_pool_mappings(params))["published"] is True
    assert (await port.verify_pool_mappings(params))["valid"] is True
