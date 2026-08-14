"""Cross-engine wire contract for READY Skills Pool Probe evidence."""

from __future__ import annotations

import json
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
    activate_aicoding_pool,
    activate_claude_code_pool,
    activate_hermes_pool,
    activate_openclaw_pool,
)
from engine.community.plugins.skills_pool.layout_probe import (
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)

_ACTIVATE = {
    "openclaw": activate_openclaw_pool,
    "claude_code": activate_claude_code_pool,
    "aicoding": activate_aicoding_pool,
    "hermes": activate_hermes_pool,
}


def _active_desktop_layout(home: Path, engine: str):
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity(engine, LAYOUT_CONTRACT_VERSION),
        RuntimeLayoutContext(home=home),
    )
    layout.legacy_local.mkdir(parents=True)
    repo_source = home / ".desktop-skills-repo"
    repo_source.mkdir()
    layout.active_root.mkdir(parents=True, exist_ok=True)
    if layout.local_bridge != layout.legacy_local:
        layout.local_bridge.symlink_to(
            layout.legacy_local,
            target_is_directory=True,
        )
    if layout.legacy_repo != repo_source:
        layout.legacy_repo.parent.mkdir(parents=True, exist_ok=True)
        layout.legacy_repo.symlink_to(
            repo_source,
            target_is_directory=True,
        )
    if (
        layout.repo_bridge != layout.legacy_repo
        and not layout.repo_bridge.exists()
        and not layout.repo_bridge.is_symlink()
    ):
        layout.repo_bridge.symlink_to(
            layout.legacy_repo,
            target_is_directory=True,
        )
    prepared = prepare_desktop_pool(
        engine=engine,
        repo_source=repo_source,
        home=home,
    )
    assert prepared.status is DesktopPreparationStatus.PREPARED
    activated = _ACTIVATE[engine](
        migration_generation="generation-1",
        preparation_id=str(prepared.preparation_id),
        registered_local_names=[],
        mappings=[],
        home=home,
    )
    assert activated.status is PoolActivationStatus.COMMITTED
    return layout


@pytest.mark.parametrize(
    "engine",
    ("openclaw", "claude_code", "aicoding", "hermes"),
)
def test_stable_repo_bridge_check_is_conditional_and_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
) -> None:
    monkeypatch.setenv("MAC_CONTAINER", "true")
    home = tmp_path / "home/admin"
    layout = _active_desktop_layout(home, engine)

    active = inspect_runtime_layout(
        engine=engine,
        home=home,
        repo_delivery=RepoDelivery.DOWNLOAD,
    )
    assert active.status is RuntimeLayoutInspectionStatus.READY
    checks = active.evidence["checks"]
    if engine in {"aicoding", "hermes"}:
        assert checks["stable_repo_bridge_valid"] is True
    else:
        assert "stable_repo_bridge_valid" not in checks

    marker = json.loads(layout.active_marker.read_text())
    marker["activation_state"] = "finalizing"
    layout.active_marker.write_text(json.dumps(marker))
    finalizing = inspect_runtime_layout(
        engine=engine,
        home=home,
        repo_delivery=RepoDelivery.DOWNLOAD,
    )
    assert finalizing.status is RuntimeLayoutInspectionStatus.READY
    assert "stable_repo_bridge_valid" not in finalizing.evidence["checks"]
