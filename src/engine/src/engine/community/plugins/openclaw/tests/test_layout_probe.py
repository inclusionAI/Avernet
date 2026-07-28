from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from engine.community.plugins.openclaw.layout_probe import (
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
    assert (
        result.evidence["mapping_contract_version"]
        == "skills-pool-mapping-v2"
    )
    assert result.evidence["resolved_layout"] == {
        "active_root": str(home / ".openclaw/workspace/skills"),
        "local_root": str(
            home / ".openclaw/workspace/skills-pool/skills-local"
        ),
        "repo_root": str(
            home / ".openclaw/workspace/skills-pool/skills-repo"
        ),
    }
    assert result.evidence["checks"]["pool_repo_mounted"] is True
    assert result.evidence["checks"]["legacy_repo_bridge_valid"] is True


def test_active_marker_requires_direct_pool_mappings_and_absent_storage_entries(
    tmp_path,
):
    home, active_root, pool_local, pool_repo = _ready_home(tmp_path)
    source = pool_local / "handmade"
    source.mkdir()
    target = active_root / "handmade"
    target.symlink_to(source, target_is_directory=True)
    (active_root / "skills-repo").unlink()
    pool_root = pool_local.parent
    (pool_root / ".pool-active").write_text(
        json.dumps(
            {
                "engine": "openclaw",
                "layout_contract_version": LAYOUT_CONTRACT_VERSION,
                "preparation_id": "2a958f59-8cf4-4413-a267-7d56d3382f23",
                "migration_generation": "generation-1",
                "activation_state": "active",
            }
        )
    )

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY
    assert result.evidence["activation_state"] == "active"
    assert result.evidence["mapping_contract_version"] == (
        "skills-pool-mapping-v2"
    )
    assert result.evidence["resolved_layout"]["local_root"] == str(pool_local)
    assert result.evidence["checks"]["legacy_storage_entries_absent"] is True


def test_active_marker_allows_normal_skill_deactivation(tmp_path):
    home, active_root, pool_local, pool_repo = _ready_home(tmp_path)
    source = pool_local / "handmade"
    source.mkdir()
    target = active_root / "handmade"
    target.symlink_to(source, target_is_directory=True)
    (active_root / "skills-repo").unlink()
    _write_active_marker(
        home,
        activation_state="active",
        mappings=[{"source": str(source), "target": str(target)}],
    )

    target.unlink()

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY
    assert result.evidence["mapping_contract_version"] == (
        "skills-pool-mapping-v2"
    )
    assert result.evidence["resolved_layout"]["local_root"] == str(pool_local)


def test_finalizing_marker_allows_concurrent_skill_deactivation(tmp_path):
    home, active_root, pool_local, pool_repo = _ready_home(tmp_path)
    source = pool_local / "handmade"
    source.mkdir()
    target = active_root / "handmade"
    target.symlink_to(source, target_is_directory=True)
    _write_active_marker(
        home,
        activation_state="finalizing",
        mappings=[{"source": str(source), "target": str(target)}],
    )

    target.unlink()

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY


def test_active_marker_allows_normal_skill_activation(tmp_path):
    home, active_root, pool_local, pool_repo = _ready_home(tmp_path)
    (active_root / "skills-repo").unlink()
    _write_active_marker(home, activation_state="active")
    source = pool_local / "new-skill"
    source.mkdir()
    (active_root / "new-skill").symlink_to(source, target_is_directory=True)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY


def _active_marker_path(home: Path) -> Path:
    return (
        home
        / ".openclaw"
        / "workspace"
        / "skills-pool"
        / ".pool-active"
    )


def _write_active_marker(
    home: Path,
    *,
    activation_state: str = "finalizing",
    mappings: object = None,
) -> Path:
    marker_path = _active_marker_path(home)
    marker = {
        "engine": "openclaw",
        "layout_contract_version": LAYOUT_CONTRACT_VERSION,
        "preparation_id": "2a958f59-8cf4-4413-a267-7d56d3382f23",
        "migration_generation": "generation-1",
        "activation_state": activation_state,
    }
    if activation_state == "finalizing" or mappings is not None:
        marker["mappings"] = [] if mappings is None else mappings
    marker_path.write_text(
        json.dumps(marker)
    )
    return marker_path


@pytest.mark.parametrize(
    "mutate",
    [
        lambda marker: "not-a-marker",
        lambda marker: {**marker, "engine": "claude_code"},
        lambda marker: {**marker, "mappings": "not-a-list"},
        lambda marker: {**marker, "mappings": ["not-a-mapping"]},
        lambda marker: {
            **marker,
            "mappings": [{"source": 1, "target": "target"}],
        },
        lambda marker: {
            **marker,
            "mappings": [{"source": "/outside/pool", "target": "/tmp/skill"}],
        },
        lambda marker: {
            **marker,
            "mappings": [
                {
                    "source": marker["pool_local"],
                    "target": marker["pool_local"],
                }
            ],
        },
    ],
)
def test_active_marker_contract_mismatch_is_invalid(tmp_path, mutate):
    home, _, pool_local, pool_repo = _ready_home(tmp_path)
    marker_path = _write_active_marker(home)
    marker = json.loads(marker_path.read_text())
    marker["pool_local"] = str(pool_local)
    marker_path.write_text(json.dumps(mutate(marker)))

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "active_marker_contract_mismatch"


def test_active_marker_must_be_regular_file(tmp_path):
    home, _, _, pool_repo = _ready_home(tmp_path)
    _active_marker_path(home).mkdir()

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "active_marker_not_regular_file"


def test_invalid_active_marker_payload_is_invalid(tmp_path):
    home, _, _, pool_repo = _ready_home(tmp_path)
    _active_marker_path(home).write_text("{")

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "active_marker_invalid"


@pytest.mark.parametrize(
    ("error", "status", "reason"),
    [
        (
            PermissionError("denied"),
            RuntimeLayoutInspectionStatus.INVALID,
            "active_marker_unreadable",
        ),
        (
            OSError(errno.ESTALE, "stale NAS handle"),
            RuntimeLayoutInspectionStatus.TRANSIENT_ERROR,
            "active_marker_temporarily_unavailable",
        ),
    ],
)
def test_active_marker_stat_error_is_classified(
    tmp_path, monkeypatch, error, status, reason
):
    home, _, _, pool_repo = _ready_home(tmp_path)
    active_marker = _write_active_marker(home)
    original_lstat = Path.lstat

    def fail_active_marker_lstat(path):
        if path == active_marker:
            raise error
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_active_marker_lstat)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is status
    assert result.evidence["reason"] == reason


