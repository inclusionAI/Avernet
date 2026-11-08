"""Unchanged shared installations still retry device projection through the real claim flow."""

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    CenterSkillByCode, Declaration, DesiredCapabilities, DesiredSkill, RepoSkillPath,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    RuntimeProjectionIssue, RuntimeProjectionResult, RuntimeProjectionStatus,
)
from ._capabilities_world import CapabilityWorld
from .test_capability_full_replace_contract import _Runtime
from .test_unified_capabilities import apply


class _RecoverableRuntime(_Runtime):
    def __init__(self, status):
        super().__init__()
        self.status = status

    async def apply_plan(self, **kwargs):
        await super().apply_plan(**kwargs)
        if self.status is RuntimeProjectionStatus.CONVERGED:
            return RuntimeProjectionResult.converged()
        return RuntimeProjectionResult(
            status=self.status,
            issues=(RuntimeProjectionIssue(
                resource_type="SKILL", code="DEVICE_UNAVAILABLE", reason="temporary outage",
                status=self.status, retryable=True,
            ),),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["repo", "center"])
@pytest.mark.parametrize("status", [RuntimeProjectionStatus.PENDING, RuntimeProjectionStatus.DEGRADED])
async def test_same_shared_declaration_retries_projection_and_keeps_unchanged_outcome(origin, status):
    runtime = _RecoverableRuntime(status)
    world = CapabilityWorld(device=True, runtime=runtime)
    if origin == "repo":
        world.shared("review", "review")
        skill = DesiredSkill("review", RepoSkillPath("review"))
    else:
        skill = DesiredSkill("weather", CenterSkillByCode("weather"))
    target = DesiredCapabilities(skills=Declaration((skill,)))
    first = await apply(world.ctx, world.materialiser, target)
    assert first.categories[0].entries[0].outcome.value == "created"
    assert first.categories[0].entries[0].note == "Runtime projection is " + status.value.lower()
    assert len(runtime.scopes) == 1
    before = world.snapshot()

    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert preview.categories[0].entries[0].outcome.value == "unchanged"
    assert len(runtime.scopes) == 1
    retry = await apply(world.ctx, world.materialiser, target)
    assert retry.categories[0].entries[0].outcome.value == "unchanged"
    assert retry.categories[0].entries[0].note == "Runtime projection is " + status.value.lower()
    assert len(runtime.scopes) == 2
    assert world.snapshot() == before

    runtime.status = RuntimeProjectionStatus.CONVERGED
    recovered = await apply(world.ctx, world.materialiser, target)
    assert recovered.categories[0].entries[0].outcome.value == "unchanged"
    assert recovered.categories[0].entries[0].note is None
    assert len(runtime.scopes) == 3
    assert world.snapshot() == before
