from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

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


def test_best_effort_mapping_keeps_unmanaged_entry_and_publishes_safe_entries(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    layout = _Layout.for_engine("aicoding", home)
    layout.active_root.mkdir(parents=True)

    ready_source = layout.pool_local / "ready"
    missing_source = layout.pool_local / "missing"
    ready_source.mkdir(parents=True)
    (ready_source / "SKILL.md").write_text("ready")

    ready_target = layout.active_root / "ready"
    missing_target = layout.active_root / "missing"
    occupied_target = layout.active_root / "occupied"
    occupied_target.mkdir()
    (occupied_target / "SKILL.md").write_text("image-owned")

    result = publish_pool_mappings(
        mappings=[
            SkillMapping(str(ready_source), str(ready_target)),
            SkillMapping(str(missing_source), str(missing_target)),
            SkillMapping(str(layout.pool_local / "occupied"), str(occupied_target)),
        ],
        home=home,
        engine="aicoding",
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )

    assert not result.published
    assert result.status == "DEGRADED"
    assert ready_target.readlink() == ready_source
    assert missing_target.is_symlink()
    assert missing_target.readlink() == missing_source
    assert occupied_target.is_dir()
    assert (occupied_target / "SKILL.md").read_text() == "image-owned"
    assert result.item_for(target=missing_target).status == "PENDING"
    assert result.item_for(target=missing_target).code == "MANAGED_SOURCE_MISSING"
    assert result.item_for(target=occupied_target).status == "DEGRADED"
    assert result.item_for(target=occupied_target).code == "UNMANAGED_ACTIVE_ENTRY_RETAINED"
    assert result.to_data()["items"] == [
        item.to_data() for item in result.items
    ]

    verified = verify_skill_mappings(
        mappings=[
            SkillMapping(str(ready_source), str(ready_target)),
            SkillMapping(str(missing_source), str(missing_target)),
            SkillMapping(str(layout.pool_local / "occupied"), str(occupied_target)),
        ],
        home=home,
        engine="aicoding",
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )

    assert not verified.valid
    assert verified.status == "DEGRADED"
    by_target = {item.target: item for item in verified.items}
    assert by_target[str(occupied_target)].status == "DEGRADED"
    assert by_target[str(missing_target)].status == "PENDING"
    assert verified.to_data()["items"] == [
        item.to_data() for item in verified.items
    ]


@pytest.mark.parametrize(
    ("engine", "source_layout", "source_root"),
    [
        ("openclaw", MappingSourceLayout.POOL, "pool_local"),
        ("claude_code", MappingSourceLayout.LEGACY, "legacy_repo"),
        ("hermes", MappingSourceLayout.POOL, "pool_center"),
    ],
)
def test_best_effort_uses_one_bounded_source_check_per_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    source_layout: MappingSourceLayout,
    source_root: str,
) -> None:
    home = tmp_path / "home/admin"
    layout = _Layout.for_engine(engine, home)
    layout.active_root.mkdir(parents=True)
    source = getattr(layout, source_root) / "nested" / "sample"
    source.mkdir(parents=True)
    for index in range(500):
        (source / f"asset-{index}.txt").write_text("payload")
    (source / "unreadable-child").write_text("still not inspected")
    target = layout.active_root / "sample"

    monkeypatch.setattr(
        os,
        "walk",
        Mock(side_effect=AssertionError("BEST_EFFORT must not recurse")),
    )
    published = publish_pool_mappings(
        mappings=[SkillMapping(str(source), str(target))],
        home=home,
        engine=engine,
        source_layout=source_layout,
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )
    verified = verify_skill_mappings(
        mappings=[SkillMapping(str(source), str(target))],
        home=home,
        engine=engine,
        source_layout=source_layout,
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )

    assert published.status == "CONVERGED"
    assert verified.status == "CONVERGED"
    assert published.evidence["source_checks"] == 1
    assert published.evidence["source_check_cache_hits"] == 1
    assert verified.evidence["source_checks"] == 1
    assert verified.evidence["source_check_cache_hits"] == 1


def test_best_effort_retirement_does_not_follow_dangling_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home/admin"
    layout = _Layout.for_engine("openclaw", home)
    layout.active_root.mkdir(parents=True)
    retired_source = layout.pool_repo / "removed" / "skill"
    target = layout.active_root / "skill"
    target.symlink_to(retired_source, target_is_directory=True)
    monkeypatch.setattr(
        Path,
        "exists",
        Mock(side_effect=AssertionError("retirement must use lstat")),
    )
    published = publish_pool_mappings(
        mappings=[],
        retired_mappings=[SkillMapping(str(retired_source), str(target))],
        home=home,
        engine="openclaw",
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )

    assert published.status == "CONVERGED"
    assert not target.is_symlink()


