"""Public contract tests for canonical ``SKILL.md`` metadata parsing."""

import json
from pathlib import Path

import pytest

from agentclaw.community.api.skill_metadata_parser import (
    SkillMetadataParserProtocol,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_metadata import (
    SkillManifestErrorCode,
    SkillMetadata,
)


_FIXTURES = Path(__file__).parents[4] / "community" / "fixtures" / "skill_metadata"


def test_parser_protocol_returns_an_immutable_metadata_value() -> None:
    parser: SkillMetadataParserProtocol = SkillParser()

    metadata = parser.parse_skill_markdown(
        b"---\n"
        b"name: release-notes\n"
        b"description: Generates concise release notes.\n"
        b"---\n"
        b"# Body title must not replace manifest metadata.\n"
    )

    assert metadata == SkillMetadata(
        name="release-notes",
        description="Generates concise release notes.",
    )
    assert isinstance(parser, SkillMetadataParserProtocol)


def test_protocol_validation_reports_a_stable_missing_name_error() -> None:
    parser: SkillMetadataParserProtocol = SkillParser()

    result = parser.validate_skill_markdown(
        "---\ndescription: Missing its authoritative name.\n---\n"
    )

    assert result.metadata is None
    assert result.errors[0].code is SkillManifestErrorCode.MISSING_NAME
    assert result.errors[0].field == "name"


def test_protocol_accepts_nested_skill_path_and_rejects_noncanonical_paths() -> None:
    parser: SkillMetadataParserProtocol = SkillParser()
    content = "---\nname: release-notes\ndescription: Generates notes.\n---\n"

    assert parser.validate_skill_markdown(
        content, path="packages/release-notes/SKILL.md"
    ).is_valid

    for path in ("README.md", "../SKILL.md", "/release-notes/SKILL.md"):
        result = parser.validate_skill_markdown(content, path=path)
        assert result.errors[0].code is SkillManifestErrorCode.INVALID_PATH
        assert result.errors[0].field == "path"


def test_protocol_enforces_persistence_safe_metadata_boundaries() -> None:
    parser: SkillMetadataParserProtocol = SkillParser()
    limits = json.loads((_FIXTURES / "boundary-limits.json").read_text())
    name_limit = limits["name_max_length"]
    description_limit = limits["description_max_utf8_bytes"]

    assert parser.validate_skill_markdown(
        f"---\nname: {'n' * name_limit}\ndescription: valid\n---\n"
    ).is_valid
    assert parser.validate_skill_markdown(
        f"---\nname: valid\ndescription: {'字' * (description_limit // 3)}\n---\n"
    ).is_valid

    name_error = parser.validate_skill_markdown(
        f"---\nname: {'n' * (name_limit + 1)}\ndescription: valid\n---\n"
    )
    description_error = parser.validate_skill_markdown(
        f"---\nname: valid\ndescription: {'字' * (description_limit // 3 + 1)}\n---\n"
    )
    assert name_error.errors[0].code is SkillManifestErrorCode.NAME_TOO_LONG
    assert description_error.errors[0].code is (
        SkillManifestErrorCode.DESCRIPTION_TOO_LONG
    )


@pytest.mark.parametrize(
    ("fixture", "expected_code"),
    [
        ("missing-frontmatter", SkillManifestErrorCode.MISSING_FRONTMATTER),
        ("missing-name", SkillManifestErrorCode.MISSING_NAME),
        ("invalid-frontmatter", SkillManifestErrorCode.INVALID_FRONTMATTER),
    ],
)
def test_shared_fixtures_pin_stable_errors(
    fixture: str, expected_code: SkillManifestErrorCode
) -> None:
    parser: SkillMetadataParserProtocol = SkillParser()

    result = parser.validate_skill_markdown(
        (_FIXTURES / fixture / "SKILL.md").read_bytes()
    )

    assert result.errors[0].code is expected_code


def test_shared_valid_fixture_uses_only_frontmatter_metadata() -> None:
    parser: SkillMetadataParserProtocol = SkillParser()

    metadata = parser.parse_skill_markdown(
        (_FIXTURES / "valid" / "SKILL.md").read_bytes()
    )

    assert metadata.to_dict() == {
        "name": "release-notes",
        "description": "Generates concise release notes.",
    }


def test_shared_encoding_and_path_fixtures_pin_stable_errors() -> None:
    parser: SkillMetadataParserProtocol = SkillParser()
    invalid_bytes = bytes.fromhex((_FIXTURES / "invalid-encoding.hex").read_text())
    invalid_path = (_FIXTURES / "invalid-path.txt").read_text().strip()

    encoding_result = parser.validate_skill_markdown(invalid_bytes)
    path_result = parser.validate_skill_markdown(
        (_FIXTURES / "valid" / "SKILL.md").read_bytes(), path=invalid_path
    )

    assert encoding_result.errors[0].code is SkillManifestErrorCode.INVALID_ENCODING
    assert encoding_result.errors[0].field is None
    assert path_result.errors[0].code is SkillManifestErrorCode.INVALID_PATH
    assert path_result.errors[0].field == "path"