def test_active_marker_read_io_error_is_transient(tmp_path, monkeypatch):
    home, _, _, pool_repo = _ready_home(tmp_path)
    active_marker = _write_active_marker(home)
    original_read_bytes = Path.read_bytes

    def fail_active_marker_read(path):
        if path == active_marker:
            raise OSError(errno.ESTALE, "stale NAS handle")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_active_marker_read)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.TRANSIENT_ERROR
    assert result.evidence["reason"] == "active_marker_temporarily_unavailable"


def test_active_marker_rejects_unretired_repo_bridge(tmp_path):
    home, _, _, pool_repo = _ready_home(tmp_path)
    _write_active_marker(home, activation_state="active")

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "retired_repo_bridge_present"


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

    def fail_marker_read(_path):
        raise OSError(errno.ESTALE, "stale NAS handle")

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

    def fail_marker_stat(_path, *_args, **_kwargs):
        raise OSError(errno.ESTALE, "stale NAS handle")

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
    assert result.evidence["reason"] == "managed_active_entries_temporarily_unavailable"


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preparation_id", "not-a-uuid"),
        ("prepared_at", None),
        ("prepared_at", "not-a-timestamp"),
    ],
)
def test_marker_contract_rejects_invalid_identity_or_timestamp(tmp_path, field, value):
    home, _, _, pool_repo = _ready_home(tmp_path)
    marker_path = home / ".openclaw" / "workspace" / "skills-pool" / ".pool-ready"
    marker = json.loads(marker_path.read_text())
    marker[field] = value
    marker_path.write_text(json.dumps(marker))

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "marker_contract_mismatch"


def test_marker_contract_rejects_non_dict_validation_summary(tmp_path):
    home, _, _, pool_repo = _ready_home(tmp_path)
    marker_path = home / ".openclaw" / "workspace" / "skills-pool" / ".pool-ready"
    marker = json.loads(marker_path.read_text())
    marker["validation_summary"] = []
    marker_path.write_text(json.dumps(marker))

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "marker_contract_mismatch"


def test_relative_repo_bridge_target_is_accepted(tmp_path):
    home, legacy_root, _, pool_repo = _ready_home(tmp_path)
    bridge = legacy_root / "skills-repo"
    bridge.unlink()
    bridge.symlink_to(
        os.path.relpath(pool_repo, start=bridge.parent),
        target_is_directory=True,
    )

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY


def test_repo_and_external_active_entries_preserve_ready_status(tmp_path):
    home, legacy_root, _, pool_repo = _ready_home(tmp_path)
    (pool_repo / "repo-skill").mkdir()
    repo_active = legacy_root / "repo-skill"
    repo_active.symlink_to(
        legacy_root / "skills-repo" / "repo-skill",
        target_is_directory=True,
    )
    external_skill = tmp_path / "external-skill"
    external_skill.mkdir()
    (legacy_root / "external-skill").symlink_to(
        external_skill,
        target_is_directory=True,
    )

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.READY


def test_invalid_declared_managed_entry_is_rejected(tmp_path):
    home, _, _, pool_repo = _ready_home(tmp_path)
    marker_path = home / ".openclaw" / "workspace" / "skills-pool" / ".pool-ready"
    marker = json.loads(marker_path.read_text())
    marker["validation_summary"]["managed_active_entries"] = [{"source": "unknown"}]
    marker_path.write_text(json.dumps(marker))

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.INVALID
    assert result.evidence["reason"] == "managed_active_entry_invalid"


def test_other_engine_is_not_capable_without_touching_home(tmp_path):
    result = inspect_runtime_layout(
        engine="unsupported-engine",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=tmp_path / "missing",
        repo_is_mounted=lambda _path: (_ for _ in ()).throw(AssertionError()),
    )

    assert result.status is RuntimeLayoutInspectionStatus.NOT_CAPABLE
    assert result.evidence["reason"] == "engine_pool_probe_not_implemented"
