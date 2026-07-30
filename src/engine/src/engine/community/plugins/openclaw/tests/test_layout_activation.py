from __future__ import annotations

import ctypes
import errno
import json
import os
import shutil
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from engine.community.core.adapters.openclaw.skills import OpenClawSkillsAdapter
from engine.community.core.skills.exceptions import (
    InvalidPoolMappingRequestError,
)
from engine.community.core.skills.layout_planner import (
    MAPPING_CONTRACT_VERSION,
)
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
    mirror_local_tree,
    write_baseline_manifest,
)
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from engine.community.plugins.skills_pool import layout_atomic
from engine.community.plugins.skills_pool.layout_activation import (
    mapping_sources_use_pool,
)
from engine.community.plugins.skills_pool.mapping_contract import (
    resolve_mapping_payload,
)

PREPARATION_ID = "2a958f59-8cf4-4413-a267-7d56d3382f23"


def test_mapping_source_classifier_selects_one_managed_layout(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    pool_source = (
        home / ".openclaw" / "workspace" / "skills-pool" / "skills-local" / "pool-skill"
    )
    legacy_source = (
        home / ".openclaw" / "workspace" / "skills" / "skills-local" / "legacy-skill"
    )
    external_source = home / "external-skills" / "external"

    assert mapping_sources_use_pool(
        engine="openclaw",
        sources=[pool_source, external_source],
        home=home,
    )
    assert not mapping_sources_use_pool(
        engine="openclaw",
        sources=[legacy_source, external_source],
        home=home,
    )
    with pytest.raises(ValueError, match="mix Legacy and Pool"):
        mapping_sources_use_pool(
            engine="openclaw",
            sources=[legacy_source, pool_source],
            home=home,
        )


def test_external_only_mapping_fails_closed_on_invalid_active_marker(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    active_marker = home / ".openclaw" / "workspace" / "skills-pool" / ".pool-active"
    active_marker.parent.mkdir(parents=True)
    active_marker.write_text("{invalid")

    with pytest.raises(ValueError, match="marker"):
        mapping_sources_use_pool(
            engine="openclaw",
            sources=[home / "external-skills" / "external"],
            home=home,
        )


def test_external_only_mapping_uses_active_marker_as_layout_authority(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    external_source = home / "external-skills" / "external"
    assert not mapping_sources_use_pool(
        engine="openclaw",
        sources=[external_source],
        home=home,
    )

    active_marker = home / ".openclaw" / "workspace" / "skills-pool" / ".pool-active"
    active_marker.parent.mkdir(parents=True)
    active_marker.write_text(
        json.dumps(
            {
                "engine": "openclaw",
                "layout_contract_version": LAYOUT_CONTRACT_VERSION,
                "preparation_id": PREPARATION_ID,
                "migration_generation": "generation-1",
                "activation_state": "active",
            }
        )
    )

    assert mapping_sources_use_pool(
        engine="openclaw",
        sources=[external_source],
        home=home,
    )


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


def test_registered_local_cutover_syncs_latest_content_and_retires_bridges(
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
    assert result.evidence["resolved_layout"] == {
        "active_root": str(home / ".openclaw/workspace/skills"),
        "local_root": str(pool_local),
        "repo_root": str(pool_repo),
    }
    assert result.evidence["local_locators"] == {
        "handmade": f"local://{pool_local / 'handmade'}"
    }
    assert not legacy_local.exists()
    assert not legacy_local.is_symlink()
    assert (pool_local / "handmade" / "SKILL.md").read_text() == "latest"
    assert not (pool_local / "handmade" / "stale.txt").exists()
    quarantine = (
        pool_local.parent / ".migration-quarantine" / "generation-1" / "skills-local"
    )
    assert (quarantine / "handmade" / "SKILL.md").read_text() == "latest"

    external_source = tmp_path / "external"
    external_source.mkdir()
    external_entry = legacy_local.parent / "external"
    external_entry.symlink_to(external_source, target_is_directory=True)
    unclassified_entry = legacy_local.parent / "unclassified"
    unclassified_entry.symlink_to(pool_local / "handmade", target_is_directory=True)
    published = publish_pool_mappings(mappings=mappings, home=home)
    assert published.published
    assert external_entry.is_symlink()
    assert unclassified_entry.is_symlink()
    assert not legacy_local.exists()
    assert not legacy_local.is_symlink()
    assert not (legacy_local.parent / "skills-repo").exists()
    assert not (legacy_local.parent / "skills-repo").is_symlink()
    active_marker = json.loads((pool_local.parent / ".pool-active").read_text())
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


def test_invalid_registered_name_is_rejected_before_cutover(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    invalid_name = " handmade"
    (legacy_local / "handmade").rename(legacy_local / invalid_name)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[invalid_name],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.INVALID
    assert result.evidence["reason"] == "registered_local_name_invalid"
    assert (legacy_local / invalid_name / "SKILL.md").read_text() == "latest"
    assert not legacy_local.is_symlink()
    assert (legacy_local.parent / "skills-repo").is_symlink()
    assert not (pool_local.parent / ".pool-active").exists()


def test_invalid_registered_name_is_rejected_before_rollback(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    activated = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert activated.committed
    invalid_name = " handmade"
    (pool_local / "handmade").rename(pool_local / invalid_name)

    result = rollback_openclaw_pool(
        rollback_generation="rollback-1",
        registered_local_names=[invalid_name],
        home=home,
    )

    assert result.status is PoolActivationStatus.INVALID
    assert result.evidence["reason"] == "registered_local_name_invalid"
    assert (pool_local / invalid_name / "SKILL.md").read_text() == "latest"
    assert not legacy_local.exists()
    assert (pool_local.parent / ".pool-active").exists()


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
    assert (legacy_local / "handmade" / "SKILL.md").read_text() == ("pool-new-write")
    assert (legacy_local / "created-after-activation" / "SKILL.md").read_text() == "new"
    assert (pool_local / "handmade" / "SKILL.md").read_text() == ("pool-new-write")
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
    assert (pool_local / "agent-created" / "SKILL.md").read_text() == "filesystem-only"
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


def test_post_rename_sync_captures_write_after_initial_copy(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def write_before_retire() -> None:
        (legacy_local / "handmade" / "created-during-cutover.txt").write_text(
            "must-survive"
        )

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        before_legacy_retire=write_before_retire,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (
        pool_local / "handmade" / "created-during-cutover.txt"
    ).read_text() == "must-survive"


def test_initial_mirror_preserves_pool_write_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.openclaw import layout_sync

    home, _legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    real_copy2 = layout_sync.shutil.copy2
    wrote_pool = False

    def copy_and_write_pool(
        source: Path,
        destination: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        nonlocal wrote_pool
        if not wrote_pool and ".final-sync-generation-1" in str(destination):
            wrote_pool = True
            (pool_local / "handmade" / "SKILL.md").write_text(
                "backend-write-during-sync"
            )
        return real_copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(layout_sync.shutil, "copy2", copy_and_write_pool)
    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert wrote_pool is True
    assert (pool_local / "handmade" / "SKILL.md").read_text() == (
        "backend-write-during-sync"
    )


def test_staging_baseline_detects_legacy_write_before_retire(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.openclaw import layout_activation

    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    real_write_baseline = layout_activation.write_baseline_manifest
    wrote_legacy = False

    def write_baseline_after_legacy_change(
        **kwargs: object,
    ) -> dict[str, tuple[str, str]]:
        nonlocal wrote_legacy
        wrote_legacy = True
        (legacy_local / "handmade" / "SKILL.md").write_text(
            "legacy-write-after-staging"
        )
        return real_write_baseline(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        layout_activation,
        "write_baseline_manifest",
        write_baseline_after_legacy_change,
    )
    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert wrote_legacy is True
    assert result.status is PoolActivationStatus.COMMITTED
    assert (pool_local / "handmade" / "SKILL.md").read_text() == (
        "legacy-write-after-staging"
    )


def test_post_rename_sync_captures_new_unregistered_skill_root(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def create_skill_before_retire() -> None:
        late_skill = legacy_local / "created-during-cutover"
        late_skill.mkdir()
        (late_skill / "SKILL.md").write_text("must-survive")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        before_legacy_retire=create_skill_before_retire,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (
        pool_local / "created-during-cutover" / "SKILL.md"
    ).read_text() == "must-survive"
    assert result.evidence["post_sync"]["applied"] == [
        "created-during-cutover",
        "created-during-cutover/SKILL.md",
    ]


def test_post_rename_sync_captures_existing_file_update_before_retire(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def update_before_retire() -> None:
        (legacy_local / "handmade" / "SKILL.md").write_text("updated-before-retire")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        before_legacy_retire=update_before_retire,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (pool_local / "handmade" / "SKILL.md").read_text() == (
        "updated-before-retire"
    )
    assert result.evidence["post_sync"]["applied"] == ["handmade/SKILL.md"]


def test_post_rename_sync_preserves_new_pool_write_on_same_file(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def update_legacy_before_retire() -> None:
        (legacy_local / "handmade" / "SKILL.md").write_text("pre-retire")

    def update_pool_after_exchange() -> None:
        (pool_local / "handmade" / "SKILL.md").write_text("post-retire")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        before_legacy_retire=update_legacy_before_retire,
        before_post_sync=update_pool_after_exchange,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (pool_local / "handmade" / "SKILL.md").read_text() == "post-retire"
    assert result.evidence["post_sync"]["conflicts_preserved_in_pool"] == [
        "handmade/SKILL.md"
    ]


def test_post_rename_sync_publishes_update_with_plain_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.openclaw import layout_sync

    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def update_before_retire() -> None:
        (legacy_local / "handmade" / "SKILL.md").write_text("legacy-window")

    real_replace = layout_sync.os.replace
    pool_replace_count = 0

    def track_replace(left: Path, right: Path) -> None:
        nonlocal pool_replace_count
        if Path(right) == pool_local / "handmade" / "SKILL.md":
            pool_replace_count += 1
        real_replace(left, right)

    monkeypatch.setattr(
        layout_sync.os,
        "replace",
        track_replace,
    )
    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        before_legacy_retire=update_before_retire,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert pool_replace_count >= 1
    assert (pool_local / "handmade" / "SKILL.md").read_text() == "legacy-window"


def test_post_rename_new_file_uses_no_clobber_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from engine.community.plugins.openclaw import layout_sync

    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def add_legacy_file_before_retire() -> None:
        (legacy_local / "handmade" / "raced.txt").write_text("legacy-window")

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
        before_legacy_retire=add_legacy_file_before_retire,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert (pool_local / "handmade" / "raced.txt").read_text() == (
        "pool-after-exchange"
    )
    assert result.evidence["post_sync"]["conflicts_preserved_in_pool"] == [
        "handmade/raced.txt"
    ]


def test_post_rename_pool_parent_deletion_is_not_resurrected(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def add_child_before_retire() -> None:
        (legacy_local / "handmade" / "late-child.txt").write_text("legacy-window")

    def delete_pool_parent() -> None:
        shutil.rmtree(pool_local / "handmade")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        before_legacy_retire=add_child_before_retire,
        before_post_sync=delete_pool_parent,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert not (pool_local / "handmade").exists()
    assert result.evidence["post_sync"]["conflicts_preserved_in_pool"] == [
        "handmade/late-child.txt"
    ]


def test_post_rename_deleted_parent_does_not_stall_new_subdirectory(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def add_subtree_before_retire() -> None:
        new_dir = legacy_local / "handmade" / "late-directory"
        new_dir.mkdir()
        (new_dir / "child.txt").write_text("legacy-window")

    def delete_pool_parent() -> None:
        shutil.rmtree(pool_local / "handmade")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        before_legacy_retire=add_subtree_before_retire,
        before_post_sync=delete_pool_parent,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert not (pool_local / "handmade").exists()
    assert set(result.evidence["post_sync"]["conflicts_preserved_in_pool"]) == {
        "handmade/late-directory",
        "handmade/late-directory/child.txt",
    }


def test_retry_after_exchange_finishes_quarantine_move(tmp_path: Path) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    write_baseline_manifest(
        pool_local=pool_local,
        local_names=["handmade"],
        manifest_path=pool_local.parent / ".cutover-baseline-generation-1.json",
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
        pool_local.parent / ".migration-quarantine" / "generation-1" / "skills-local"
    )
    assert result.status is PoolActivationStatus.ALREADY_COMMITTED
    assert quarantine.is_dir()
    assert not temporary.exists()


def test_retry_after_legacy_rename_finishes_from_quarantine(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    mirror_local_tree(
        source_root=legacy_local,
        pool_local=pool_local,
        staging_root=pool_local.parent / ".final-sync-generation-1",
    )
    write_baseline_manifest(
        pool_local=pool_local,
        local_names=["handmade"],
        manifest_path=pool_local.parent / ".cutover-baseline-generation-1.json",
    )
    quarantine = (
        pool_local.parent / ".migration-quarantine" / "generation-1" / "skills-local"
    )
    quarantine.parent.mkdir(parents=True)
    legacy_local.rename(quarantine)

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
    )

    assert result.status is PoolActivationStatus.ALREADY_COMMITTED
    assert quarantine.is_dir()
    assert not legacy_local.exists()
    assert (legacy_local.parent / "handmade").resolve() == (pool_local / "handmade")


def test_finalizing_marker_retry_collects_recreated_legacy_local(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    mirror_local_tree(
        source_root=legacy_local,
        pool_local=pool_local,
        staging_root=pool_local.parent / ".final-sync-generation-1",
    )
    write_baseline_manifest(
        pool_local=pool_local,
        local_names=["handmade"],
        manifest_path=pool_local.parent / ".cutover-baseline-generation-1.json",
    )
    quarantine = (
        pool_local.parent / ".migration-quarantine" / "generation-1" / "skills-local"
    )
    quarantine.parent.mkdir(parents=True)
    legacy_local.rename(quarantine)
    (pool_local.parent / ".pool-active").write_text(
        json.dumps(
            {
                "engine": "openclaw",
                "layout_contract_version": LAYOUT_CONTRACT_VERSION,
                "preparation_id": PREPARATION_ID,
                "migration_generation": "generation-1",
                "activation_state": "finalizing",
                "mappings": [],
            }
        )
    )
    late_skill = legacy_local / "created-after-finalizing"
    late_skill.mkdir(parents=True)
    (late_skill / "SKILL.md").write_text("must-survive")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.ALREADY_COMMITTED
    assert not legacy_local.exists()
    assert (
        pool_local / "created-after-finalizing" / "SKILL.md"
    ).read_text() == "must-survive"
    marker = json.loads((pool_local.parent / ".pool-active").read_text())
    assert marker["activation_state"] == "active"


def test_cutover_collects_recreated_legacy_local_before_commit(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def recreate_legacy_local() -> None:
        late_skill = legacy_local / "created-after-retire"
        late_skill.mkdir(parents=True)
        (late_skill / "SKILL.md").write_text("must-survive")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        before_post_sync=recreate_legacy_local,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert not legacy_local.exists()
    assert (
        pool_local / "created-after-retire" / "SKILL.md"
    ).read_text() == "must-survive"
    assert result.evidence["legacy_residue"]["captured_count"] == 1


def test_cutover_retry_replays_residue_after_merge_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.openclaw import layout_activation

    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    real_merge = layout_activation.merge_post_cutover_changes
    fail_residue_once = True

    def recreate_legacy_local() -> None:
        late_skill = legacy_local / "created-before-merge-failure"
        late_skill.mkdir(parents=True)
        (late_skill / "SKILL.md").write_text("must-survive")

    def flaky_merge(**kwargs: object) -> dict[str, object]:
        nonlocal fail_residue_once
        source_root = Path(str(kwargs["source_root"]))
        if fail_residue_once and source_root.name.startswith("skills-local-residue-"):
            fail_residue_once = False
            raise OSError(5, "injected merge failure")
        return real_merge(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        layout_activation,
        "merge_post_cutover_changes",
        flaky_merge,
    )
    first = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        before_post_sync=recreate_legacy_local,
    )

    assert first.status is PoolActivationStatus.TRANSIENT_ERROR
    assert (pool_local.parent / ".cutover-baseline-generation-1.json").is_file()

    retry = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert retry.status is PoolActivationStatus.ALREADY_COMMITTED
    assert retry.evidence["quarantine"] == str(
        pool_local.parent / ".migration-quarantine" / "generation-1" / "skills-local"
    )
    assert retry.evidence["quarantine_cleanup_pending"] is True
    assert not legacy_local.exists()
    assert (
        pool_local / "created-before-merge-failure" / "SKILL.md"
    ).read_text() == "must-survive"


def test_cutover_retry_continues_after_repeated_legacy_recreation(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    recreate_count = 0

    def recreate_legacy_local() -> None:
        late_skill = legacy_local / f"created-{recreate_count}"
        late_skill.mkdir(parents=True)
        (late_skill / "SKILL.md").write_text("must-survive")

    def retire_and_recreate(source: Path, target: Path) -> None:
        nonlocal recreate_count
        os.replace(source, target)
        if target.name.startswith("skills-local-residue-"):
            recreate_count += 1
            recreate_legacy_local()

    first = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        retire_path=retire_and_recreate,
        before_post_sync=recreate_legacy_local,
    )

    assert first.status is PoolActivationStatus.POST_CUTOVER_SYNC_PENDING
    assert first.evidence["reason"] == "legacy_local_recreated_during_cutover"

    retry = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert retry.status is PoolActivationStatus.ALREADY_COMMITTED
    assert not legacy_local.exists()
    for index in range(4):
        assert (
            pool_local / f"created-{index}" / "SKILL.md"
        ).read_text() == "must-survive"


def test_cutover_commits_without_atomic_exchange_and_retires_storage_bridges(
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

    assert result.status is PoolActivationStatus.COMMITTED
    assert not legacy_local.exists()
    assert not legacy_local.is_symlink()
    assert not (legacy_local.parent / "skills-repo").exists()
    assert (legacy_local.parent / "handmade").resolve() == (pool_local / "handmade")


def test_atomic_exchange_treats_einval_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRenameAt2:
        argtypes: ClassVar[list[object]] = []
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

    assert result.status is PoolActivationStatus.COMMITTED
    assert result.evidence["recovered_temporary"] is True
    assert not legacy_local.exists()
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
        (
            "bad generation",
            PREPARATION_ID,
            ["handmade"],
            "migration_generation_invalid",
        ),
        ("generation-1", "stale-preparation", ["handmade"], "runtime_layout_not_ready"),
        (
            "generation-1",
            PREPARATION_ID,
            ["../escape"],
            "registered_local_name_invalid",
        ),
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
    (pool_local / "handmade").symlink_to(tmp_path / "old", target_is_directory=True)

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


def test_cutover_rejects_occupied_legacy_exchange_temporary(
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


def test_cutover_rejects_unowned_quarantine_generation(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    generation_dir = pool_local.parent / ".migration-quarantine" / "generation-1"
    generation_dir.mkdir(parents=True)
    unknown = generation_dir / "unknown"
    unknown.write_text("external")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.INVALID
    assert result.evidence["reason"] == ("cutover_quarantine_ownership_unproven")
    assert legacy_local.is_dir()
    assert unknown.read_text() == "external"
    assert not (pool_local.parent / ".pool-active").exists()


def test_cutover_recovers_truncated_quarantine_owner(
    tmp_path: Path,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    generation_dir = pool_local.parent / ".migration-quarantine" / "generation-1"
    generation_dir.mkdir(parents=True)
    (generation_dir / ".owner.json").write_text("{")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert not legacy_local.exists()
    owner = json.loads((generation_dir / ".owner.json").read_text())
    assert owner["migration_generation"] == "generation-1"
    assert list(generation_dir.glob(".owner.invalid-*"))


def test_cutover_accepts_same_owner_marker_publish_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.openclaw import layout_activation

    home, legacy_local, _pool_local, pool_repo = _prepared_home(tmp_path)
    real_link = layout_activation.os.link
    raced = False

    def race_owner_link(source: Path, target: Path) -> None:
        nonlocal raced
        if not raced and Path(target).name == ".owner.json":
            raced = True
            Path(target).write_text(
                json.dumps(
                    {
                        "engine": "openclaw",
                        "migration_generation": "generation-1",
                        "preparation_id": PREPARATION_ID,
                    }
                )
            )
            raise FileExistsError
        real_link(source, target)

    monkeypatch.setattr(layout_activation.os, "link", race_owner_link)
    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert raced is True
    assert result.status is PoolActivationStatus.COMMITTED
    assert not legacy_local.exists()


def test_cutover_reports_transient_filesystem_failure(tmp_path: Path) -> None:
    home, legacy_local, _pool_local, pool_repo = _prepared_home(tmp_path)
    temporary = legacy_local.parent / ".skills-local.pool-cutover-generation-1"

    def fail_retire(_left: Path, _right: Path) -> None:
        raise OSError(5, "io")

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
        retire_path=fail_retire,
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
    )
    assert retry.status is PoolActivationStatus.COMMITTED


def test_committed_cleanup_failure_keeps_quarantine_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.openclaw import layout_activation

    home, legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)

    def fail_after_compatibility_bridge(**_kwargs: object) -> None:
        legacy_local.symlink_to(pool_local, target_is_directory=True)
        raise OSError(5, "injected post-cutover cleanup failure")

    monkeypatch.setattr(
        layout_activation,
        "_finalize_active_root",
        fail_after_compatibility_bridge,
    )
    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    quarantine = (
        pool_local.parent / ".migration-quarantine" / "generation-1" / "skills-local"
    )
    assert result.status is PoolActivationStatus.COMMITTED
    assert result.evidence["reason"] == "post_cutover_cleanup_failed"
    assert result.evidence["quarantine"] == str(quarantine)
    assert result.evidence["quarantine_cleanup_pending"] is True


def test_already_committed_evidence_does_not_reprobe_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, _legacy_local, pool_local, pool_repo = _prepared_home(tmp_path)
    first = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )
    assert first.status is PoolActivationStatus.COMMITTED

    quarantine = (
        pool_local.parent / ".migration-quarantine" / "generation-1" / "skills-local"
    )
    real_exists = Path.exists

    def fail_quarantine_probe(path: Path) -> bool:
        if path == quarantine:
            raise OSError(5, "injected quarantine probe failure")
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", fail_quarantine_probe)
    replay = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert replay.status is PoolActivationStatus.ALREADY_COMMITTED
    assert replay.evidence["quarantine"] == str(quarantine)
    assert replay.evidence["quarantine_cleanup_pending"] is True


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
    assert {failure["reason"] for failure in result.evidence["failures"]} == {
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
    port.publish_pool_mappings = AsyncMock(return_value={"published": True, "total": 1})
    port.verify_pool_mappings = AsyncMock(return_value={"valid": True, "checked": 1})
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


@pytest.mark.asyncio
async def test_openclaw_port_resolves_logical_mapping_and_returns_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.openclaw import _skills

    received: dict[str, object] = {}

    def publish(**kwargs):
        received.update(kwargs)
        return MappingPublishResult(True, {"total": 1})

    monkeypatch.setattr(_skills, "publish_pool_mappings", publish)
    result = await OpenClawPluginImpl().publish_pool_mappings(
        {
            "mapping_contract_version": MAPPING_CONTRACT_VERSION,
            "mappings": [
                {
                    "corpus": "repo",
                    "relative_path": "business/reviewer",
                    "link_name": "reviewer",
                }
            ],
            "source_layout": "pool",
        }
    )

    assert received["mappings"] == [
        SkillMapping(
            source=(
                "/home/admin/.openclaw/workspace/skills-pool/"
                "skills-repo/business/reviewer"
            ),
            target="/home/admin/.openclaw/workspace/skills/reviewer",
        ),
    ]
    assert result["evidence"]["resolved_mappings"] == [
        {
            "corpus": "repo",
            "relative_path": "business/reviewer",
            "link_name": "reviewer",
            "resolved_locator": "git://business/reviewer",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mappings",
    [
        [
            {
                "corpus": "local",
                "relative_path": "writer\x00draft",
                "link_name": "writer",
            }
        ],
        [
            {
                "corpus": "local",
                "relative_path": "writer",
                "link_name": "writer",
            },
            {
                "corpus": "repo",
                "relative_path": "reviewer",
                "link_name": "reviewer\x00draft",
            },
        ],
    ],
)
async def test_openclaw_port_rejects_nul_before_publish(
    monkeypatch: pytest.MonkeyPatch,
    mappings: list[dict[str, str]],
) -> None:
    from engine.community.plugins.openclaw import _skills

    publish = MagicMock()
    monkeypatch.setattr(_skills, "publish_pool_mappings", publish)

    with pytest.raises(InvalidPoolMappingRequestError):
        await OpenClawPluginImpl().publish_pool_mappings(
            {
                "mapping_contract_version": MAPPING_CONTRACT_VERSION,
                "mappings": mappings,
                "source_layout": "pool",
            }
        )

    publish.assert_not_called()


def test_logical_dot_mapping_is_rejected_before_active_tree_mutation(
    tmp_path: Path,
) -> None:
    home, legacy_local, _pool_local, _pool_repo = _prepared_home(tmp_path)
    active_target = legacy_local.parent / "all-skills"

    with pytest.raises(InvalidPoolMappingRequestError):
        resolved = resolve_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.POOL,
            payload=[
                {
                    "corpus": "local",
                    "relative_path": ".",
                    "link_name": "all-skills",
                }
            ],
            mapping_contract_version=MAPPING_CONTRACT_VERSION,
            home=home,
        )
        publish_pool_mappings(
            mappings=list(resolved.mappings),
            home=home,
        )

    assert not active_target.exists()
    assert not active_target.is_symlink()
