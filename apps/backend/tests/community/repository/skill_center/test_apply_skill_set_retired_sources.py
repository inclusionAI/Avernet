"""Unavailable member cleanup uses recorded Set ownership, never raw alias guesses."""

import pytest

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.skill_installation_source import SkillInstallationSourceKind
from agentclaw.community.core.repository.implementations.skill_center.tables import skill_installations
from agentclaw.community.core.skill_center.skill_set_apply import DefaultExclusionChange
from .test_apply_center_membership_identity import SCOPE, seed, snapshot


@pytest.mark.parametrize("unavailable", ["unpublished", "missing_uuid"])
@pytest.mark.parametrize("origin", ["default", "direct", "another_set", "unknown"])
def test_unavailable_default_cleanup_only_retires_its_recorded_installation(unavailable, origin):
    db, repo = seed(missing_uuid=unavailable == "missing_uuid")
    with db.transactional_orm_session() as session:
        if unavailable == "unpublished":
            session.get(Skill, 1).status = "DRAFT"
        kind = {
            "default": SkillInstallationSourceKind.DEFAULT,
            "direct": SkillInstallationSourceKind.DIRECT,
            "another_set": SkillInstallationSourceKind.DEFAULT,
            "unknown": None,
        }[origin]
        skill_installations.install(
            session, bot_id="bot", owner_id="owner", env="dev", skill_id=1,
            source_kind=kind, source_set_id=10 if origin == "default" else 20 if origin == "another_set" else None,
        )
    result = repo.replace_apply_skill_sets(
        scope=SCOPE, sets=(), remove_names=(),
        default_change=DefaultExclusionChange(add_skills=frozenset({(10, 999)})),
        platform_default_codes=frozenset(), mcp_catalog_entries={},
    )
    assert result.changed
    assert snapshot(repo).skill_exclusions == frozenset({(10, 999)})
    assert snapshot(repo).installations == (set() if origin == "default" else {1})
