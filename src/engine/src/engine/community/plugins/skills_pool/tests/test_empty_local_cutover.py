from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest
from engine.community.plugins.openclaw.layout_activation import (
    PoolActivationStatus,
    activate_openclaw_pool,
)
from engine.community.plugins.openclaw.layout_probe import LAYOUT_CONTRACT_VERSION
from engine.community.plugins.skills_pool import layout_sync

PREPARATION_ID = "2a958f59-8cf4-4413-a267-7d56d3382f23"


def _prepared_empty_home(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home" / "admin"
    workspace = home / ".openclaw" / "workspace"
    legacy_root = workspace / "skills"
    legacy_local = legacy_root / "skills-local"
    pool_root = workspace / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"
    legacy_repo = legacy_root / "skills-repo"

    legacy_local.mkdir(parents=True)
    pool_local.mkdir(parents=True)
    pool_repo.mkdir()
    legacy_repo.symlink_to(pool_repo, target_is_directory=True)
    (pool_root / ".pool-ready").write_text(
        json.dumps(
            {
                "engine": "openclaw",
                "layout_contract_version": LAYOUT_CONTRACT_VERSION,
                "preparation_id": PREPARATION_ID,
                "prepared_at": "2026-09-17T14:05:00Z",
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
        )
    )
    return home, legacy_local, pool_local, pool_repo


def test_empty_local_cutover_does_not_recreate_staging_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, legacy_local, pool_local, pool_repo = _prepared_empty_home(tmp_path)
    staging = pool_local.parent / ".final-sync-generation-1"
    staging.mkdir()
    real_remove_path = layout_sync._remove_path
    real_mkdir = Path.mkdir
    staging_removals = 0

    def reject_empty_post_sync_staging(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        if path.name.startswith(".post-sync-"):
            raise OSError(errno.EINVAL, "Invalid argument")
        real_mkdir(path, *args, **kwargs)

    def emulate_virtiofs_immediate_remove_failure(path: Path) -> None:
        nonlocal staging_removals
        if path == staging and path.is_dir():
            staging_removals += 1
            if staging_removals > 1:
                raise OSError(errno.EINVAL, "Invalid argument")
        real_remove_path(path)

    monkeypatch.setattr(
        layout_sync,
        "_remove_path",
        emulate_virtiofs_immediate_remove_failure,
    )
    monkeypatch.setattr(Path, "mkdir", reject_empty_post_sync_staging)

    result = activate_openclaw_pool(
        migration_generation="generation-1",
        preparation_id=PREPARATION_ID,
        registered_local_names=[],
        mappings=[],
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is PoolActivationStatus.COMMITTED
    assert staging_removals == 1
    assert not staging.exists()
    assert not legacy_local.exists()
    assert (pool_local.parent / ".pool-active").is_file()
