from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
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
    MappingSourceLayout,
    PoolActivationResult,
    PoolActivationStatus,
    SkillMapping,
    activate_aicoding_pool,
    activate_claude_code_pool,
    activate_hermes_pool,
    activate_openclaw_pool,
    publish_pool_mappings,
    rollback_aicoding_pool,
    rollback_claude_code_pool,
    rollback_hermes_pool,
    rollback_openclaw_pool,
)
from engine.community.plugins.skills_pool.layout_probe import (
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)

Activate = Callable[..., PoolActivationResult]
Rollback = Callable[..., PoolActivationResult]


@pytest.mark.parametrize(
    ("engine", "activate", "rollback"),
    [
        ("openclaw", activate_openclaw_pool, rollback_openclaw_pool),
        ("claude_code", activate_claude_code_pool, rollback_claude_code_pool),
        ("aicoding", activate_aicoding_pool, rollback_aicoding_pool),
        ("hermes", activate_hermes_pool, rollback_hermes_pool),
    ],
)
def test_desktop_download_rollback_is_ready_for_remigration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    activate: Activate,
    rollback: Rollback,
) -> None:
    monkeypatch.setenv("MAC_CONTAINER", "true")
    home = tmp_path / "home" / "admin"
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity(engine, LAYOUT_CONTRACT_VERSION),
        RuntimeLayoutContext(home=home),
    )
    layout.active_root.mkdir(parents=True)
    layout.legacy_local.mkdir(parents=True)
    if layout.local_bridge != layout.legacy_local:
        layout.local_bridge.symlink_to(
            layout.legacy_local,
            target_is_directory=True,
        )
    repo_source = layout.legacy_repo
    (repo_source / "repo-skill").mkdir(parents=True)
    (repo_source / "repo-skill" / "SKILL.md").write_text("repo")
    if layout.repo_bridge != layout.legacy_repo:
        layout.repo_bridge.parent.mkdir(parents=True, exist_ok=True)
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
    assert prepared.preparation_id is not None
    mapping = SkillMapping(
        source=str(layout.pool_repo / "repo-skill"),
        target=str(layout.active_root / "repo-skill"),
    )
    activated = activate(
        migration_generation="generation-1",
        preparation_id=prepared.preparation_id,
        registered_local_names=[],
        mappings=[mapping],
        home=home,
    )
    assert activated.status is PoolActivationStatus.COMMITTED

    rolled_back = rollback(
        rollback_generation="generation-1",
        registered_local_names=[],
        home=home,
    )
    assert rolled_back.status is PoolActivationStatus.COMMITTED
    assert "reason" not in rolled_back.evidence
    assert not layout.active_marker.exists()
    assert layout.repo_bridge.is_symlink()
    assert layout.repo_bridge.resolve() == layout.pool_repo.resolve()
    if layout.local_bridge != layout.legacy_local:
        assert layout.local_bridge.is_symlink()
        assert layout.local_bridge.resolve() == layout.legacy_local.resolve()

    legacy_mapping = SkillMapping(
        source=str(layout.legacy_repo / "repo-skill"),
        target=str(layout.active_root / "repo-skill"),
    )
    republished = publish_pool_mappings(
        mappings=[legacy_mapping],
        home=home,
        engine=engine,
        source_layout=MappingSourceLayout.LEGACY,
    )
    assert republished.published
    ready = inspect_runtime_layout(
        engine=engine,
        home=home,
    )
    assert ready.status is RuntimeLayoutInspectionStatus.READY
    assert ready.preparation_id == prepared.preparation_id
