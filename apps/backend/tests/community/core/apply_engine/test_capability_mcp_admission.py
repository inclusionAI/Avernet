"""Set admission uses effective Owner defaults and retained Bot overrides."""

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DefaultSkillSetRef,
    DesiredCapabilities,
    DesiredSkillSet,
)
from agentclaw.community.core.models.mcp import BotMCPConfig, SkillSetMCPServer
from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion
from ._capabilities_world import CapabilityWorld
from .test_unified_capabilities import apply


def _pre_override_world(*, default: bool):
    world = CapabilityWorld()
    world.center.endpoint_envs["mcp.one"] = ("PRE",)
    with world.db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.one",
            config='{"endpoint_env":"PRE"}', env="dev",
        ))
        group = SkillSet(
            name="default" if default else "expert", user_id="" if default else "entity-1",
            bolt_id="" if default else "bot-1", engine_type="moltis",
            is_default=default, is_active=True, env="dev",
        )
        session.add(group)
        session.flush()
        group_id = str(group.id)
        if default:
            session.add(SkillSetMCPServer(
                skill_set_id=group.id, server_code="mcp.one", name="one", env="dev",
            ))
            session.add(DefaultSkillsetMcpExclusion(
                user_id="entity-1", bot_id="bot-1", skill_set_id=group.id,
                server_code="mcp.one",
            ))
    return world, group_id


@pytest.mark.asyncio
@pytest.mark.parametrize("default", [False, True])
async def test_apply_set_admission_preserves_effective_bot_override(default: bool):
    world, _ = _pre_override_world(default=default)
    target = DesiredCapabilities(skill_sets=Declaration((
        DefaultSkillSetRef() if default else DesiredSkillSet("expert", True, (), ("mcp.one",)),
    )))
    before = world.snapshot()
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert preview.status.value == "SUCCEEDED"
    assert world.snapshot() == before
    actual = await apply(world.ctx, world.materialiser, target)
    assert actual.status.value == "SUCCEEDED"
    after = world.snapshot()
    assert after.installed_mcp_codes == frozenset({"mcp.one"})
    assert after.mcp_overrides == before.mcp_overrides == {"mcp.one": {"endpoint_env": "PRE"}}
    assert after.mcp_exclusions == frozenset()


@pytest.mark.asyncio
@pytest.mark.parametrize("default", [False, True])
async def test_management_add_or_restore_uses_effective_bot_override(default: bool):
    world, group_id = _pre_override_world(default=default)
    before = world.snapshot()
    await world.management.add_mcp(
        bot_id="bot-1", owner_id="entity-1", user_id="owner",
        set_id=group_id, server_code="mcp.one",
    )
    after = world.snapshot()
    assert after.installed_mcp_codes == frozenset({"mcp.one"})
    assert after.mcp_overrides == before.mcp_overrides
    assert after.mcp_exclusions == frozenset()
