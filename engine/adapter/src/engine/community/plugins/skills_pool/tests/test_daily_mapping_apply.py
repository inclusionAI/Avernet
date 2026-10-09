from pathlib import Path

import pytest

from engine.community.core.skills.exceptions import InvalidPoolMappingRequestError
from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    LayoutIdentity,
    RuntimeLayoutContext,
    resolve_filesystem_skill_layout,
)
from engine.community.plugins.skills_pool.layout_activation import (
    MappingProjectionStatus,
    MappingSourceLayout,
)
from engine.community.plugins.skills_pool.center_content import (
    MountedCenterContentAdapter,
)
from engine.community.plugins.skills_pool.mapping_contract import (
    apply_logical_mapping_payload as _apply_logical_mapping_payload,
)


def apply_logical_mapping_payload(**kwargs):
    kwargs.setdefault("content_adapter", MountedCenterContentAdapter())
    return _apply_logical_mapping_payload(**kwargs)


def _layout(home: Path):
    return resolve_filesystem_skill_layout(
        LayoutIdentity("openclaw", LAYOUT_CONTRACT_VERSION),
        RuntimeLayoutContext(home=home),
    )


def test_retirement_across_active_roots_returns_one_logical_result(tmp_path: Path) -> None:
    historical_root = tmp_path / ".claude_code/workspace/skills"
    retired = {"corpus": "local", "relative_path": "package", "link_name": "writer"}

    result = apply_logical_mapping_payload(
        engine="claude_code", source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[], retired_payload=[retired],
        additional_retirement_roots=[historical_root], home=tmp_path,
    )

    assert result.status is MappingProjectionStatus.CONVERGED
    assert len(result.items) == 1
    assert result.items[0].mapping == retired
    assert result.items[0].action == "RETIRE"


@pytest.mark.parametrize("replacement", [False, True], ids=["retire", "replace"])
@pytest.mark.parametrize("occupied", [False, True], ids=["managed", "user-directory"])
def test_historical_root_outcome_is_aggregated_without_hiding_failures(
    tmp_path: Path, replacement: bool, occupied: bool,
) -> None:
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity("claude_code", LAYOUT_CONTRACT_VERSION),
        RuntimeLayoutContext(home=tmp_path),
    )
    historical_root = tmp_path / ".claude_code/workspace/skills"
    old = {"corpus": "local", "relative_path": "old", "link_name": "writer"}
    new = {"corpus": "local", "relative_path": "new", "link_name": "writer"}
    for package in ("old", "new"):
        (layout.legacy_local / package).mkdir(parents=True)
    layout.active_root.mkdir(parents=True, exist_ok=True)
    current = layout.active_root / "writer"
    historical = historical_root / "writer"
    current.symlink_to(layout.legacy_local / "old", target_is_directory=True)
    if occupied:
        historical.mkdir()
    else:
        historical.symlink_to(layout.legacy_local / "old", target_is_directory=True)

    result = apply_logical_mapping_payload(
        engine="claude_code", source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[new] if replacement else [], retired_payload=[old],
        additional_retirement_roots=[historical_root], home=tmp_path,
    )

    expected = MappingProjectionStatus.DEGRADED if occupied else MappingProjectionStatus.CONVERGED
    assert result.status is expected
    assert len(result.items) == 1
    assert result.items[0].mapping == (new if replacement else old)
    assert result.items[0].action == ("APPLY" if replacement else "RETIRE")
    assert result.items[0].status is expected
    assert len(result.evidence["physical_items"]) == 2
    if occupied:
        assert historical.is_dir()
        assert result.items[0].code == "UNMANAGED_ACTIVE_ENTRY_RETAINED"
    else:
        assert not historical.is_symlink()
    if replacement:
        assert current.readlink() == layout.legacy_local / "new"
    else:
        assert not current.is_symlink()


def test_aggregated_retirement_keeps_pending_retry_alongside_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity("claude_code", LAYOUT_CONTRACT_VERSION),
        RuntimeLayoutContext(home=tmp_path),
    )
    historical_root = tmp_path / ".claude_code/workspace/skills"
    source = layout.legacy_local / "package"
    source.mkdir(parents=True)
    (layout.active_root / "writer").mkdir(parents=True)
    historical = historical_root / "writer"
    historical.symlink_to(source, target_is_directory=True)
    unlink = Path.unlink

    def deny_historical_unlink(path: Path, *args, **kwargs):
        if path == historical:
            raise PermissionError("test denied unlink")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_historical_unlink)
    result = apply_logical_mapping_payload(
        engine="claude_code", source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[],
        retired_payload=[{"corpus": "local", "relative_path": "package", "link_name": "writer"}],
        additional_retirement_roots=[historical_root], home=tmp_path,
    )

    assert len(result.items) == 1
    assert result.items[0].status is MappingProjectionStatus.DEGRADED
    assert result.items[0].retryable is True
    assert {item["status"] for item in result.evidence["physical_items"]} == {"DEGRADED", "PENDING"}
    assert historical.is_symlink()


