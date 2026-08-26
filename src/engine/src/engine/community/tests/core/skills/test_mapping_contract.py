from pathlib import Path

import pytest
from engine.community.core.skills.exceptions import (
    InvalidPoolMappingRequestError,
)
from engine.community.core.skills.layout_planner import (
    MAPPING_CONTRACT_VERSION,
    MAPPING_V3_CONTRACT_VERSION,
    SkillLayoutResolutionError,
)
from engine.community.plugins.claude_code.layout_pool import (
    claude_code_retirement_active_roots,
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
    ("engine", "active_root", "center_root"),
    [
        ("openclaw", ".openclaw/workspace/skills", ".openclaw/workspace/skills-pool/skill-center"),
        ("claude_code", ".claude/skills", ".claude_code/workspace/skills-pool/skill-center"),
        ("aicoding", ".claude/skills", ".aicoding/workspace/skills-pool/skill-center"),
        ("hermes", ".hermes/skills", ".hermes/workspace/skills-pool/skill-center"),
    ],
)
def test_mapping_v3_projects_structured_center_identity_for_every_runtime(
    tmp_path: Path,
    engine: str,
    active_root: str,
    center_root: str,
) -> None:
    skill_uuid = "2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a"
    resolved = resolve_mapping_payload(
        engine=engine,
        source_layout=MappingSourceLayout.LEGACY,
        mapping_contract_version=MAPPING_V3_CONTRACT_VERSION,
        payload=[
            {
                "corpus": "center",
                "skill_uuid": skill_uuid,
                "sc_version_number": "2026.8.19",
                "link_name": "risk-review",
            }
        ],
        home=tmp_path,
    )

    assert resolved.mappings[0].source == str(
        tmp_path / center_root / skill_uuid / "2026.8.19"
    )
    assert resolved.mappings[0].target == str(tmp_path / active_root / "risk-review")
    assert resolved.resolved_locators == (
        {
            "corpus": "center",
            "skill_uuid": skill_uuid,
            "sc_version_number": "2026.8.19",
            "link_name": "risk-review",
            "resolved_locator": f"center://{skill_uuid}/2026.8.19",
        },
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

    with pytest.raises(InvalidPoolMappingRequestError, match=message):
        resolve_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.POOL,
            mapping_contract_version=version,
            payload=payload,
            home=tmp_path,
        )

    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "version,payload,message",
    [
        (MAPPING_V3_CONTRACT_VERSION, ["not-an-object"], "must be an object"),
        (MAPPING_CONTRACT_VERSION, [{"corpus": "center", "skill_uuid": "u", "sc_version_number": "1", "link_name": "a"}], "v2 logical"),
        (MAPPING_V3_CONTRACT_VERSION, [{"corpus": "repo", "skill_uuid": "u", "sc_version_number": "1", "link_name": "a"}], "structured"),
        (MAPPING_V3_CONTRACT_VERSION, [{"corpus": "center", "relative_path": "x", "link_name": "a"}], "logical mapping"),
    ],
)
def test_mapping_contract_rejects_invalid_v3_shapes_before_resolution(
    tmp_path: Path,
    version: str,
    payload: list[object],
    message: str,
) -> None:
    with pytest.raises(InvalidPoolMappingRequestError, match=message):
        resolve_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.POOL,
            mapping_contract_version=version,
            payload=payload,
            home=tmp_path,
        )


def test_internal_layout_resolution_error_is_not_reclassified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.community.plugins.skills_pool import mapping_contract

    def fail_layout_resolution(*args: object, **kwargs: object) -> None:
        raise SkillLayoutResolutionError("descriptor invariant failed")

    monkeypatch.setattr(
        mapping_contract,
        "resolve_filesystem_skill_layout",
        fail_layout_resolution,
    )

    with pytest.raises(
        SkillLayoutResolutionError,
        match="descriptor invariant failed",
    ) as error:
        resolve_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.POOL,
            mapping_contract_version=MAPPING_CONTRACT_VERSION,
            payload=[],
            home=tmp_path,
        )

    assert not isinstance(error.value, InvalidPoolMappingRequestError)


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


def test_claude_code_retired_legacy_mapping_resolves_both_managed_active_roots(
    tmp_path: Path,
) -> None:
    mapping = {
        "corpus": "local",
        "relative_path": "financial-data-query",
        "link_name": "financial-data-query",
    }

    resolved = resolve_mapping_payload(
        engine="claude_code",
        source_layout=MappingSourceLayout.LEGACY,
        payload=[mapping],
        mapping_contract_version=MAPPING_CONTRACT_VERSION,
        additional_retirement_roots=claude_code_retirement_active_roots(
            home=tmp_path
        ),
        home=tmp_path,
    )

    assert [item.target for item in resolved.mappings] == [
        str(tmp_path / ".claude/skills/financial-data-query"),
        str(tmp_path / ".claude_code/workspace/skills/financial-data-query"),
    ]
