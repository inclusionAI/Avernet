import pytest

from agentclaw.community.core.skills_pool.mapping_intent import (
    build_logical_skill_mappings,
    local_locators_from_evidence,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset


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
