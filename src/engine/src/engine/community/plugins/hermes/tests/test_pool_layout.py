from __future__ import annotations

import json
from pathlib import Path

from engine.community.plugins.hermes.layout_pool import (
    PoolActivationStatus,
    RuntimeLayoutInspectionStatus,
    SkillMapping,
    activate_hermes_pool,
    inspect_hermes_runtime_layout,
    publish_hermes_pool_mappings,
    rollback_hermes_pool,
    verify_hermes_pool_mappings,
)


def test_probe_requires_verified_hermes_legacy_bridge(tmp_path: Path) -> None:
    home = tmp_path / "home" / "admin"
    active_root = home / ".hermes" / "skills"
    legacy_local = home / ".hermes" / "workspace" / "skills" / "skills-local"
    pool_root = home / ".hermes" / "workspace" / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"
    local_bridge = active_root / "skills-local"
    repo_bridge = home / ".hermes" / "skills-repo"

    legacy_local.mkdir(parents=True)
    pool_local.mkdir(parents=True)
    pool_repo.mkdir(parents=True)
    active_root.mkdir(parents=True)
    local_bridge.symlink_to(legacy_local, target_is_directory=True)
    repo_bridge.symlink_to(pool_repo, target_is_directory=True)
    (pool_root / ".pool-ready").write_text(
        json.dumps(
            {
                "engine": "hermes",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": "b7f7a125-9133-45fd-956d-fb66da81f68d",
                "prepared_at": "2026-07-24T00:00:00Z",
                "pool_local_root": str(pool_local),
                "pool_repo_root": str(pool_repo),
                "validation_summary": {
                    "all_valid": True,
                    "legacy_bridge_verified": True,
                    "legacy_bridge_repaired": False,
                    "pool_local": {"path": str(pool_local), "valid": True},
                    "pool_repo": {
                        "path": str(pool_repo),
                        "readable_mount": True,
                        "valid": True,
                    },
                    "managed_active_entries": [],
                    "external_active_entry_count": 0,
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
                },
            }
        )
    )

    result = inspect_hermes_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY
    assert result.engine == "hermes"
    assert result.evidence["checks"]["legacy_local_bridge_valid"] is True

    marker_path = pool_root / ".pool-ready"
    marker = json.loads(marker_path.read_text())
    del marker["validation_summary"]["legacy_bridge_verified"]
    marker_path.write_text(json.dumps(marker))

    missing_h0_evidence = inspect_hermes_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert missing_h0_evidence.status is RuntimeLayoutInspectionStatus.INVALID
    assert (
        missing_h0_evidence.evidence["reason"]
        == "marker_contract_mismatch"
    )


