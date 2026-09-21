from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from engine.community.core.skills.layout_planner import LAYOUT_CONTRACT_VERSION
from engine.community.plugins.skills_pool.layout_probe import (
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)
from engine.community.plugins.skills_pool.pool_native_initialization import (
    PoolNativeInitializationError,
    initialize_pool_native,
    main,
)


def test_empty_openclaw_home_initializes_minimal_active_layout(tmp_path: Path) -> None:
    home = tmp_path / "home" / "admin"

    evidence = initialize_pool_native(engine="openclaw", home=home)

    pool_root = home / ".openclaw" / "workspace" / "skills-pool"
    active_root = home / ".openclaw" / "workspace" / "skills"
    marker = json.loads((pool_root / ".pool-active").read_text())
    assert marker == {
        "activation_state": "active",
        "engine": "openclaw",
        "layout_contract_version": LAYOUT_CONTRACT_VERSION,
    }
    assert active_root.is_dir()
    assert (pool_root / "skills-local").is_dir()
    assert not (pool_root / ".pool-ready").exists()
    assert not (active_root / "skills-local").exists()
    assert not (active_root / "skills-repo").exists()
    assert evidence.roots_initialized is True


def test_concurrent_directory_creation_converges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home" / "admin"
    active_root = home / ".openclaw" / "workspace" / "skills"
    original_mkdir = Path.mkdir
    lost_once = False

    def concurrent_mkdir(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        nonlocal lost_once
        if path == active_root and not lost_once:
            lost_once = True
            original_mkdir(path, mode=mode, parents=parents, exist_ok=True)
            raise FileExistsError(path)
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", concurrent_mkdir)

    evidence = initialize_pool_native(engine="openclaw", home=home)

    assert lost_once
    assert active_root.is_dir()
    assert evidence.roots_initialized is True


def test_restart_repairs_missing_marker_without_touching_pool_content(tmp_path: Path) -> None:
    home = tmp_path / "home" / "admin"
    pool_local = home / ".openclaw" / "workspace" / "skills-pool" / "skills-local"
    (pool_local / "kept").mkdir(parents=True)
    (pool_local / "kept" / "SKILL.md").write_text("keep")
    (home / ".openclaw" / "workspace" / "skills").mkdir(parents=True)

    initialize_pool_native(engine="openclaw", home=home)

    assert (pool_local / "kept" / "SKILL.md").read_text() == "keep"


def test_restart_accepts_matching_active_marker(tmp_path: Path) -> None:
    home = tmp_path / "home" / "admin"
    initialize_pool_native(engine="openclaw", home=home)

    evidence = initialize_pool_native(engine="openclaw", home=home)

    assert evidence.actual_layout == "pool"


def test_restart_accepts_and_preserves_completed_migration_marker(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    initialize_pool_native(engine="openclaw", home=home)
    marker_path = home / ".openclaw/workspace/skills-pool/.pool-active"
    marker = json.loads(marker_path.read_text())
    marker.update(
        {
            "preparation_id": "2a958f59-8cf4-4413-a267-7d56d3382f23",
            "migration_generation": "generation-1",
        }
    )
    marker_path.write_text(json.dumps(marker))

    evidence = initialize_pool_native(engine="openclaw", home=home)

    assert evidence.actual_layout == "pool"
    assert json.loads(marker_path.read_text()) == marker


def test_restart_rejects_partial_migration_identity(tmp_path: Path) -> None:
    home = tmp_path / "home" / "admin"
    initialize_pool_native(engine="openclaw", home=home)
    marker_path = home / ".openclaw/workspace/skills-pool/.pool-active"
    marker = json.loads(marker_path.read_text())
    marker["preparation_id"] = "2a958f59-8cf4-4413-a267-7d56d3382f23"
    marker_path.write_text(json.dumps(marker))

    with pytest.raises(PoolNativeInitializationError, match="conflicts with startup"):
        initialize_pool_native(engine="openclaw", home=home)


def test_concurrent_finalizing_marker_is_preserved_and_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home" / "admin"
    marker_path = home / ".openclaw/workspace/skills-pool/.pool-active"
    finalizing = {
        "engine": "openclaw",
        "layout_contract_version": LAYOUT_CONTRACT_VERSION,
        "activation_state": "finalizing",
        "preparation_id": "preparation-1",
        "migration_generation": "generation-1",
    }

    def concurrent_create(_source: os.PathLike[str], target: os.PathLike[str]) -> None:
        Path(target).write_text(json.dumps(finalizing))
        raise FileExistsError

    monkeypatch.setattr(os, "link", concurrent_create)

    with pytest.raises(PoolNativeInitializationError, match="conflicts with startup"):
        initialize_pool_native(engine="openclaw", home=home)

    assert json.loads(marker_path.read_text()) == finalizing


def test_non_openclaw_engine_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PoolNativeInitializationError, match="unsupported for engine"):
        initialize_pool_native(engine="hermes", home=tmp_path)


def test_invalid_existing_active_marker_is_rejected(tmp_path: Path) -> None:
    marker = (
        tmp_path
        / ".openclaw"
        / "workspace"
        / "skills-pool"
        / ".pool-active"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text("not-json")

    with pytest.raises(PoolNativeInitializationError, match="marker is invalid"):
        initialize_pool_native(engine="openclaw", home=tmp_path)


def test_conflicting_legacy_entry_is_preserved_and_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home" / "admin"
    legacy_local = home / ".openclaw" / "workspace" / "skills" / "skills-local"
    legacy_local.mkdir(parents=True)
    (legacy_local / "do-not-delete").write_text("content")

    with pytest.raises(PoolNativeInitializationError, match="legacy entry"):
        initialize_pool_native(engine="openclaw", home=home)

    assert (legacy_local / "do-not-delete").read_text() == "content"


def test_prepared_migration_is_not_promoted_to_native_active(tmp_path: Path) -> None:
    home = tmp_path / "home" / "admin"
    ready_marker = home / ".openclaw/workspace/skills-pool/.pool-ready"
    ready_marker.parent.mkdir(parents=True)
    ready_marker.write_text('{"preparation_id":"prepared-1"}')

    with pytest.raises(
        PoolNativeInitializationError,
        match="migration preparation requires recovery",
    ):
        initialize_pool_native(engine="openclaw", home=home)

    assert ready_marker.read_text() == '{"preparation_id":"prepared-1"}'
    assert not (ready_marker.parent / ".pool-active").exists()


def test_legacy_entry_stat_error_is_not_treated_as_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home" / "admin"
    legacy_local = home / ".openclaw/workspace/skills/skills-local"
    original_lstat = Path.lstat

    def fail_legacy_lstat(path: Path):
        if path == legacy_local:
            raise OSError(errno.ESTALE, "stale NAS handle")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_legacy_lstat)

    with pytest.raises(
        PoolNativeInitializationError,
        match="Legacy entry could not be inspected",
    ):
        initialize_pool_native(engine="openclaw", home=home)

    assert not (home / ".openclaw/workspace/skills-pool/.pool-active").exists()


def test_steady_probe_accepts_minimal_active_marker_without_ready_history(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    initialize_pool_native(engine="openclaw", home=home)

    result = inspect_runtime_layout(engine="openclaw", home=home)

    assert result.status is RuntimeLayoutInspectionStatus.READY
    assert result.preparation_id is None
    assert result.evidence["activation_state"] == "active"
    assert result.evidence["checks"]["active_marker_valid"] is True


def test_finalizing_marker_without_migration_history_is_not_steady(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    initialize_pool_native(engine="openclaw", home=home)
    marker_path = home / ".openclaw" / "workspace" / "skills-pool" / ".pool-active"
    marker = json.loads(marker_path.read_text())
    marker["activation_state"] = "finalizing"
    marker_path.write_text(json.dumps(marker))

    result = inspect_runtime_layout(engine="openclaw", home=home)

    assert result.status is not RuntimeLayoutInspectionStatus.READY


def test_cli_prints_machine_readable_initialization_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--engine", "openclaw", "--home", str(tmp_path)]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "actual_engine": "openclaw",
        "actual_layout": "pool",
        "layout_contract_version": LAYOUT_CONTRACT_VERSION,
        "roots_initialized": True,
    }
