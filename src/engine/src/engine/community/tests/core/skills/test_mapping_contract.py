from pathlib import Path

import pytest

from engine.community.core.skills.layout_planner import (
    MAPPING_CONTRACT_VERSION,
)
from engine.community.plugins.skills_pool.layout_activation import (
    MappingSourceLayout,
)
from engine.community.plugins.skills_pool.mapping_contract import (
    resolve_mapping_payload,
)


def _snapshot(root: Path) -> list[Path]:
    return sorted(path.relative_to(root) for path in root.rglob("*"))


def test_logical_mapping_projects_desktop_claude_paths_and_locator_evidence(
    tmp_path: Path,
) -> None:
    resolved = resolve_mapping_payload(
        engine="claude_code",
        source_layout=MappingSourceLayout.POOL,
        mapping_contract_version=MAPPING_CONTRACT_VERSION,
        payload=[
            {
                "corpus": "local",
                "relative_path": "writer",
                "link_name": "writer",
            },
            {
                "corpus": "repo",
                "relative_path": "business/reviewer",
                "link_name": "reviewer",
            },
        ],
        home=tmp_path,
    )

    assert resolved.mappings[0].source == str(
        tmp_path / ".claude_code/workspace/skills-pool/skills-local/writer"
    )
    assert resolved.mappings[1].target == str(
        tmp_path / ".claude/skills/reviewer"
    )
    assert resolved.resolved_locators == (
        {
            "corpus": "local",
            "relative_path": "writer",
            "link_name": "writer",
            "resolved_locator": (
                f"local://{tmp_path}/"
                ".claude_code/workspace/skills-pool/skills-local/writer"
            ),
        },
        {
            "corpus": "repo",
            "relative_path": "business/reviewer",
            "link_name": "reviewer",
            "resolved_locator": "git://business/reviewer",
        },
    )


@pytest.mark.parametrize(
    ("engine", "active_root"),
    [
        ("openclaw", ".openclaw/workspace/skills"),
        ("claude_code", ".claude/skills"),
        ("aicoding", ".claude/skills"),
        ("hermes", ".hermes/skills"),
    ],
)
def test_mapping_v2_resolves_for_every_filesystem_engine(
    tmp_path: Path,
    engine: str,
    active_root: str,
) -> None:
    resolved = resolve_mapping_payload(
        engine=engine,
        source_layout=MappingSourceLayout.POOL,
        mapping_contract_version=MAPPING_CONTRACT_VERSION,
        payload=[
            {
                "corpus": "repo",
                "relative_path": "business/reviewer",
                "link_name": "reviewer",
            }
        ],
        home=tmp_path,
    )

    assert resolved.mappings[0].target == str(
        tmp_path / active_root / "reviewer"
    )
    assert (
        resolved.resolved_locators[0]["resolved_locator"]
        == "git://business/reviewer"
    )


@pytest.mark.parametrize(
    ("version", "payload", "message"),
    [
        (
            "skills-pool-mapping-future-v3",
            [
                {
                    "corpus": "local",
                    "relative_path": "writer",
                    "link_name": "writer",
                }
            ],
            "unsupported mapping contract",
        ),
        (
            None,
            [
                {
                    "corpus": "local",
                    "relative_path": "writer",
                    "link_name": "writer",
                }
            ],
            "legacy mapping",
        ),
        (
            MAPPING_CONTRACT_VERSION,
            [{"source": "/pool/writer", "target": "/active/writer"}],
            "logical mapping",
        ),
        (
            MAPPING_CONTRACT_VERSION,
            [
                {
                    "corpus": "local",
                    "relative_path": "writer",
                    "link_name": "writer",
                    "source": "/pool/writer",
                    "target": "/active/writer",
                }
            ],
            "logical mapping",
        ),
    ],
)
def test_invalid_contract_shape_has_no_filesystem_side_effect(
    tmp_path: Path,
    version: str | None,
    payload: list[dict[str, str]],
    message: str,
) -> None:
    before = _snapshot(tmp_path)

    with pytest.raises((TypeError, ValueError), match=message):
        resolve_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.POOL,
            mapping_contract_version=version,
            payload=payload,
            home=tmp_path,
        )

    assert _snapshot(tmp_path) == before


def test_legacy_no_version_physical_payload_remains_compatible(
    tmp_path: Path,
) -> None:
    resolved = resolve_mapping_payload(
        engine="openclaw",
        source_layout=MappingSourceLayout.POOL,
        mapping_contract_version=None,
        payload=[{"source": "/pool/writer", "target": "/active/writer"}],
        home=tmp_path,
    )

    assert resolved.mappings[0].source == "/pool/writer"
    assert resolved.mappings[0].target == "/active/writer"
    assert resolved.resolved_locators == ()
    assert _snapshot(tmp_path) == []
