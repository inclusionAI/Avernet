"""Legacy Apply preserves inactive ownership without consuming unavailable content."""

from datetime import datetime

import pytest

from agentclaw.community.core.apply_engine.capabilities import Category
from agentclaw.community.core.apply_engine.delivery import ArcaDelivery
from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DesiredMcpServer,
    DesiredSkill,
    DesiredState,
    RepoSkillPath,
)
from agentclaw.community.core.apply_engine.orchestrator import ApplyOrchestrator
from agentclaw.community.core.models.skill import Skill, SkillSetSkill
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from ._capabilities_world import CapabilityWorld


async def _legacy_apply(world, *, skills, mcp, dry_run):
    state = DesiredState(skills=Declaration(skills), mcp=Declaration(mcp))
    assert state.capabilities.entries[0].skill_sets is None
    return await ApplyOrchestrator(
        {Category.CAPABILITIES: world.materialiser},
        steps=ArcaDelivery(lambda: None).steps_for,
    ).apply(
        world.ctx, state, apply_id="legacy", trigger="explicit",
        started_at=datetime.now(), dry_run=dry_run,
    )


async def _retain(world, skill, *, active):
    await world.sets.replace_apply_skill_sets(
        scope=world.snapshot().scope,
        actor_id="owner",
        sets=(ResolvedSkillSetWrite(
            "retained", is_active=active, skill_ids=(int(skill["id"]),),
        ),),
        remove_names=(),
        default_change=DefaultExclusionChange(),
        platform_default_codes=frozenset(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("unavailable", ["offline", "center_metadata"])
@pytest.mark.parametrize("dry_run", [True, False])
async def test_legacy_apply_only_resolves_content_of_active_retained_sets(
    active, unavailable, dry_run,
):
    world = CapabilityWorld()
    retained = world.shared("retained", "retained")
    fresh = world.shared("fresh", "fresh")
    await _retain(world, retained, active=active)
    with world.db.transactional_orm_session() as session:
        asset = session.get(Skill, int(retained["id"]))
        if unavailable == "offline":
            asset.offline_at = datetime.now()
        else:
            asset.git_path = "center://retained"
            asset.skill_uuid = "11111111-1111-4111-8111-111111111111"
            for membership in session.query(SkillSetSkill).filter_by(skill_id=asset.id):
                membership.skill_uuid = asset.skill_uuid
    before = world.snapshot()
    retained_asset = next(
        s for s in before.skills if int(s["id"]) == int(retained["id"])
    )
    if unavailable == "center_metadata":
        assert retained_asset["skill_uuid"] == "11111111-1111-4111-8111-111111111111"
        assert before.sets[0].skill_references == (
            (int(retained["id"]), retained_asset["skill_uuid"]),
        )
        assert retained_asset["resolution_error"] == "SKILL_MCP_METADATA_UNAVAILABLE"
    else:
        assert retained_asset["offline_at"] is not None

    report = await _legacy_apply(
        world,
        skills=(DesiredSkill("fresh", RepoSkillPath("fresh")),),
        mcp=(DesiredMcpServer("mcp.fresh"),),
        dry_run=dry_run,
    )

    after = world.snapshot()
    assert after.sets == before.sets
    assert after.skill_exclusions == before.skill_exclusions
    assert after.mcp_exclusions == before.mcp_exclusions
    if active:
        assert report.categories[0].aborted
        assert after == before
    else:
        assert report.status.value == "SUCCEEDED", report
        assert not report.categories[0].aborted
        if dry_run:
            assert after == before
        else:
            assert after.installed_skill_ids == frozenset({int(fresh["id"])})
            assert after.installed_mcp_codes == frozenset({"mcp.fresh"})


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_legacy_direct_cannot_claim_a_healthy_inactive_set_member(dry_run):
    world = CapabilityWorld()
    retained = world.shared("retained", "retained")
    await _retain(world, retained, active=False)
    before = world.snapshot()

    report = await _legacy_apply(
        world,
        skills=(DesiredSkill("retained", RepoSkillPath("retained")),),
        mcp=(),
        dry_run=dry_run,
    )

    assert report.categories[0].aborted
    assert "APPLY_SET_SUPPLIED" in report.categories[0].entries[0].reason
    assert world.snapshot() == before
