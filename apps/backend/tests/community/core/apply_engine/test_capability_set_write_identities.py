"""Set-only writes preserve unrelated Direct installations behind historical aliases."""

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration, DefaultSkillSetRef, DesiredCapabilities, DesiredSkillSet,
)
from agentclaw.community.core.models.skill import BotSkillInstallation
from agentclaw.community.core.models.skill_installation_source import BotSkillInstallationSource
from .test_capability_membership_aliases import _historical_world
from .test_unified_capabilities import apply


@pytest.mark.asyncio
@pytest.mark.parametrize("known_direct", [True, False])
async def test_set_only_write_preserves_direct_behind_a_resolved_raw_alias(known_direct):
    world, direct, center = await _historical_world("default")
    if not known_direct:
        # Legacy installations can predate provenance; a raw alias is not proof
        # that this independent installation belonged to Default.
        with world.db.transactional_orm_session() as session:
            installation = session.query(BotSkillInstallation).filter_by(
                bot_id="bot-1", owner_id="entity-1", skill_id=int(direct["id"]), env="dev",
            ).one()
            session.query(BotSkillInstallationSource).filter_by(
                installation_id=installation.id,
            ).delete()
    before = world.snapshot()
    target = DesiredCapabilities(skill_sets=Declaration((
        DefaultSkillSetRef(), DesiredSkillSet("unrelated", True, (), ()),
    )))
    assert target.skills is None
    result = await apply(world.ctx, world.materialiser, target)
    assert result.status.value == "SUCCEEDED", result
    after = world.snapshot()
    assert after.installed_skill_ids == before.installed_skill_ids
    assert after.installed_skill_ids == frozenset({int(direct["id"]), int(center["id"])})
    assert next(s for s in after.sets if s.is_default) == before.sets[0]
    assert after.skill_exclusions == before.skill_exclusions
    assert {s.name for s in after.sets} == {"default", "unrelated"}
