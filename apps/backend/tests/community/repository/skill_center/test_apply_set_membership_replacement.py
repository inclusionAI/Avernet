"""Set replacement compares stable identities rather than historical raw keys."""

from datetime import datetime

import pytest

from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from .test_apply_center_membership_identity import UUID, SCOPE, seed, snapshot


OTHER_UUID = "00000000-0000-4000-8000-000000000002"


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize(
    "raw_id,stored_uuid,changed",
    [(1, OTHER_UUID, True), (999, UUID, False), (1, UUID, False)],
    ids=["raw-collision", "same-uuid-stale-raw", "same-identity"],
)
def test_replacement_converges_by_stable_identity(active, raw_id, stored_uuid, changed):
    db, repo = seed()
    with db.transactional_orm_session() as session:
        session.query(SkillSetSkill).delete()
        session.query(SkillSet).delete()
        session.add_all(
            [
                Skill(
                    id=2,
                    name="other",
                    git_path="center://other",
                    skill_uuid=OTHER_UUID,
                    status="PUBLISHED",
                    version=1,
                    is_public=True,
                    env="dev",
                ),
                SkillVersion(
                    id=102,
                    skill_id=2,
                    version_ordinal=1,
                    status="PUBLISHED",
                    sc_version_number="1.0",
                    sc_skill_id=8,
                    sc_version_id=11,
                    name="other",
                    description="Other published asset",
                    metadata_json='{"mcp_dependencies":[]}',
                    published_at=datetime(2026, 9, 1),
                    created_by="owner",
                    env="dev",
                ),
                SkillSet(
                    id=20,
                    name="expert",
                    is_default=False,
                    is_active=active,
                    user_id="owner",
                    bolt_id="bot",
                    engine_type="openclaw",
                    env="dev",
                ),
                SkillSetSkill(
                    skill_set_id=20, skill_id=raw_id, skill_uuid=stored_uuid, env="dev"
                ),
            ]
        )
    repo.flush_installations(
        bot_id="bot", owner_id="owner", engine_type="openclaw", env="dev"
    )

    def apply():
        return repo.replace_apply_skill_sets(
            scope=SCOPE,
            sets=(ResolvedSkillSetWrite("expert", is_active=active, skill_ids=(1,)),),
            remove_names=(),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
            mcp_catalog_entries={},
        )

    result = apply()
    assert result.changed is changed
    assert result.details["updated" if changed else "unchanged"] == ("expert",)
    with db.orm_session() as session:
        member = session.query(SkillSetSkill).filter_by(skill_set_id=20).one()
        assert member.skill_uuid == UUID
        assert member.skill_id == (1 if changed else raw_id)
    assert snapshot(repo).installations == ({1} if active else set())
    repeated = apply()
    assert repeated.changed is False
    assert repeated.details["unchanged"] == ("expert",)
    assert snapshot(repo).installations == ({1} if active else set())
