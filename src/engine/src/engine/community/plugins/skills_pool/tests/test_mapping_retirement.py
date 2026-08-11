from __future__ import annotations

from pathlib import Path

import pytest

from engine.community.plugins.skills_pool.layout_activation import (
    SkillMapping,
    _Layout,
    publish_pool_mappings,
    verify_skill_mappings,
)


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "hermes"])
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


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "hermes"])
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


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "hermes"])
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


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "hermes"])
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


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "hermes"])
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
        retired_mappings=[
            SkillMapping(str(old_source), str(retired_target))
        ],
        home=home,
        engine=engine,
    )

    assert not published.published
    assert published.evidence["reason"] == "retired_mapping_invalid"
    assert retired_target.is_symlink()
    assert retired_target.readlink() == other_source
    assert not desired_target.exists()
