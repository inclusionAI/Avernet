from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from engine.community.config import RepoDelivery
from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    LayoutIdentity,
    RuntimeLayoutContext,
    resolve_filesystem_skill_layout,
)
from engine.community.plugins.skills_pool.desktop_preparation import (
    DesktopPreparationStatus,
    prepare_desktop_pool,
)
from engine.community.plugins.skills_pool.layout_activation import (
    PoolActivationStatus,
    SkillMapping,
    activate_aicoding_pool,
    activate_claude_code_pool,
    activate_hermes_pool,
    activate_openclaw_pool,
    rollback_aicoding_pool,
    rollback_claude_code_pool,
    rollback_hermes_pool,
    rollback_openclaw_pool,
)
from engine.community.plugins.skills_pool.layout_probe import (
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)

FILESYSTEM_ENGINES = ("openclaw", "claude_code", "aicoding", "hermes")
ACTIVATE = {
    "openclaw": activate_openclaw_pool,
    "claude_code": activate_claude_code_pool,
    "aicoding": activate_aicoding_pool,
    "hermes": activate_hermes_pool,
}
ROLLBACK = {
    "openclaw": rollback_openclaw_pool,
    "claude_code": rollback_claude_code_pool,
    "aicoding": rollback_aicoding_pool,
    "hermes": rollback_hermes_pool,
}


def _target(path: Path) -> Path:
    target = path.readlink()
    if not target.is_absolute():
        target = path.parent / target
    return Path(os.path.abspath(target))


def _layout(home: Path, engine: str):
    return resolve_filesystem_skill_layout(
        LayoutIdentity(
            engine_type=engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
        ),
        RuntimeLayoutContext(home=home),
    )


def _legacy_runtime(home: Path, engine: str):
    layout = _layout(home, engine)
    (layout.legacy_local / "handmade").mkdir(parents=True)
    (layout.legacy_local / "handmade" / "SKILL.md").write_text("legacy")
    repo_source = home / ".openclaw/workspace/skills/skills-repo"
    (repo_source / "business/reviewer").mkdir(parents=True)
    (repo_source / "business/reviewer/SKILL.md").write_text("repo")
    layout.active_root.mkdir(parents=True, exist_ok=True)
    if layout.local_bridge != layout.legacy_local:
        layout.local_bridge.symlink_to(
            layout.legacy_local,
            target_is_directory=True,
        )
    if layout.legacy_repo != repo_source:
        layout.legacy_repo.parent.mkdir(parents=True, exist_ok=True)
        layout.legacy_repo.symlink_to(repo_source, target_is_directory=True)
    if (
        layout.repo_bridge != layout.legacy_repo
        and not layout.repo_bridge.exists()
        and not layout.repo_bridge.is_symlink()
    ):
        layout.repo_bridge.symlink_to(
            layout.legacy_repo,
            target_is_directory=True,
        )
    return layout, repo_source


@pytest.mark.parametrize("engine", FILESYSTEM_ENGINES)
def test_desktop_download_layout_uses_public_cutover_and_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
) -> None:
    monkeypatch.setenv("MAC_CONTAINER", "true")
    home = tmp_path / "home/admin"
    layout, repo_source = _legacy_runtime(home, engine)
    prepared = prepare_desktop_pool(
        engine=engine,
        repo_source=repo_source,
        home=home,
    )
    assert prepared.status is DesktopPreparationStatus.PREPARED

    mapping = SkillMapping(
        source=str(layout.pool_local / "handmade"),
        target=str(layout.active_root / "handmade"),
    )
    activated = ACTIVATE[engine](
        migration_generation="G1",
        preparation_id=str(prepared.preparation_id),
        registered_local_names=["handmade"],
        mappings=[mapping],
        home=home,
    )

    assert activated.status is PoolActivationStatus.COMMITTED
    assert (layout.pool_repo / "business/reviewer/SKILL.md").read_text() == (
        "repo"
    )
    assert layout.pool_repo.is_dir()
    assert not layout.pool_repo.is_symlink()
    assert _target(layout.active_root / "handmade") == (
        layout.pool_local / "handmade"
    )
    if engine in {"aicoding", "hermes"}:
        assert _target(layout.repo_bridge) == layout.pool_repo
    else:
        assert not layout.repo_bridge.exists()
        assert not layout.repo_bridge.is_symlink()
    active = inspect_runtime_layout(
        engine=engine,
        home=home,
        repo_delivery=RepoDelivery.DOWNLOAD,
    )
    assert active.status is RuntimeLayoutInspectionStatus.READY
    if engine in {"aicoding", "hermes"}:
        assert active.evidence["checks"]["stable_repo_bridge_valid"] is True
    else:
        assert "stable_repo_bridge_valid" not in active.evidence["checks"]

    active_marker = json.loads(layout.active_marker.read_text())
    active_marker["activation_state"] = "finalizing"
    layout.active_marker.write_text(json.dumps(active_marker))
    finalizing = inspect_runtime_layout(
        engine=engine,
        home=home,
        repo_delivery=RepoDelivery.DOWNLOAD,
    )
    assert finalizing.status is RuntimeLayoutInspectionStatus.READY
    assert "stable_repo_bridge_valid" not in finalizing.evidence["checks"]
    active_marker["activation_state"] = "active"
    layout.active_marker.write_text(json.dumps(active_marker))

    (layout.pool_local / "handmade/SKILL.md").write_text("pool")
    rolled_back = ROLLBACK[engine](
        rollback_generation="R1",
        registered_local_names=["handmade"],
        home=home,
    )

    assert rolled_back.status is PoolActivationStatus.COMMITTED
    assert (layout.legacy_local / "handmade/SKILL.md").read_text() == "pool"
    assert (repo_source / "business/reviewer/SKILL.md").read_text() == "repo"
    assert repo_source.is_symlink()
    assert _target(repo_source) == layout.pool_repo
    legacy_ready = inspect_runtime_layout(
        engine=engine,
        home=home,
        repo_delivery=RepoDelivery.DOWNLOAD,
    )
    assert legacy_ready.status is RuntimeLayoutInspectionStatus.READY
    assert legacy_ready.preparation_id == prepared.preparation_id
