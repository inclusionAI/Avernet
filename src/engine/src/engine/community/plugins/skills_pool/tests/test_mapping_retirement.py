from __future__ import annotations

from pathlib import Path

import pytest

from engine.community.plugins.claude_code.layout_pool import (
    claude_code_retirement_active_roots,
)
from engine.community.plugins.skills_pool.layout_activation import (
    MappingApplyMode,
    MappingSourceLayout,
    SkillMapping,
    _Layout,
    publish_pool_mappings,
    verify_skill_mappings,
)


def test_best_effort_keeps_unmanaged_directory_and_publishes_safe_entries(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    layout = _Layout.for_engine("openclaw", home)
    layout.active_root.mkdir(parents=True)
    ready_source = layout.pool_local / "ready"
    ready_source.mkdir(parents=True)
    missing_source = layout.pool_local / "missing"
    blocked = layout.active_root / "blocked"
    blocked.mkdir()

    result = publish_pool_mappings(
        mappings=[
            SkillMapping(str(ready_source), str(layout.active_root / "ready")),
            SkillMapping(str(missing_source), str(layout.active_root / "missing")),
            SkillMapping(str(ready_source), str(blocked)),
        ],
        home=home,
        engine="openclaw",
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )
    verified = verify_skill_mappings(
        mappings=[
            SkillMapping(str(ready_source), str(layout.active_root / "ready")),
            SkillMapping(str(missing_source), str(layout.active_root / "missing")),
            SkillMapping(str(ready_source), str(blocked)),
        ],
        home=home,
        engine="openclaw",
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )

    assert result.published
    assert (layout.active_root / "ready").readlink() == ready_source
    assert (layout.active_root / "missing").is_symlink()
    assert (layout.active_root / "missing").readlink() == missing_source
    assert blocked.is_dir() and not blocked.is_symlink()
    assert verified.valid
    assert any(item["reason"] == "source_missing" for item in verified.evidence["pending"])
    assert any(
        item["reason"] == "UNMANAGED_ACTIVE_ENTRY_RETAINED"
        for item in result.evidence["issues"]
    )


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "aicoding", "hermes"])
def test_retired_product_mapping_is_removed_without_touching_other_entries(
    tmp_path: Path,
    engine: str,
) -> None:
    home = tmp_path / "home" / "admin"
    layout = _Layout.for_engine(engine, home)
    layout.active_root.mkdir(parents=True)

    keep_source = layout.pool_local / "keep"
    retired_source = layout.pool_local / "retired"
    filesystem_only_source = layout.pool_local / "filesystem-only"
    for source in (keep_source, retired_source, filesystem_only_source):
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(source.name)

    keep_target = layout.active_root / "keep"
    retired_target = layout.active_root / "retired"
    filesystem_only_target = layout.active_root / "filesystem-only"
    keep_target.symlink_to(keep_source, target_is_directory=True)
    retired_target.symlink_to(retired_source, target_is_directory=True)
    filesystem_only_target.symlink_to(
        filesystem_only_source,
        target_is_directory=True,
    )

    external_source = tmp_path / "external"
    external_source.mkdir()
    external_target = layout.active_root / "external"
    external_target.symlink_to(external_source, target_is_directory=True)

    desired = [SkillMapping(str(keep_source), str(keep_target))]
    retired = [SkillMapping(str(retired_source), str(retired_target))]

    published = publish_pool_mappings(
        mappings=desired,
        retired_mappings=retired,
        home=home,
        engine=engine,
    )
    verified = verify_skill_mappings(
        mappings=desired,
        retired_mappings=retired,
        home=home,
        engine=engine,
    )

    assert published.published
    assert published.evidence["removed"] == [str(retired_target)]
    assert verified.valid
    assert not retired_target.exists()
    assert not retired_target.is_symlink()
    assert filesystem_only_target.readlink() == filesystem_only_source
    assert external_target.readlink() == external_source


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "aicoding", "hermes"])
def test_retired_mapping_allows_same_name_product_replacement(
    tmp_path: Path,
    engine: str,
) -> None:
    home = tmp_path / "home" / "admin"
    layout = _Layout.for_engine(engine, home)
    layout.active_root.mkdir(parents=True)
    old_source = layout.pool_repo / "old" / "shared"
    new_source = layout.pool_repo / "new" / "shared"
    old_source.mkdir(parents=True)
    new_source.mkdir(parents=True)
    target = layout.active_root / "shared"
    target.symlink_to(old_source, target_is_directory=True)

    published = publish_pool_mappings(
        mappings=[SkillMapping(str(new_source), str(target))],
        retired_mappings=[SkillMapping(str(old_source), str(target))],
        home=home,
        engine=engine,
    )
    verified = verify_skill_mappings(
        mappings=[SkillMapping(str(new_source), str(target))],
        retired_mappings=[SkillMapping(str(old_source), str(target))],
        home=home,
        engine=engine,
    )

    assert published.published
    assert target.readlink() == new_source
    assert verified.valid


