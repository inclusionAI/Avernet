from pathlib import Path

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
from engine.community.plugins.skills_pool.mapping_contract import (
    apply_logical_mapping_payload,
)


def _layout(home: Path):
    return resolve_filesystem_skill_layout(
        LayoutIdentity("openclaw", LAYOUT_CONTRACT_VERSION),
        RuntimeLayoutContext(home=home),
    )


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


def test_unavailable_center_replacement_keeps_old_link_and_applies_other_items(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    old_source = layout.pool_center / "00000000-0000-4000-8000-000000000001" / "1"
    old_source.mkdir(parents=True)
    (old_source / "SKILL.md").write_text("old", encoding="utf-8")
    local_source = layout.legacy_local / "writer-package"
    local_source.mkdir(parents=True)
    layout.active_root.mkdir(parents=True, exist_ok=True)
    replacement = layout.active_root / "writer"
    replacement.symlink_to(old_source, target_is_directory=True)

    result = apply_logical_mapping_payload(
        engine="openclaw",
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
        center_is_mounted=lambda _path: True,
    )

    assert result.status is MappingProjectionStatus.PENDING
    assert replacement.readlink() == old_source
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
