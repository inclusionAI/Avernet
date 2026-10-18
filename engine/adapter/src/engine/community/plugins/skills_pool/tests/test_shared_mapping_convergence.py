"""A full snapshot owns Repo/Center links, never implicitly owns Local links."""

from pathlib import Path

import pytest

from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION, LayoutIdentity, RuntimeLayoutContext,
    resolve_filesystem_skill_layout,
)
from engine.community.plugins.skills_pool.layout_activation import (
    MappingApplyMode, MappingProjectionStatus, MappingSourceLayout,
    SkillMapping, publish_pool_mappings, verify_skill_mappings,
)
from engine.community.plugins.skills_pool.center_content import MountedCenterContentAdapter
from engine.community.plugins.skills_pool.mapping_contract import apply_logical_mapping_payload


@pytest.mark.parametrize("engine", ["openclaw", "hermes", "claude_code", "aicoding"])
@pytest.mark.parametrize("layout_kind", [MappingSourceLayout.LEGACY, MappingSourceLayout.POOL])
@pytest.mark.parametrize("mode", list(MappingApplyMode))
def test_empty_snapshot_prunes_both_shared_layouts_but_preserves_local_and_users(
    tmp_path: Path, engine, layout_kind, mode,
):
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity(engine, LAYOUT_CONTRACT_VERSION), RuntimeLayoutContext(home=tmp_path),
    )
    layout.active_root.mkdir(parents=True, exist_ok=True)
    sources = {
        "legacy-repo": layout.legacy_repo / "infra/old",
        "pool-repo": layout.pool_repo / "infra/old",
        "center": layout.pool_center / "uuid/1",
        "local-legacy": layout.legacy_local / "agent-written",
        "local-pool": layout.pool_local / "agent-written",
        "external": tmp_path / "user-owned/skill",
    }
    for name, source in sources.items():
        source.mkdir(parents=True, exist_ok=True)
        (layout.active_root / name).symlink_to(source, target_is_directory=True)
    (layout.active_root / "user-directory").mkdir()
    # A dangling shared link must also be retired without reading package bytes.
    (layout.active_root / "dangling").symlink_to(layout.pool_repo / "gone")
    before = verify_skill_mappings(
        mappings=[], home=tmp_path, engine=engine, source_layout=layout_kind, apply_mode=mode,
    )
    assert not before.valid
    result = publish_pool_mappings(
        mappings=[], home=tmp_path, engine=engine, source_layout=layout_kind, apply_mode=mode,
    )
    assert result.status is MappingProjectionStatus.CONVERGED
    for name in ("legacy-repo", "pool-repo", "center", "dangling"):
        assert not (layout.active_root / name).is_symlink()
    for name in ("local-legacy", "local-pool", "external"):
        assert (layout.active_root / name).is_symlink()
    assert (layout.active_root / "user-directory").is_dir()
    assert all(source.is_dir() for source in sources.values())
    assert verify_skill_mappings(
        mappings=[], home=tmp_path, engine=engine, source_layout=layout_kind, apply_mode=mode,
    ).valid


def test_explicit_local_retirement_and_selected_direct_repo_survive_convergence(tmp_path):
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity("openclaw", LAYOUT_CONTRACT_VERSION), RuntimeLayoutContext(home=tmp_path),
    )
    layout.active_root.mkdir(parents=True)
    repo = layout.legacy_repo / "selected"
    local = layout.legacy_local / "explicitly-uninstalled"
    repo.mkdir(parents=True)
    local.mkdir(parents=True)
    selected = SkillMapping(str(repo), str(layout.active_root / "direct"))
    retired = SkillMapping(str(local), str(layout.active_root / "local"))
    Path(selected.target).symlink_to(repo)
    Path(retired.target).symlink_to(local)
    result = publish_pool_mappings(
        mappings=[selected], retired_mappings=[retired], home=tmp_path,
        source_layout=MappingSourceLayout.LEGACY, apply_mode=MappingApplyMode.BEST_EFFORT,
    )
    assert result.status is MappingProjectionStatus.CONVERGED
    assert Path(selected.target).readlink() == repo
    assert not Path(retired.target).is_symlink()
    assert local.is_dir()