def test_invalid_retired_mapping_fails_before_desired_mapping_mutation(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    layout = _Layout.for_engine("openclaw", home)
    layout.active_root.mkdir(parents=True)
    source = layout.pool_local / "keep"
    source.mkdir(parents=True)
    target = layout.active_root / "keep"
    old_target = tmp_path / "outside-active-root"

    published = publish_pool_mappings(
        mappings=[SkillMapping(str(source), str(target))],
        retired_mappings=[SkillMapping(str(source), str(old_target))],
        home=home,
        engine="openclaw",
    )

    assert not published.published
    assert published.evidence["reason"] == "retired_mapping_invalid"
    assert not target.exists()
    assert not target.is_symlink()


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "aicoding", "hermes"])
def test_absent_retired_mapping_is_idempotent(
    tmp_path: Path,
    engine: str,
) -> None:
    home = tmp_path / "home/admin"
    layout = _Layout.for_engine(engine, home)
    layout.active_root.mkdir(parents=True)
    old_source = layout.pool_local / "old"
    old_source.mkdir(parents=True)
    retired = SkillMapping(
        str(old_source),
        str(layout.active_root / "old"),
    )

    published = publish_pool_mappings(
        mappings=[],
        retired_mappings=[retired],
        home=home,
        engine=engine,
    )
    verified = verify_skill_mappings(
        mappings=[],
        retired_mappings=[retired],
        home=home,
        engine=engine,
    )

    assert published.published
    assert verified.valid


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "aicoding", "hermes"])
def test_retired_target_rebound_to_external_is_preserved(
    tmp_path: Path,
    engine: str,
) -> None:
    home = tmp_path / "home/admin"
    layout = _Layout.for_engine(engine, home)
    layout.active_root.mkdir(parents=True)
    old_source = layout.pool_local / "old"
    old_source.mkdir(parents=True)
    external_source = tmp_path / "external"
    external_source.mkdir()
    target = layout.active_root / "old"
    target.symlink_to(external_source, target_is_directory=True)

    published = publish_pool_mappings(
        mappings=[],
        retired_mappings=[SkillMapping(str(old_source), str(target))],
        home=home,
        engine=engine,
    )

    assert published.published
    assert target.is_symlink()
    assert target.readlink() == external_source


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "aicoding", "hermes"])
def test_retired_target_rebound_to_other_managed_identity_fails_before_mutation(
    tmp_path: Path,
    engine: str,
) -> None:
    home = tmp_path / "home/admin"
    layout = _Layout.for_engine(engine, home)
    layout.active_root.mkdir(parents=True)
    old_source = layout.pool_local / "old"
    other_source = layout.pool_local / "other"
    desired_source = layout.pool_local / "desired"
    for source in (old_source, other_source, desired_source):
        source.mkdir(parents=True)
    retired_target = layout.active_root / "old"
    retired_target.symlink_to(other_source, target_is_directory=True)
    desired_target = layout.active_root / "desired"

    published = publish_pool_mappings(
        mappings=[SkillMapping(str(desired_source), str(desired_target))],
        retired_mappings=[SkillMapping(str(old_source), str(retired_target))],
        home=home,
        engine=engine,
    )

    assert not published.published
    assert published.evidence["reason"] == "retired_mapping_invalid"
    assert retired_target.is_symlink()
    assert retired_target.readlink() == other_source
    assert not desired_target.exists()