def test_best_effort_preserves_bounded_source_safety_and_availability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home/admin"
    layout = _Layout.for_engine("openclaw", home)
    layout.active_root.mkdir(parents=True)
    unreadable = layout.pool_local / "unreadable"
    unreadable.mkdir(parents=True)
    ordinary_file = layout.pool_local / "ordinary-file"
    ordinary_file.write_text("not a directory")
    outside = tmp_path / "outside"
    outside.mkdir()
    escaping = layout.pool_repo / "escaping"
    escaping.parent.mkdir(parents=True)
    escaping.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(os, "access", lambda path, _mode: Path(path) != unreadable)
    result = publish_pool_mappings(
        mappings=[
            SkillMapping(str(unreadable), str(layout.active_root / "unreadable")),
            SkillMapping(str(ordinary_file), str(layout.active_root / "ordinary-file")),
            SkillMapping(str(escaping), str(layout.active_root / "escaping")),
        ],
        home=home,
        engine="openclaw",
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )

    assert result.item_for(target=layout.active_root / "unreadable").status == "PENDING"
    assert (
        result.item_for(target=layout.active_root / "unreadable").code
        == "MANAGED_SOURCE_MISSING"
    )
    assert (
        result.item_for(target=layout.active_root / "ordinary-file").code
        == "SOURCE_NOT_DIRECTORY"
    )
    assert (
        result.item_for(target=layout.active_root / "escaping").code
        == "SOURCE_ESCAPES_POOL"
    )


def test_best_effort_reports_invalid_requested_mappings_without_touching_safe_ones(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    layout = _Layout.for_engine("openclaw", home)
    layout.active_root.mkdir(parents=True)
    first = layout.pool_local / "first"
    second = layout.pool_local / "second"
    outside = tmp_path / "outside"
    for source in (first, second, outside):
        source.mkdir(parents=True)

    result = publish_pool_mappings(
        mappings=[
            SkillMapping("relative", str(layout.active_root / "relative")),
            SkillMapping(str(first), str(tmp_path / "wrong-root")),
            SkillMapping(str(outside), str(layout.active_root / "outside")),
            SkillMapping(str(first), str(layout.active_root / "duplicate")),
            SkillMapping(str(second), str(layout.active_root / "duplicate")),
        ],
        home=home,
        engine="openclaw",
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )

    assert result.status == "DEGRADED"
    assert {
        item.code for item in result.items
    } >= {"SOURCE_OUTSIDE_POOL", "TARGET_INVALID", "MANAGED_SOURCE_CONFLICT"}
    assert not (layout.active_root / "relative").exists()
    assert not (layout.active_root / "duplicate").exists()


def test_best_effort_verify_and_retire_preserve_unmanaged_and_report_pending(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    layout = _Layout.for_engine("claude_code", home)
    layout.active_root.mkdir(parents=True)
    ready_source = layout.pool_local / "ready"
    old_source = layout.pool_local / "old"
    missing_source = layout.pool_local / "missing"
    other_source = layout.pool_local / "other"
    for source in (ready_source, old_source, other_source):
        source.mkdir(parents=True)

    missing_target = layout.active_root / "missing"
    missing_target.symlink_to(missing_source, target_is_directory=True)
    mismatch_target = layout.active_root / "mismatch"
    mismatch_target.symlink_to(other_source, target_is_directory=True)
    occupied_target = layout.active_root / "occupied"
    occupied_target.mkdir()
    external_source = tmp_path / "external"
    external_source.mkdir()
    external_target = layout.active_root / "external"
    external_target.symlink_to(external_source, target_is_directory=True)
    retired_directory = layout.active_root / "retired-directory"
    retired_directory.mkdir()

    verified = verify_skill_mappings(
        mappings=[
            SkillMapping(str(ready_source), str(layout.active_root / "not-link")),
            SkillMapping(str(ready_source), str(mismatch_target)),
            SkillMapping(str(missing_source), str(missing_target)),
            SkillMapping(str(ready_source), str(occupied_target)),
            SkillMapping(str(ready_source), str(external_target)),
        ],
        retired_mappings=[SkillMapping(str(old_source), str(retired_directory))],
        home=home,
        engine="claude_code",
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )

    assert verified.status == "DEGRADED"
    assert {item.code for item in verified.items} >= {
        "TARGET_NOT_SYMLINK",
        "TARGET_MISMATCH",
        "MANAGED_SOURCE_MISSING",
        "UNMANAGED_ACTIVE_ENTRY_RETAINED",
        "EXTERNAL_ACTIVE_ENTRY_RETAINED",
    }
    assert next(
        item for item in verified.items if item.target == str(missing_target)
    ).retryable is True
    assert retired_directory.is_dir()


def test_best_effort_updates_managed_link_and_surfaces_unrelated_missing_source(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home" / "admin"
    layout = _Layout.for_engine("hermes", home)
    layout.active_root.mkdir(parents=True)
    old_source = layout.pool_local / "old"
    new_source = layout.pool_local / "new"
    for source in (old_source, new_source):
        source.mkdir(parents=True)
    target = layout.active_root / "replaced"
    target.symlink_to(old_source, target_is_directory=True)
    stale_target = layout.active_root / "stale"
    stale_target.symlink_to(layout.pool_local / "stale", target_is_directory=True)

    result = publish_pool_mappings(
        mappings=[SkillMapping(str(new_source), str(target))],
        home=home,
        engine="hermes",
        apply_mode=MappingApplyMode.BEST_EFFORT,
    )

    assert target.readlink() == new_source
    assert result.status == "PENDING"
    assert result.item_for(target=stale_target).code == "MANAGED_SOURCE_MISSING"


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
