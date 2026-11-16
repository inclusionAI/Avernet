"""Preview and actual Apply agree when Default's excluded historical UUID joins a Set."""

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillRef, Declaration, DesiredCapabilities, DesiredSkill, DesiredSkillSet,
)
from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion
from agentclaw.community.core.repository.implementations.skill_center.tables import skill_installations
from .test_capability_membership_aliases import _historical_world
from .test_unified_capabilities import apply, local_skill


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("with_local", [True, False])
async def test_excluded_historical_default_member_joins_ordinary_set(dry_run, with_local):
    world, direct, center = await _historical_world("default")
    snapshot = world.snapshot()
    default = snapshot.sets[0]
    with world.db.transactional_orm_session() as session:
        session.add(DefaultSkillsetSkillExclusion(
            user_id="entity-1", bot_id="bot-1", skill_set_id=default.id,
            skill_id=int(direct["id"]), avernet_tenant=snapshot.scope.tenant,
        ))
        skill_installations.uninstall(
            session, bot_id="bot-1", owner_id="entity-1", env="dev",
            skill_ids={int(center["id"])},
        )
    before = world.snapshot()
    members = [DesiredSkill("weather", CenterSkillRef(center["skill_uuid"]))]
    if with_local:
        members.append(local_skill(world, "local-one"))
    target = DesiredCapabilities(skill_sets=Declaration((
        DesiredSkillSet("expert", True, tuple(members), ()),
    )))
    result = await apply(world.ctx, world.materialiser, target, dry_run=dry_run)
    assert result.status.value == "SUCCEEDED", result
    after = world.snapshot()
    assert after.skill_exclusions == before.skill_exclusions
    assert next(s for s in after.sets if s.is_default) == default
    assert int(direct["id"]) in after.installed_skill_ids
    if dry_run:
        assert after == before
        assert not world.uploads.packages
    else:
        ordinary = next(s for s in after.sets if s.name == "expert")
        assert int(center["id"]) in ordinary.skill_ids
        assert len(ordinary.skill_ids) == 1 + int(with_local)
        assert len(after.installed_skill_ids) == 2 + int(with_local)
        assert len(world.uploads.packages) == int(with_local)
