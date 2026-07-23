from __future__ import annotations

import json
from pathlib import Path

from engine.community.plugins.claude_code.layout_pool import (
    PoolActivationStatus,
    RuntimeLayoutInspectionStatus,
    SkillMapping,
    activate_claude_code_pool,
    inspect_claude_code_runtime_layout,
)


PREPARATION_ID = "2a958f59-8cf4-4413-a267-7d56d3382f23"


def _prepared_home(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    home = tmp_path / "home" / "admin"
    legacy_local = home / ".claude_code" / "workspace" / "skills" / "skills-local"
    active_root = home / ".claude" / "skills"
    local_bridge = active_root / "skills-local"
    repo_bridge = active_root / "skills-repo"
    pool_root = home / ".claude_code" / "workspace" / "skills-pool"
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
        "engine": "claude_code",
        "layout_contract_version": "skills-pool-p3-v1",
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


def test_claude_code_probe_requires_both_stable_bridges(tmp_path: Path) -> None:
    home, _, _, repo_bridge, _, pool_repo = _prepared_home(tmp_path)

    ready = inspect_claude_code_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert ready.status is RuntimeLayoutInspectionStatus.READY
    assert ready.engine == "claude_code"
    assert ready.preparation_id == PREPARATION_ID
    assert ready.evidence["checks"]["stable_local_bridge_valid"] is True
    assert ready.evidence["checks"]["stable_repo_bridge_valid"] is True

    repo_bridge.unlink()
    repo_bridge.symlink_to(home / "wrong", target_is_directory=True)
    invalid = inspect_claude_code_runtime_layout(
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert invalid.status is RuntimeLayoutInspectionStatus.INVALID
    assert invalid.evidence["reason"] == "stable_repo_bridge_invalid"


def test_claude_code_activation_atomically_switches_physical_local(
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
            target=str(repo_bridge.parent / "shared"),
        ),
    ]

    result = activate_claude_code_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=["handmade"],
        mappings=mappings,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert legacy_local.is_symlink()
    assert legacy_local.resolve() == pool_local.resolve()
    assert local_bridge.readlink() == legacy_local
    assert local_bridge.resolve() == pool_local.resolve()
    assert repo_bridge.resolve() == pool_repo.resolve()
    assert (pool_local / "handmade" / "SKILL.md").read_text() == "latest"
