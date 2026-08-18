"""Contract tests for the canonical SKILL.md metadata parser."""

import json
from pathlib import Path

import pytest

from agentclaw.community.core.skill_center.skill_metadata import (
    SkillMetadataProjection,
    SkillMetadataErrorCode,
    SkillMetadataValidationError,
)
from agentclaw.community.core.skill_center.services.skill_parser import (
    LegacySkillParserAdapter,
    SkillParser,
)

_FIXTURE_ROOT = (
    Path(__file__).parents[4] / "community" / "fixtures" / "skill_metadata"
)


def test_parse_skill_markdown_reads_name_and_description_from_frontmatter() -> None:
    metadata = SkillParser.parse_skill_markdown(
        "---\n"
        "name: release-notes\n"
        "description: Generates concise release notes.\n"
        "---\n"
        "# Release notes\n"
        "\n"
        "Turn merged changes into a publishable summary.\n"
    )

    assert metadata.name == "release-notes"
    assert metadata.description == "Generates concise release notes."


def test_validate_skill_markdown_returns_a_stable_error_for_missing_name() -> None:
    result = SkillParser.validate_skill_markdown(
        "---\n"
        "description: Generates concise release notes.\n"
        "---\n"
        "# Release notes\n"
    )

    assert result.metadata is None
    assert result.errors[0].code is SkillMetadataErrorCode.MISSING_NAME
    assert result.errors[0].field == "name"


@pytest.mark.parametrize(
    "reader",
    [SkillParser.parse_skill_markdown, SkillParser.project_skill_markdown],
)
def test_strict_public_readers_raise_the_same_stable_error_for_invalid_content(
    reader,
) -> None:
    with pytest.raises(SkillMetadataValidationError) as error:
        reader("---\ndescription: Generates release notes.\n---\n# Release notes\n")

    assert error.value.issue.code is SkillMetadataErrorCode.MISSING_NAME
    assert error.value.issue.field == "name"


def test_validate_skill_markdown_rejects_non_utf8_content() -> None:
    result = SkillParser.validate_skill_markdown(
        bytes.fromhex((_FIXTURE_ROOT / "invalid-utf8.hex").read_text())
    )

    assert result.metadata is None
    assert result.errors[0].code is SkillMetadataErrorCode.INVALID_ENCODING
    assert result.errors[0].field == "content"


def test_validate_skill_markdown_rejects_a_noncanonical_manifest_path() -> None:
    result = SkillParser.validate_skill_markdown(
        "---\nname: release-notes\ndescription: Generates release notes.\n---\n# Release notes\n",
        path=(_FIXTURE_ROOT / "invalid-path.txt").read_text().strip(),
    )

    assert result.metadata is None
    assert result.errors[0].code is SkillMetadataErrorCode.INVALID_PATH
    assert result.errors[0].field == "path"


def test_validate_skill_markdown_rejects_invalid_frontmatter() -> None:
    result = SkillParser.validate_skill_markdown(
        "---\nname: [unterminated\n---\n# Release notes\n"
    )

    assert result.metadata is None
    assert result.errors[0].code is SkillMetadataErrorCode.INVALID_FRONTMATTER


def test_validate_skill_markdown_rejects_non_string_required_values() -> None:
    result = SkillParser.validate_skill_markdown(
        "---\nname: 42\ndescription: Generates release notes.\n---\n# Release notes\n"
    )

    assert result.metadata is None
    assert result.errors[0].code is SkillMetadataErrorCode.INVALID_NAME
    assert result.errors[0].field == "name"


def test_validate_skill_markdown_rejects_an_empty_body() -> None:
    result = SkillParser.validate_skill_markdown(
        "---\nname: release-notes\ndescription: Generates release notes.\n---\n"
    )

    assert result.metadata is None
    assert result.errors[0].code is SkillMetadataErrorCode.MISSING_BODY
    assert result.errors[0].field == "body"


