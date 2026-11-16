"""Default releases stable Center ownership by the association's raw exclusion key."""

from datetime import datetime

import pytest

from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.models.skill_installation_source import SkillInstallationSourceKind
from agentclaw.community.core.repository.implementations.skill_center.tables import skill_installations
from agentclaw.community.core.skill_center.errors import SkillSetControlPlaneConflictError
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange, ResolvedSkillSetWrite,
)
from .test_apply_center_membership_identity import SCOPE, seed, snapshot


@pytest.mark.parametrize("excluded", [True, False])
@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("direct", [True, False])
def test_ordinary_set_join_uses_raw_default_exclusion_and_retains_direct_guard(excluded, active, direct):
    db, repo = seed(excluded=excluded)
    if direct:
        with db.transactional_orm_session() as session:
            skill_installations.install(
                session, bot_id="bot", owner_id="owner", env="dev", skill_id=1,
                source_kind=SkillInstallationSourceKind.DIRECT,
            )
    before = snapshot(repo)
    kwargs = dict(
        scope=SCOPE, sets=(ResolvedSkillSetWrite("expert", is_active=active, skill_ids=(1,)),),
        remove_names=(), default_change=DefaultExclusionChange(),
        platform_default_codes=frozenset(), mcp_catalog_entries={},
    )
    if not excluded or direct:
        reason = "RESOURCE_DIRECT_ACTIVE" if direct and excluded else "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
        with pytest.raises(SkillSetControlPlaneConflictError, match=reason):
            repo.replace_apply_skill_sets(**kwargs)
        assert snapshot(repo) == before
    else:
        result = repo.replace_apply_skill_sets(**kwargs)
        assert result.details["created"] == ("expert",)
        after = snapshot(repo)
        assert after.skill_exclusions == before.skill_exclusions == frozenset({(10, 999)})
        assert after.installations == ({1} if active else set())


def test_default_restore_ignores_another_centers_colliding_raw_alias():
    db, repo = seed(excluded=True)
    other_uuid = "00000000-0000-4000-8000-000000000002"
    with db.transactional_orm_session() as session:
        session.add(Skill(
            id=2, name="other", git_path="center://other", skill_uuid=other_uuid,
            status="PUBLISHED", version=1, env="dev",
        ))
        session.add(SkillVersion(
            skill_id=2, version_ordinal=1, status="PUBLISHED", sc_version_number="1.0",
            sc_skill_id=8, sc_version_id=11, name="other", metadata_json='{"mcp_dependencies":[]}',
            published_at=datetime(2026, 9, 1), created_by="owner", env="dev",
        ))
        session.add(SkillSet(
            id=20, name="retained", is_active=False, user_id="owner", bolt_id="bot",
            engine_type="openclaw", env="dev",
        ))
        session.add(SkillSetSkill(skill_set_id=20, skill_id=1, skill_uuid=other_uuid, env="dev"))
    result = repo.replace_apply_skill_sets(
        scope=SCOPE, sets=(), remove_names=(),
        default_change=DefaultExclusionChange(remove_skills=frozenset({(10, 999)})),
        platform_default_codes=frozenset(), mcp_catalog_entries={},
    )
    assert result.changed
    after = snapshot(repo)
    assert after.installations == {1}
    assert after.skill_exclusions == frozenset()
    assert after.set_active[20] is False
