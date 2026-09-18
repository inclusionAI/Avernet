from __future__ import annotations

import json
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