def test_validate_skill_markdown_enforces_name_and_description_length_limits() -> None:
    limits = json.loads((_FIXTURE_ROOT / "boundary-limits.json").read_text())
    name_limit = limits["name_max_length"]
    description_limit = limits["description_max_utf8_bytes"]
    at_name_limit = SkillParser.validate_skill_markdown(
        f"---\nname: {'n' * name_limit}\ndescription: Short description.\n---\n# Title\n"
    )
    at_description_limit = SkillParser.validate_skill_markdown(
        f"---\nname: release-notes\ndescription: {'d' * description_limit}\n---\n# Title\n"
    )
    name_too_long = SkillParser.validate_skill_markdown(
        f"---\nname: {'n' * (name_limit + 1)}\ndescription: Short description.\n---\n# Title\n"
    )
    description_too_long = SkillParser.validate_skill_markdown(
        f"---\nname: release-notes\ndescription: {'d' * (description_limit + 1)}\n---\n# Title\n"
    )

    assert at_name_limit.is_valid
    assert at_description_limit.is_valid
    assert name_too_long.errors[0].code is SkillMetadataErrorCode.NAME_TOO_LONG
    assert name_too_long.errors[0].field == "name"
    assert (
        description_too_long.errors[0].code
        is SkillMetadataErrorCode.DESCRIPTION_TOO_LONG
    )
    assert description_too_long.errors[0].field == "description"


def test_metadata_projection_exposes_only_the_manifest_name_and_description() -> None:
    metadata = SkillParser.parse_skill_markdown(
        "---\nname: release-notes\ndescription: Generates release notes.\n---\n# Title\n"
    )

    projection = SkillMetadataProjection.from_metadata(metadata)

    assert projection.to_dict() == {
        "name": "release-notes",
        "description": "Generates release notes.",
    }


def test_project_skill_markdown_is_the_public_read_projection_api() -> None:
    projection = SkillParser.project_skill_markdown(
        "---\nname: release-notes\ndescription: Generates release notes.\n---\n# Title\n"
    )

    assert projection == SkillMetadataProjection(
        name="release-notes", description="Generates release notes."
    )


def test_legacy_read_entry_points_use_the_canonical_projection_for_valid_skill_md(
    tmp_path,
) -> None:
    content = (
        "---\n"
        'name: " release-notes "\n'
        'description: " Generates release notes. "\n'
        "---\n"
        "# Title\n"
    )
    skill_dir = tmp_path / "release-notes"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    expected = SkillParser.project_skill_markdown(content).to_dict()
    content_result = LegacySkillParserAdapter.parse_content(content)
    file_result = LegacySkillParserAdapter.parse(skill_dir)

    assert {key: content_result[key] for key in expected} == expected
    assert {key: file_result[key] for key in expected} == expected


@pytest.mark.parametrize(
    ("fixture_name", "expected_code"),
    [
        ("missing-name", SkillMetadataErrorCode.MISSING_NAME),
        ("missing-frontmatter", SkillMetadataErrorCode.MISSING_FRONTMATTER),
        ("invalid-frontmatter", SkillMetadataErrorCode.INVALID_FRONTMATTER),
        ("empty-body", SkillMetadataErrorCode.MISSING_BODY),
    ],
)
def test_shared_skill_metadata_fixtures_preserve_validation_contract(
    fixture_name: str, expected_code: SkillMetadataErrorCode
) -> None:
    content = (_FIXTURE_ROOT / fixture_name / "SKILL.md").read_bytes()

    result = SkillParser.validate_skill_markdown(content)

    assert result.errors[0].code is expected_code


def test_shared_valid_skill_metadata_fixture_projects_name_and_description() -> None:
    content = (_FIXTURE_ROOT / "valid" / "SKILL.md").read_bytes()

    projection = SkillParser.project_skill_markdown(content)

    assert projection.to_dict() == {
        "name": "release-notes",
        "description": "Generates concise release notes.",
    }
