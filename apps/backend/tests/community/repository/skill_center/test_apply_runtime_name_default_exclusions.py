"""Name admission excludes Default references by their historical raw key."""

from datetime import datetime

import pytest

from agentclaw.community.core.models.skill import Skill, SkillSet, SkillSetSkill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.skill_center.errors import SkillRuntimeNameConflictError
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from .test_apply_center_membership_identity import SCOPE, seed, snapshot


@pytest.mark.parametrize("excluded", [False, True])
def test_apply_name_admission_filters_raw_default_exclusions_before_resolution(
    excluded,
):
    db, repo = seed(excluded=excluded)
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(
                    id=2,
                    name="weather",
                    git_path="center://other-weather",
                    skill_uuid="00000000-0000-4000-8000-000000000002",
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
                    name="weather",
                    description="Different runtime source",
                    metadata_json='{"mcp_dependencies":[]}',
                    published_at=datetime(2026, 9, 1),
                    created_by="owner",
                    env="dev",
                ),
            ]
        )

    def apply():
        return repo.replace_apply_skill_sets(
            scope=SCOPE,
            sets=(ResolvedSkillSetWrite("expert", is_active=True, skill_ids=(2,)),),
            remove_names=(),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
            mcp_catalog_entries={},
        )

    if excluded:
        assert apply().details["created"] == ("expert",)
        assert snapshot(repo).installations == {2}
        assert apply().changed is False
    else:
        with pytest.raises(SkillRuntimeNameConflictError):
            apply()
        with db.orm_session() as session:
            assert session.query(SkillSet).filter_by(name="expert").count() == 0
            assert session.query(SkillSetSkill).count() == 1
