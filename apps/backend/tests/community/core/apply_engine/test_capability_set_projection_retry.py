"""Explicit active Set retries reconcile runtime even when desired-state rows match."""

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillByCode, Declaration, DefaultSkillSetRef, DesiredCapabilities,
    DesiredSkill, DesiredSkillSet, RepoSkillPath,
)
from agentclaw.community.core.models.skill import SkillSet, SkillSetSkill
from agentclaw.community.core.skill_center.runtime_projection_contract import RuntimeProjectionStatus
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange, ResolvedSkillSetWrite,
)
from ._capabilities_world import CapabilityWorld
from .test_capability_projection_retry import _RecoverableRuntime
from .test_unified_capabilities import apply


@pytest.mark.asyncio
@pytest.mark.parametrize("device", [True, False])
@pytest.mark.parametrize("origin", ["repo", "center"])
@pytest.mark.parametrize("status", [RuntimeProjectionStatus.PENDING, RuntimeProjectionStatus.DEGRADED])
async def test_active_set_retry_preserves_unchanged_and_runtime_warning(device, origin, status):
    runtime = _RecoverableRuntime(status)
    world = CapabilityWorld(device=device, runtime=runtime)
    if origin == "repo":
        world.shared("review", "review")
        skill = DesiredSkill("review", RepoSkillPath("review"))
    else:
        skill = DesiredSkill("weather", CenterSkillByCode("weather"))
    target = DesiredCapabilities(skill_sets=Declaration((
        DesiredSkillSet("expert", True, (skill,), ()),
    )))
    first = await apply(world.ctx, world.materialiser, target)
    assert first.categories[0].entries[0].outcome.value == "created"
    expected_note = "Runtime projection is " + status.value.lower() if device else None
    assert first.categories[0].entries[0].note == expected_note
    assert len(runtime.scopes) == int(device)
    before = world.snapshot()
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert preview.categories[0].entries[0].outcome.value == "unchanged"
    assert len(runtime.scopes) == int(device)
    retry = await apply(world.ctx, world.materialiser, target)
    assert retry.categories[0].entries[0].outcome.value == "unchanged"
    assert retry.categories[0].entries[0].note == expected_note
    assert len(runtime.scopes) == 2 * int(device)
    assert world.snapshot() == before
    runtime.status = RuntimeProjectionStatus.CONVERGED
    recovered = await apply(world.ctx, world.materialiser, target)
    assert recovered.categories[0].entries[0].outcome.value == "unchanged"
    assert recovered.categories[0].entries[0].note is None
    assert len(runtime.scopes) == 3 * int(device)
    assert world.snapshot() == before


@pytest.mark.asyncio
async def test_unchanged_set_command_itself_retries_projection():
    runtime = _RecoverableRuntime(RuntimeProjectionStatus.PENDING)
    world = CapabilityWorld(device=True, runtime=runtime)
    asset = world.shared("review", "review")
    kwargs = dict(
        scope=world.snapshot().scope, actor_id="owner",
        sets=(ResolvedSkillSetWrite("expert", skill_ids=(int(asset["id"]),)),),
        remove_names=(), default_change=DefaultExclusionChange(), platform_default_codes=frozenset(),
    )
    first = await world.sets.replace_apply_skill_sets(**kwargs)
    assert first.changed
    assert first.runtime_projection["status"] == "PENDING"
    retry = await world.sets.replace_apply_skill_sets(**kwargs)
    assert not retry.changed
    assert retry.unchanged == ("expert",)
    assert retry.runtime_projection["status"] == "PENDING"
    assert len(runtime.scopes) == 2


@pytest.mark.asyncio
async def test_default_reference_retries_projection_without_changing_default_facts():
    runtime = _RecoverableRuntime(RuntimeProjectionStatus.PENDING)
    world = CapabilityWorld(device=True, runtime=runtime)
    asset = world.shared("review", "review")
    with world.db.transactional_orm_session() as session:
        group = SkillSet(
            name="default", is_default=True, engine_type="moltis", user_id="", bolt_id="", env="dev",
        )
        session.add(group)
        session.flush()
        session.add(SkillSetSkill(skill_set_id=group.id, skill_id=int(asset["id"]), env="dev"))
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    first = await apply(world.ctx, world.materialiser, target)
    assert first.categories[0].entries[0].outcome.value == "updated"
    assert first.categories[0].entries[0].note == "Runtime projection is pending"
    before = world.snapshot()
    retry = await apply(world.ctx, world.materialiser, target)
    assert retry.categories[0].entries[0].outcome.value == "unchanged"
    assert retry.categories[0].entries[0].note == "Runtime projection is pending"
    assert len(runtime.scopes) == 2
    assert world.snapshot() == before


@pytest.mark.asyncio
async def test_unchanged_inactive_set_does_not_request_another_projection():
    runtime = _RecoverableRuntime(RuntimeProjectionStatus.PENDING)
    world = CapabilityWorld(device=True, runtime=runtime)
    world.shared("review", "review")
    target = DesiredCapabilities(skill_sets=Declaration((
        DesiredSkillSet("expert", False, (DesiredSkill("review", RepoSkillPath("review")),), ()),
    )))
    await apply(world.ctx, world.materialiser, target)
    count = len(runtime.scopes)
    before = world.snapshot()
    retry = await apply(world.ctx, world.materialiser, target)
    assert retry.categories[0].entries[0].outcome.value == "unchanged"
    assert len(runtime.scopes) == count
    assert world.snapshot() == before
