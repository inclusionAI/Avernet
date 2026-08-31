import pytest

from agentclaw.community.core.skills_pool.mapping_intent import (
    build_logical_skill_mappings,
    logical_skill_mappings_from_evidence,
    local_locators_from_evidence,
    mapping_contract_for,
)
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
    RegisteredSkillAsset,
)


def test_builds_logical_intent_without_engine_paths() -> None:
    mappings = build_logical_skill_mappings(
        [
            RegisteredSkillAsset(
                skill_id=1,
                name="writer",
                git_path="local:///legacy/engine-specific/writer",
            ),
            RegisteredSkillAsset(
                skill_id=2,
                name="reviewer",
                git_path="git://business/reviewer",
            ),
        ]
    )

    assert [mapping.to_dict() for mapping in mappings] == [
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
    ]


def test_builds_structured_center_intent_without_runtime_paths() -> None:
    mappings = build_logical_skill_mappings(
        [
            RegisteredSkillAsset(
                skill_id=3,
                name="risk-review",
                git_path="center://2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a",
                skill_uuid="2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a",
                sc_version_number="2026.8.19",
            )
        ]
    )

    assert [mapping.to_dict() for mapping in mappings] == [
        {
            "corpus": "center",
            "skill_uuid": "2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a",
            "sc_version_number": "2026.8.19",
            "link_name": "risk-review",
        }
    ]


def test_rejects_center_mapping_without_structured_exact_version() -> None:
    with pytest.raises(ValueError, match="structured identity"):
        build_logical_skill_mappings(
            [
                RegisteredSkillAsset(
                    skill_id=3,
                    name="risk-review",
                    git_path="center://2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a",
                )
            ]
        )


def test_center_mapping_requires_explicit_runtime_v3_capability() -> None:
    mappings = build_logical_skill_mappings(
        [
            RegisteredSkillAsset(
                skill_id=3,
                name="risk-review",
                git_path="center://2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a",
                skill_uuid="2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a",
                sc_version_number="2026.8.19",
            )
        ]
    )

    with pytest.raises(ValueError, match="explicitly support mapping v3"):
        mapping_contract_for(mappings, ["skills-pool-mapping-v2"])

    assert (
        mapping_contract_for(
            mappings,
            ["skills-pool-mapping-v2", "skills-pool-mapping-v3"],
        )
        == "skills-pool-mapping-v3"
    )
    assert (
        mapping_contract_for(
            mappings,
            [
                "skills-pool-mapping-v2",
                "skills-pool-mapping-v3",
                "skills-pool-mapping-v4",
            ],
        )
        == "skills-pool-mapping-v4"
    )


def test_restores_structured_center_retirement_for_retry() -> None:
    assert logical_skill_mappings_from_evidence(
        {
            "retired_mappings": [
                {
                    "corpus": "center",
                    "skill_uuid": "2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a",
                    "sc_version_number": "2026.8.19",
                    "link_name": "risk-review",
                }
            ]
        }
    ) == [
        PoolSkillMapping(
            corpus="center",
            relative_path=None,
            link_name="risk-review",
            skill_uuid="2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a",
            sc_version_number="2026.8.19",
        )
    ]


@pytest.mark.parametrize(
    "git_path",
    [
        "git:///absolute",
        "git://../escape",
        "git://business/../escape",
        "git://business//reviewer",
        "git://business/./reviewer",
        "git://business/reviewer/",
    ],
)
def test_rejects_invalid_logical_relative_path(git_path: str) -> None:
    with pytest.raises(ValueError, match="invalid skill locator"):
        build_logical_skill_mappings(
            [
                RegisteredSkillAsset(
                    skill_id=1,
                    name="reviewer",
                    git_path=git_path,
                )
            ]
        )


def test_rejects_duplicate_active_target() -> None:
    with pytest.raises(ValueError, match="duplicate managed target"):
        build_logical_skill_mappings(
            [
                RegisteredSkillAsset(
                    skill_id=1,
                    name="writer",
                    git_path="local:///legacy/writer",
                ),
                RegisteredSkillAsset(
                    skill_id=2,
                    name="writer",
                    git_path="git://business/writer",
                ),
            ]
        )