def test_pending_center_keeps_declared_link_while_removing_other_shared_links(tmp_path):
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity("openclaw", LAYOUT_CONTRACT_VERSION), RuntimeLayoutContext(home=tmp_path),
    )
    layout.active_root.mkdir(parents=True)
    selected = layout.active_root / "selected"
    selected.symlink_to(layout.pool_center / "uuid/old")
    extra = layout.active_root / "extra"
    extra.symlink_to(layout.legacy_repo / "old")
    result = apply_logical_mapping_payload(
        engine="openclaw", source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[{
            "corpus": "center", "skill_uuid": "22222222-2222-4222-8222-222222222222", "sc_version_number": "2",
            "link_name": "selected",
        }], retired_payload=[], home=tmp_path, content_adapter=MountedCenterContentAdapter(),
    )
    assert result.status is MappingProjectionStatus.PENDING
    assert selected.is_symlink()
    assert not extra.is_symlink()


def test_unlink_failure_is_pending_and_repeated_snapshot_retries(tmp_path, monkeypatch):
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity("openclaw", LAYOUT_CONTRACT_VERSION), RuntimeLayoutContext(home=tmp_path),
    )
    layout.active_root.mkdir(parents=True)
    stale = layout.active_root / "stale"
    stale.symlink_to(layout.legacy_repo / "gone")
    unlink = Path.unlink

    def denied(path, *args, **kwargs):
        if path == stale:
            raise PermissionError("denied")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", denied)
        result = publish_pool_mappings(
            mappings=[], home=tmp_path, source_layout=MappingSourceLayout.LEGACY,
            apply_mode=MappingApplyMode.BEST_EFFORT,
        )
    assert result.status is MappingProjectionStatus.PENDING
    assert any(item.code == "MAPPING_PUBLISH_IO_ERROR" and item.retryable for item in result.items)
    assert stale.is_symlink()
    assert publish_pool_mappings(
        mappings=[], home=tmp_path, source_layout=MappingSourceLayout.LEGACY,
        apply_mode=MappingApplyMode.BEST_EFFORT,
    ).status is MappingProjectionStatus.CONVERGED
    assert not stale.is_symlink()


def test_repo_prefix_escape_and_structural_bridges_are_preserved(tmp_path):
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity("openclaw", LAYOUT_CONTRACT_VERSION), RuntimeLayoutContext(home=tmp_path),
    )
    layout.active_root.mkdir(parents=True)
    layout.legacy_repo.mkdir(parents=True)
    external = tmp_path / "user"
    external.mkdir()
    (layout.legacy_repo / "escape").symlink_to(external)
    (layout.active_root / "external").symlink_to(layout.legacy_repo / "escape/skill")
    (layout.active_root / "prefix").symlink_to(Path(str(layout.legacy_repo) + "-user") / "skill")
    result = publish_pool_mappings(
        mappings=[], home=tmp_path, source_layout=MappingSourceLayout.LEGACY,
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )
    assert result.status is MappingProjectionStatus.CONVERGED
    assert all((layout.active_root / name).is_symlink() for name in ("external", "prefix"))
    assert layout.legacy_repo.is_dir()


def test_invalid_snapshot_cannot_prune_existing_links(tmp_path):
    from engine.community.core.skills.exceptions import InvalidPoolMappingRequestError

    layout = resolve_filesystem_skill_layout(
        LayoutIdentity("openclaw", LAYOUT_CONTRACT_VERSION), RuntimeLayoutContext(home=tmp_path),
    )
    layout.active_root.mkdir(parents=True)
    stale = layout.active_root / "stale"
    stale.symlink_to(layout.legacy_repo / "gone")
    with pytest.raises(InvalidPoolMappingRequestError):
        apply_logical_mapping_payload(
            engine="openclaw", source_layout=MappingSourceLayout.LEGACY,
            mappings_payload=[{"corpus": "repo", "relative_path": "../escape", "link_name": "x"}],
            retired_payload=[], home=tmp_path, content_adapter=MountedCenterContentAdapter(),
        )
    assert stale.is_symlink()