def test_activation_switches_hermes_local_bridge_to_pool(tmp_path: Path) -> None:
    home = tmp_path / "home" / "admin"
    active_root = home / ".hermes" / "skills"
    legacy_local = home / ".hermes" / "workspace" / "skills" / "skills-local"
    pool_root = home / ".hermes" / "workspace" / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"
    local_bridge = active_root / "skills-local"
    repo_bridge = home / ".hermes" / "skills-repo"

    (legacy_local / "handmade").mkdir(parents=True)
    (legacy_local / "handmade" / "SKILL.md").write_text("latest")
    (pool_local / "handmade").mkdir(parents=True)
    (pool_local / "handmade" / "SKILL.md").write_text("prepared")
    pool_repo.mkdir(parents=True)
    active_root.mkdir(parents=True)
    local_bridge.symlink_to(legacy_local, target_is_directory=True)
    repo_bridge.symlink_to(pool_repo, target_is_directory=True)
    (pool_root / ".pool-ready").write_text(
        json.dumps(
            {
                "engine": "hermes",
                "layout_contract_version": "skills-pool-p3-v1",
                "preparation_id": "b7f7a125-9133-45fd-956d-fb66da81f68d",
                "prepared_at": "2026-07-24T00:00:00Z",
                "pool_local_root": str(pool_local),
                "pool_repo_root": str(pool_repo),
                "validation_summary": {
                    "all_valid": True,
                    "legacy_bridge_verified": True,
                    "legacy_bridge_repaired": False,
                    "pool_local": {"path": str(pool_local), "valid": True},
                    "pool_repo": {
                        "path": str(pool_repo),
                        "readable_mount": True,
                        "valid": True,
                    },
                    "managed_active_entries": [],
                    "external_active_entry_count": 0,
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
                },
            }
        )
    )

    result = activate_hermes_pool(
        migration_generation="generation-1",
        preparation_id="b7f7a125-9133-45fd-956d-fb66da81f68d",
        registered_local_names=["handmade"],
        mappings=[
            SkillMapping(
                source=str(pool_local / "handmade"),
                target=str(active_root / "handmade"),
            )
        ],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert legacy_local.is_symlink()
    assert legacy_local.resolve() == pool_local.resolve()
    assert local_bridge.readlink() == legacy_local
    assert local_bridge.resolve() == pool_local.resolve()
    assert (pool_local / "handmade" / "SKILL.md").read_text() == "latest"

    mapping = SkillMapping(
        source=str(pool_local / "handmade"),
        target=str(active_root / "handmade"),
    )
    published = publish_hermes_pool_mappings(mappings=[mapping], home=home)
    verified = verify_hermes_pool_mappings(mappings=[mapping], home=home)

    assert published.published is True
    assert verified.valid is True
    assert (active_root / "handmade").resolve() == (pool_local / "handmade")

    (pool_local / "handmade" / "SKILL.md").write_text("pool-write")
    rolled_back = rollback_hermes_pool(
        rollback_generation="rollback-1",
        registered_local_names=["handmade"],
        home=home,
    )

    assert rolled_back.status is PoolActivationStatus.COMMITTED
    assert legacy_local.is_dir()
    assert not legacy_local.is_symlink()
    assert (legacy_local / "handmade" / "SKILL.md").read_text() == "pool-write"
    assert local_bridge.resolve() == legacy_local.resolve()


def test_mapping_rejects_openclaw_source_instead_of_falling_back(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    active_root = home / ".hermes" / "skills"
    openclaw_source = (
        home
        / ".openclaw"
        / "workspace"
        / "skills-pool"
        / "skills-local"
        / "wrong-engine"
    )
    openclaw_source.mkdir(parents=True)
    active_root.mkdir(parents=True)

    result = publish_hermes_pool_mappings(
        mappings=[
            SkillMapping(
                source=str(openclaw_source),
                target=str(active_root / "wrong-engine"),
            )
        ],
        home=home,
    )

    assert result.published is False
    assert result.evidence["reason"] == "mapping_invalid"
    assert result.evidence["failures"][0]["reason"] == "source_outside_pool"
    assert not (active_root / "wrong-engine").exists()


def test_mapping_cannot_replace_hermes_permanent_local_bridge(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    active_root = home / ".hermes" / "skills"
    legacy_local = home / ".hermes" / "workspace" / "skills" / "skills-local"
    pool_local = home / ".hermes" / "workspace" / "skills-pool" / "skills-local"
    source = pool_local / "skills-local"
    source.mkdir(parents=True)
    legacy_local.mkdir(parents=True)
    active_root.mkdir(parents=True)
    local_bridge = active_root / "skills-local"
    local_bridge.symlink_to(legacy_local, target_is_directory=True)

    published = publish_hermes_pool_mappings(
        mappings=[
            SkillMapping(source=str(source), target=str(local_bridge)),
        ],
        home=home,
    )
    verified = verify_hermes_pool_mappings(
        mappings=[
            SkillMapping(source=str(source), target=str(local_bridge)),
        ],
        home=home,
    )

    assert published.published is False
    assert published.evidence["failures"][0]["reason"] == "target_invalid"
    assert verified.valid is False
    assert verified.evidence["failures"][0]["reason"] == "target_invalid"
    assert local_bridge.readlink() == legacy_local
