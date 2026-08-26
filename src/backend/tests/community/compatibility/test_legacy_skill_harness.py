"""Q7 legacy Skill regression harness contract.

These tests deliberately construct every source under ``tmp_path``.  They do
not read a developer's workspace, pre/prod database, or an existing bot.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.community.compatibility.legacy_skill_harness import (
    LEGACY_COMPATIBILITY_MATRIX,
    LegacySkillFixtureFactory,
    render_release_report,
)


def test_fixture_factory_creates_isolated_legacy_and_center_sources(tmp_path: Path) -> None:
    fixture = LegacySkillFixtureFactory(tmp_path).create(
        LEGACY_COMPATIBILITY_MATRIX[0]
    )

    assert fixture.repo_skill.read_text() == fixture.local_skill.read_text()
    assert fixture.bot_local_skill.read_text() == fixture.local_skill.read_text()
    assert fixture.center_skill.read_text() == fixture.local_skill.read_text()
    assert fixture.repo_locator == "git://regression/repo-skill"
    assert fixture.local_locator.startswith("local://")
    assert fixture.active_links["repo-skill"].resolve() == fixture.repo_skill.parent
    assert fixture.active_links["local-skill"].resolve() == fixture.local_skill.parent
    assert fixture.active_links["bot-local-skill"].resolve() == fixture.bot_local_skill.parent


def test_fixture_skill_md_is_read_by_the_existing_parser(tmp_path: Path) -> None:
    from agentclaw.community.core.skill_center.services.skill_parser import SkillParser

    fixture = LegacySkillFixtureFactory(tmp_path).create(
        LEGACY_COMPATIBILITY_MATRIX[0]
    )

    projection = SkillParser.parse_content(fixture.local_skill.read_text())
    assert projection is not None
    assert projection["name"] == "regression-skill"
    assert projection["description"] == "Q7 isolated fixture"


@pytest.mark.parametrize("case", LEGACY_COMPATIBILITY_MATRIX, ids=lambda case: case.id)
def test_matrix_preserves_legacy_locators_and_keeps_center_separate(
    tmp_path: Path, case
) -> None:
    fixture = LegacySkillFixtureFactory(tmp_path).create(case)

    fixture.assert_legacy_baseline()
    assert fixture.center_skill.parent.parent.parent.name == "skill-center"
    assert "center://" not in fixture.repo_locator
    assert "center://" not in fixture.local_locator
    assert "center://" not in fixture.bot_local_locator


def test_teclaw_v4_fixture_round_trips_through_the_published_artifact_contract(
    tmp_path: Path,
) -> None:
    from agentclaw.community.kernel.bot_config import BotConfigArtifact

    fixture = LegacySkillFixtureFactory(tmp_path).create(
        next(case for case in LEGACY_COMPATIBILITY_MATRIX if case.is_teclaw_v4)
    )

    assert fixture.teclaw_v4_artifact is not None
    assert BotConfigArtifact.from_dict(
        fixture.teclaw_v4_artifact.to_dict()
    ) == fixture.teclaw_v4_artifact


@pytest.mark.parametrize(
    "case",
    tuple(case for case in LEGACY_COMPATIBILITY_MATRIX if not case.is_teclaw_v4),
    ids=lambda case: case.id,
)
def test_supported_filesystem_matrix_uses_real_skillset_mapping_service(
    tmp_path: Path, case
) -> None:
    """Exercise the public mapping seam twice, as startup/restart does."""
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetService,
    )
    from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset

    fixture = LegacySkillFixtureFactory(tmp_path).create(case)

    class _FixtureReader:
        """Installation is the source of truth: the mapping seam reads the
        capability state reader, so the legacy fixtures are answered as the
        flushed Installation projection."""

        def active_skill_assets(self, *, bot_id, owner_id, bot=None):
            assert (bot_id, owner_id) == ("fixture-bot", "fixture-owner")
            return (
                RegisteredSkillAsset(
                    skill_id=1, name="repo-skill", git_path=fixture.repo_locator
                ),
                RegisteredSkillAsset(
                    skill_id=2, name="local-skill", git_path=fixture.local_locator
                ),
                RegisteredSkillAsset(
                    skill_id=3,
                    name="bot-local-skill",
                    git_path=fixture.bot_local_locator,
                ),
            )

    service = SkillSetService(
        skill_repo=MagicMock(),
        skill_set_repo=MagicMock(),
        mcp_center=MagicMock(),
        mcp_config_service=MagicMock(),
        skill_service=MagicMock(),
        bot_repo=MagicMock(),
        path_factory=MagicMock(),
        entity_id="fixture-owner",
        bot_id="fixture-bot",
        engine_type="aicoding" if case.image == "aicoding" else case.engine,
        reader=_FixtureReader(),
    )
    active = fixture.root / "active-skills"
    service._pool_layout_paths = lambda *_: (
        str(active),
        str(fixture.root / "skills-local"),
        str(fixture.root / "skills-repo"),
    )

    startup = service.get_symlink_mappings(user_id="fixture-owner", bolt_id="fixture-bot")
    restart = service.get_symlink_mappings(user_id="fixture-owner", bolt_id="fixture-bot")

    assert [mapping.to_dict() for mapping in restart] == [
        mapping.to_dict() for mapping in startup
    ]
    assert {mapping.source for mapping in startup} == {
        str(fixture.repo_skill.parent),
        str(fixture.local_skill.parent),
        str(fixture.root / "skills-local" / "bot-local-skill"),
    }
    assert all("center" not in mapping.source for mapping in startup)


def test_matrix_covers_only_final_spec_supported_bot_engine_combinations() -> None:
    assert {case.bot_type for case in LEGACY_COMPATIBILITY_MATRIX} == {
        "personal",
        "desktop",
        "service",
    }
    assert {(case.bot_type, case.engine, case.image) for case in LEGACY_COMPATIBILITY_MATRIX} == {
        ("personal", "openclaw", "native"),
        ("personal", "claude_code", "native"),
        ("personal", "claude_code", "aicoding"),
        ("personal", "hermes", "native"),
        ("personal", "teclaw", "v4"),
        ("desktop", "openclaw", "native"),
        ("desktop", "hermes", "native"),
        ("service", "openclaw", "native"),
        ("service", "claude_code", "native"),
        ("service", "claude_code", "aicoding"),
        ("service", "teclaw", "v4"),
    }


def test_release_report_marks_missing_evidence_as_a_publish_blocker(tmp_path: Path) -> None:
    fixture = LegacySkillFixtureFactory(tmp_path).create(
        LEGACY_COMPATIBILITY_MATRIX[0]
    )

    report = render_release_report(
        results={fixture.case.id: "passed"},
        blocked={LEGACY_COMPATIBILITY_MATRIX[1].id: "not run"},
    )

    assert "发布结论：阻断" in report
    assert LEGACY_COMPATIBILITY_MATRIX[1].id in report


def test_release_report_passes_only_when_every_matrix_cell_passes() -> None:
    report = render_release_report(
        results={case.id: "passed" for case in LEGACY_COMPATIBILITY_MATRIX},
        blocked={},
    )

    assert "发布结论：通过" in report