def test_apply_uses_logical_identity_when_package_name_differs(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    source = layout.legacy_local / "package-on-disk"
    source.mkdir(parents=True)

    result = apply_logical_mapping_payload(
        engine="openclaw",
        source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[
            {
                "corpus": "local",
                "relative_path": "package-on-disk",
                "link_name": "display-name",
            }
        ],
        retired_payload=[],
        home=tmp_path,
    )

    assert result.status is MappingProjectionStatus.CONVERGED
    assert (layout.active_root / "display-name").readlink() == source
    assert result.items[0].mapping == {
        "corpus": "local",
        "relative_path": "package-on-disk",
        "link_name": "display-name",
    }


@pytest.mark.parametrize("engine", ["openclaw", "claude_code"])
def test_unavailable_center_replacement_keeps_old_link_and_applies_other_items(
    tmp_path: Path, engine: str,
) -> None:
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity(engine, LAYOUT_CONTRACT_VERSION), RuntimeLayoutContext(home=tmp_path),
    )
    historical_roots = [tmp_path / ".claude_code/workspace/skills"] if engine == "claude_code" else []
    old_source = layout.pool_center / "00000000-0000-4000-8000-000000000001" / "1"
    old_source.mkdir(parents=True)
    (old_source / "SKILL.md").write_text("old", encoding="utf-8")
    local_source = layout.legacy_local / "writer-package"
    local_source.mkdir(parents=True)
    layout.active_root.mkdir(parents=True, exist_ok=True)
    replacement = layout.active_root / "writer"
    replacement.symlink_to(old_source, target_is_directory=True)
    for root in historical_roots:
        (root / "writer").symlink_to(old_source, target_is_directory=True)

    result = apply_logical_mapping_payload(
        engine=engine,
        source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[
            {
                "corpus": "center",
                "skill_uuid": "00000000-0000-4000-8000-000000000001",
                "sc_version_number": "2",
                "link_name": "writer",
            },
            {
                "corpus": "local",
                "relative_path": "writer-package",
                "link_name": "local-writer",
            },
        ],
        retired_payload=[
            {
                "corpus": "center",
                "skill_uuid": "00000000-0000-4000-8000-000000000001",
                "sc_version_number": "1",
                "link_name": "writer",
            }
        ],
        home=tmp_path,
        additional_retirement_roots=historical_roots,
        content_adapter=MountedCenterContentAdapter(is_mounted=lambda _path: True),
    )

    assert result.status is MappingProjectionStatus.PENDING
    assert replacement.readlink() == old_source
    for root in historical_roots:
        assert (root / "writer").readlink() == old_source
    assert (layout.active_root / "local-writer").readlink() == local_source
    assert any(
        item.mapping
        and item.mapping.get("sc_version_number") == "2"
        and item.action == "APPLY"
        and item.code == "CENTER_VERSION_NOT_FOUND"
        for item in result.items
    )
    assert not any(item.action == "RETIRE" for item in result.items)


def test_empty_intent_does_not_clear_unknown_active_entries(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    user_entry = layout.active_root / "user-owned"
    user_entry.mkdir(parents=True)

    result = apply_logical_mapping_payload(
        engine="openclaw",
        source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[],
        retired_payload=[],
        home=tmp_path,
    )

    assert result.status is MappingProjectionStatus.CONVERGED
    assert user_entry.is_dir()
    assert result.items == ()


def test_conflicting_retired_identities_fail_before_filesystem_mutation(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    first_source = layout.legacy_repo / "old/first"
    second_source = layout.legacy_repo / "old/second"
    first_source.mkdir(parents=True)
    second_source.mkdir(parents=True)
    layout.active_root.mkdir(parents=True, exist_ok=True)
    target = layout.active_root / "shared-name"
    target.symlink_to(second_source, target_is_directory=True)

    with pytest.raises(
        InvalidPoolMappingRequestError, match="duplicate active Skill target"
    ):
        apply_logical_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.LEGACY,
            mappings_payload=[],
            retired_payload=[
                {
                    "corpus": "repo",
                    "relative_path": "old/first",
                    "link_name": "shared-name",
                },
                {
                    "corpus": "repo",
                    "relative_path": "old/second",
                    "link_name": "shared-name",
                },
            ],
            home=tmp_path,
        )

    assert target.readlink() == second_source
