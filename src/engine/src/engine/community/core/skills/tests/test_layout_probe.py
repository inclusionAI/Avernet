from __future__ import annotations

import errno
import json
from pathlib import Path

from engine.community.core.skills.layout_probe import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)


def _ready_home(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home" / "admin"
    legacy_root = home / ".openclaw" / "workspace" / "skills"
    pool_root = home / ".openclaw" / "workspace" / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_repo = pool_root / "skills-repo"
    legacy_repo = legacy_root / "skills-repo"
    pool_local.mkdir(parents=True)
    pool_repo.mkdir()
    legacy_root.mkdir(parents=True, exist_ok=True)
    legacy_repo.symlink_to(pool_repo, target_is_directory=True)
    marker = {
        "engine": "openclaw",
        "layout_contract_version": LAYOUT_CONTRACT_VERSION,
        "preparation_id": "2a958f59-8cf4-4413-a267-7d56d3382f23",
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
    return home, legacy_root, pool_local, pool_repo


def test_ready_requires_real_bridge_mount_and_readable_repo(tmp_path):
    home, _, _, pool_repo = _ready_home(tmp_path)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY
    assert result.preparation_id == "2a958f59-8cf4-4413-a267-7d56d3382f23"
    assert result.evidence["checks"]["pool_repo_mounted"] is True
    assert result.evidence["checks"]["legacy_repo_bridge_valid"] is True


def test_absent_marker_is_not_capable(tmp_path):
    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=tmp_path,
        repo_is_mounted=lambda _path: False,
    )

    assert result.status is RuntimeLayoutInspectionStatus.NOT_CAPABLE
    assert result.evidence["reason"] == "pool_ready_marker_absent"


def test_marker_nas_io_error_is_transient(tmp_path, monkeypatch):
    home, _, _, pool_repo = _ready_home(tmp_path)
    marker_path = home / ".openclaw" / "workspace" / "skills-pool" / ".pool-ready"
    original_read_bytes = Path.read_bytes

    def fail_marker_read(path):
        if path == marker_path:
            raise OSError(errno.ESTALE, "stale NAS handle")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_marker_read)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.TRANSIENT_ERROR
    assert result.evidence["reason"] == "marker_temporarily_unreadable"


def test_marker_stat_nas_io_error_is_transient(tmp_path, monkeypatch):
    home, _, _, pool_repo = _ready_home(tmp_path)
    marker_path = home / ".openclaw" / "workspace" / "skills-pool" / ".pool-ready"
    original_stat = Path.stat

    def fail_marker_stat(path, *args, **kwargs):
        if path == marker_path:
            raise OSError(errno.ESTALE, "stale NAS handle")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_marker_stat)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.TRANSIENT_ERROR
    assert result.evidence["reason"] == "marker_temporarily_unreadable"


def test_readable_directory_without_mount_is_invalid(tmp_path):
    home, _, _, _ = _ready_home(tmp_path)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda _path: False,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "pool_repo_not_mounted"


def test_repo_mount_io_error_is_transient(tmp_path):
    home, _, _, _ = _ready_home(tmp_path)

    def fail_mount_probe(_path):
        raise OSError(errno.EIO, "OSSFS temporarily unavailable")

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=fail_mount_probe,
    )

    assert result.status is RuntimeLayoutInspectionStatus.TRANSIENT_ERROR
    assert result.evidence["reason"] == "pool_repo_temporarily_unavailable"


def test_bridge_replaced_by_directory_is_invalid(tmp_path):
    home, legacy_root, _, pool_repo = _ready_home(tmp_path)
    bridge = legacy_root / "skills-repo"
    bridge.unlink()
    bridge.mkdir()

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "legacy_repo_bridge_invalid"


def test_managed_active_entry_drift_is_invalid(tmp_path):
    home, legacy_root, pool_local, pool_repo = _ready_home(tmp_path)
    pool_skill = pool_local / "handmade"
    pool_skill.mkdir()
    legacy_local = legacy_root / "skills-local"
    legacy_local.mkdir()
    legacy_skill = legacy_local / "handmade"
    legacy_skill.mkdir()
    active = legacy_root / "handmade"
    active.symlink_to(legacy_skill, target_is_directory=True)

    marker_path = home / ".openclaw" / "workspace" / "skills-pool" / ".pool-ready"
    marker = json.loads(marker_path.read_text())
    marker["validation_summary"]["managed_active_entries"] = [
        {
            "path": str(active),
            "source": "local",
            "legacy_target": str(legacy_skill),
            "pool_target": str(pool_skill),
            "valid": True,
        }
    ]
    marker_path.write_text(json.dumps(marker))
    active.unlink()
    active.symlink_to(legacy_local / "missing", target_is_directory=True)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "managed_active_entry_invalid"


def test_managed_active_entry_nas_io_error_is_transient(tmp_path, monkeypatch):
    home, legacy_root, _, pool_repo = _ready_home(tmp_path)
    legacy_local = legacy_root / "skills-local"
    legacy_skill = legacy_local / "handmade"
    legacy_skill.mkdir(parents=True)
    active = legacy_root / "handmade"
    active.symlink_to(legacy_skill, target_is_directory=True)
    original_lstat = Path.lstat

    def fail_active_lstat(path):
        if path == active:
            raise OSError(errno.ESTALE, "stale NAS handle")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_active_lstat)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.TRANSIENT_ERROR
    assert (
        result.evidence["reason"]
        == "managed_active_entries_temporarily_unavailable"
    )


def test_deactivating_prepared_managed_entry_does_not_invalidate_marker(tmp_path):
    home, legacy_root, pool_local, pool_repo = _ready_home(tmp_path)
    pool_skill = pool_local / "handmade"
    pool_skill.mkdir()
    legacy_local = legacy_root / "skills-local"
    legacy_local.mkdir()
    legacy_skill = legacy_local / "handmade"
    legacy_skill.mkdir()
    active = legacy_root / "handmade"
    active.symlink_to(legacy_skill, target_is_directory=True)
    marker_path = home / ".openclaw" / "workspace" / "skills-pool" / ".pool-ready"
    marker = json.loads(marker_path.read_text())
    marker["validation_summary"]["managed_active_entries"] = [
        {
            "path": str(active),
            "source": "local",
            "legacy_target": str(legacy_skill),
            "pool_target": str(pool_skill),
            "valid": True,
        }
    ]
    marker_path.write_text(json.dumps(marker))
    active.unlink()

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY


def test_new_managed_entry_can_wait_for_final_sync(tmp_path):
    home, legacy_root, _, pool_repo = _ready_home(tmp_path)
    legacy_local = legacy_root / "skills-local"
    legacy_local.mkdir()
    legacy_skill = legacy_local / "created-after-preparation"
    legacy_skill.mkdir()
    active = legacy_root / "created-after-preparation"
    active.symlink_to(legacy_skill, target_is_directory=True)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY


def test_pool_local_symlink_is_not_a_canonical_directory(tmp_path):
    home, _, pool_local, pool_repo = _ready_home(tmp_path)
    external = tmp_path / "external-local"
    external.mkdir()
    pool_local.rmdir()
    pool_local.symlink_to(external, target_is_directory=True)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "pool_local_invalid"


def test_teclaw_is_noop_without_touching_home(tmp_path):
    result = inspect_runtime_layout(
        engine="teclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=tmp_path / "missing",
        repo_is_mounted=lambda _path: (_ for _ in ()).throw(AssertionError()),
    )

    assert result.status is RuntimeLayoutInspectionStatus.NOT_CAPABLE
    assert result.evidence["reason"] == "engine_has_no_filesystem_pool_layout"
