"""Bulk Apply through the existing authorized management service seam."""

from dataclasses import replace

import pytest

from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from tests.community.repository.skill_center.test_capability_desired_state_uow import (
    _Database,
)
from tests.community.core.skill_center._test_skill_set_management_service_support import (
    _skill_service,
    _Runtime,
)


@pytest.mark.asyncio
async def test_bulk_apply_service_reads_and_replaces_without_runtime_for_platform():
    service = _skill_service(CapabilityDesiredStateRepository(_Database()), _Runtime())
    before = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )
    result = await service.replace_apply_skill_sets(
        scope=before.scope,
        actor_id="true-owner",
        sets=(ResolvedSkillSetWrite("expert"),),
        remove_names=(),
        default_change=DefaultExclusionChange(),
        platform_default_codes=before.platform_default_codes,
        project=False,
    )
    assert result.created == ("expert",) and result.changed
    assert result.runtime_projection["status"] == "SKIPPED"
    after = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )
    assert tuple(item.name for item in after.sets) == ("expert",)
    assert after.sets[0].is_active


@pytest.mark.asyncio
async def test_bulk_apply_service_refuses_stale_trusted_scope_before_writes():
    service = _skill_service(CapabilityDesiredStateRepository(_Database()), _Runtime())
    before = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="CAPABILITY_SCOPE_CHANGED"
    ):
        await service.replace_apply_skill_sets(
            scope=replace(before.scope, engine_type="hermes"),
            actor_id="true-owner",
            sets=(ResolvedSkillSetWrite("expert"),),
            remove_names=(),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
            project=False,
        )
    assert (
        service.list_sets(bot_id="bot-1", owner_id="true-owner", user_id="true-owner")
        == []
    )