def test_claude_code_legacy_retirement_removes_current_and_historical_links(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home/admin"
    layout = _Layout.for_engine("claude_code", home)
    source = layout.legacy_local / "financial-data-query"
    source.mkdir(parents=True)
    historical_root = claude_code_retirement_active_roots(home=home)[0]
    for root in (layout.active_root, historical_root):
        root.mkdir(parents=True, exist_ok=True)
        (root / "financial-data-query").symlink_to(source, target_is_directory=True)

    retired = [
        SkillMapping(str(source), str(layout.active_root / "financial-data-query")),
        SkillMapping(
            str(source),
            str(historical_root / "financial-data-query"),
        ),
    ]
    published = publish_pool_mappings(
        mappings=[],
        retired_mappings=retired,
        home=home,
        engine="claude_code",
        source_layout=MappingSourceLayout.LEGACY,
        additional_retirement_roots=(historical_root,),
    )
    verified = verify_skill_mappings(
        mappings=[],
        retired_mappings=retired,
        home=home,
        engine="claude_code",
        source_layout=MappingSourceLayout.LEGACY,
        additional_retirement_roots=(historical_root,),
    )

    assert published.published
    assert verified.valid
    for root in (layout.active_root, historical_root):
        assert not (root / "financial-data-query").is_symlink()


def test_claude_code_historical_retirement_preserves_external_symlink(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home/admin"
    layout = _Layout.for_engine("claude_code", home)
    source = layout.legacy_local / "financial-data-query"
    source.mkdir(parents=True)
    layout.active_root.mkdir(parents=True, exist_ok=True)
    historical_root = claude_code_retirement_active_roots(home=home)[0]
    external = tmp_path / "external"
    external.mkdir()
    historical_root.mkdir(parents=True, exist_ok=True)
    target = historical_root / "financial-data-query"
    target.symlink_to(external, target_is_directory=True)

    published = publish_pool_mappings(
        mappings=[],
        retired_mappings=[SkillMapping(str(source), str(target))],
        home=home,
        engine="claude_code",
        source_layout=MappingSourceLayout.LEGACY,
        additional_retirement_roots=(historical_root,),
    )

    assert published.published
    assert target.readlink() == external


def test_claude_code_historical_retirement_rejects_occupied_entry_before_mutation(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home/admin"
    layout = _Layout.for_engine("claude_code", home)
    source = layout.legacy_local / "financial-data-query"
    source.mkdir(parents=True)
    layout.active_root.mkdir(parents=True, exist_ok=True)
    historical_root = claude_code_retirement_active_roots(home=home)[0]
    historical_root.mkdir(parents=True, exist_ok=True)
    occupied = historical_root / "financial-data-query"
    occupied.write_text("user content")
    desired_source = layout.legacy_local / "desired"
    desired_source.mkdir()
    desired_target = layout.active_root / "desired"

    published = publish_pool_mappings(
        mappings=[SkillMapping(str(desired_source), str(desired_target))],
        retired_mappings=[SkillMapping(str(source), str(occupied))],
        home=home,
        engine="claude_code",
        source_layout=MappingSourceLayout.LEGACY,
        additional_retirement_roots=(historical_root,),
    )

    assert not published.published
    assert published.evidence["reason"] == "retired_mapping_invalid"
    assert occupied.read_text() == "user content"
    assert not desired_target.exists()


def test_claude_code_historical_retirement_is_idempotent_when_absent(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home/admin"
    layout = _Layout.for_engine("claude_code", home)
    source = layout.legacy_local / "financial-data-query"
    source.mkdir(parents=True)
    layout.active_root.mkdir(parents=True, exist_ok=True)
    historical_root = claude_code_retirement_active_roots(home=home)[0]
    target = historical_root / "financial-data-query"

    published = publish_pool_mappings(
        mappings=[],
        retired_mappings=[SkillMapping(str(source), str(target))],
        home=home,
        engine="claude_code",
        source_layout=MappingSourceLayout.LEGACY,
        additional_retirement_roots=(historical_root,),
    )
    verified = verify_skill_mappings(
        mappings=[],
        retired_mappings=[SkillMapping(str(source), str(target))],
        home=home,
        engine="claude_code",
        source_layout=MappingSourceLayout.LEGACY,
        additional_retirement_roots=(historical_root,),
    )

    assert published.published
    assert published.evidence["retired_absent"] == [str(target)]
    assert verified.valid