def test_repo_runtime_name_uses_skill_name_not_path_tail() -> None:
    with pytest.raises(ValueError, match="duplicate managed target"):
        build_logical_skill_mappings(
            [
                RegisteredSkillAsset(
                    skill_id=1,
                    name="report",
                    git_path="git://ops/weekly-report",
                ),
                RegisteredSkillAsset(
                    skill_id=2,
                    name="report",
                    git_path="git://finance/monthly-report",
                ),
            ]
        )


def test_repo_retirement_evidence_round_trips_when_name_differs_from_locator_tail() -> (
    None
):
    current = build_logical_skill_mappings(
        [
            RegisteredSkillAsset(
                skill_id=1,
                name="incident-review",
                git_path="git://ops/weekly-report",
            )
        ]
    )

    assert (
        logical_skill_mappings_from_evidence(
            {"retired_mappings": [current[0].to_dict()]}
        )
        == current
    )


def test_retirement_evidence_rejects_same_runtime_name_with_different_identities() -> (
    None
):
    with pytest.raises(ValueError, match="ambiguous retired mapping evidence"):
        logical_skill_mappings_from_evidence(
            {
                "retired_mappings": [
                    {
                        "corpus": "repo",
                        "relative_path": "ops/weekly-report",
                        "link_name": "incident-review",
                    },
                    {
                        "corpus": "repo",
                        "relative_path": "finance/monthly-report",
                        "link_name": "incident-review",
                    },
                ]
            }
        )


def test_legacy_retirement_evidence_with_matching_tail_remains_compatible() -> None:
    assert logical_skill_mappings_from_evidence(
        {
            "retired_mappings": [
                {
                    "corpus": "local",
                    "relative_path": "writer",
                    "link_name": "writer",
                }
            ]
        }
    ) == [PoolSkillMapping(corpus="local", relative_path="writer", link_name="writer")]


def test_validates_and_keys_engine_returned_locator_evidence() -> None:
    assets = [
        RegisteredSkillAsset(
            skill_id=7,
            name="writer",
            git_path="local:///legacy/writer",
        )
    ]

    assert local_locators_from_evidence(
        assets,
        ["writer"],
        {"local_locators": {"writer": "local:///runtime/pool/writer"}},
    ) == {7: "local:///runtime/pool/writer"}


def test_reuses_one_engine_locator_for_historical_skill_versions() -> None:
    assets = [
        RegisteredSkillAsset(
            skill_id=7,
            name="writer",
            git_path="local:///legacy/v1/writer",
        ),
        RegisteredSkillAsset(
            skill_id=8,
            name="writer",
            git_path="local:///legacy/v2/writer",
        ),
    ]

    assert local_locators_from_evidence(
        assets,
        ["writer", "writer"],
        {"local_locators": {"writer": "local:///runtime/pool/writer"}},
    ) == {
        7: "local:///runtime/pool/writer",
        8: "local:///runtime/pool/writer",
    }


@pytest.mark.parametrize("evidence", [{}, {"other_runtime_evidence": "kept"}])
def test_accepts_omitted_locator_evidence_when_no_local_skills(
    evidence: dict[str, object],
) -> None:
    """Old runtimes may omit a field that has no values to report."""

    assert local_locators_from_evidence([], [], evidence) == {}


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        {"local_locators": None},
        {"local_locators": []},
        {"local_locators": {"unexpected": "local:///runtime/pool/unexpected"}},
    ],
)
def test_rejects_invalid_locator_evidence_when_no_local_skills(
    evidence: dict[str, object] | None,
) -> None:
    with pytest.raises(ValueError, match="Engine"):
        local_locators_from_evidence([], [], evidence)


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        {},
        {"local_locators": {}},
        {"local_locators": {"writer": "git://writer"}},
        {"local_locators": {"writer": "local://relative/writer"}},
        {"local_locators": {"writer": "local:///runtime/../escape"}},
        {
            "local_locators": {
                "writer": "local:///runtime/pool/writer",
                "extra": "local:///runtime/pool/extra",
            }
        },
    ],
)
def test_rejects_invalid_or_mismatched_locator_evidence(
    evidence: dict[str, object] | None,
) -> None:
    with pytest.raises(ValueError, match="Engine"):
        local_locators_from_evidence(
            [
                RegisteredSkillAsset(
                    skill_id=7,
                    name="writer",
                    git_path="local:///legacy/writer",
                )
            ],
            ["writer"],
            evidence,
        )
