"""Set supply is rechecked when an MCP configuration action reaches persistence."""

import pytest

from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.repository.implementations.bot.scoped_mcp_config import ScopedMCPConfigRepository

from ._mcp_config_support import McpWorld
from .test_capability_full_replace_contract import _Runtime


def _set_with_mcp(world, code, *, active):
    row = world.desired.create_set(
        bot_id="bot-1", owner_id="entity-1", name="ordinary", description=None, engine_type="moltis",
    )
    if not active:
        world.desired.set_skill_set_active(
            bot_id="bot-1", owner_id="entity-1", set_id=row["id"], active=False, engine_type="moltis",
        )
    world.desired.add_mcp(
        bot_id="bot-1", owner_id="entity-1", set_id=row["id"], server_code=code,
        platform_default_codes=frozenset(), name=code, description=None, icon=None, engine_type="moltis",
    )
    return row["id"]


def _save(world, code):
    ScopedMCPConfigRepository(world.db).replace(
        user_id="owner", server_code=code, headers={}, bot_headers={"bot-1": {"X-Saved": "keep"}},
        owned_bot_ids={"bot-1"}, endpoint_env="PROD", transport_protocol=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [None, {}, {"headers": {"X-New": "no"}}])
async def test_a_set_beginning_supply_after_precheck_prevents_a_direct_configuration_claim(config):
    world = McpWorld()
    _save(world, "mcp.raced")
    world.center.on_detail = lambda: _set_with_mcp(world, "mcp.raced", active=True)
    entry = {"server_code": "mcp.raced", **({"config": config} if config is not None else {})}
    report = await world.apply_manifest([entry])
    assert report.status is ApplyStatus.FAILED
    assert world.saved() == {"mcp.raced": {"headers": {"X-Saved": "keep"}}}
    assert world.direct.set_supplied_capabilities(bot_id="bot-1", owner_id="owner", actor_id="owner")[1] == {"mcp.raced"}


@pytest.mark.asyncio
async def test_a_set_beginning_supply_after_planning_prevents_parent_removal_without_rolling_back_prior_claims():
    class Runtime(_Runtime):
        async def apply_plan(self, **kwargs):
            await super().apply_plan(**kwargs)
            world.desired.set_skill_set_active(
                bot_id="bot-1", owner_id="entity-1", set_id=set_id, active=True, engine_type="moltis",
            )

    world = McpWorld(runtime=Runtime())
    set_id = _set_with_mcp(world, "mcp.raced", active=False)
    _save(world, "mcp.raced")
    report = await world.apply_manifest([{"server_code": "mcp.first"}], retain_fixture_supplies=True)
    assert report.status is ApplyStatus.PARTIAL
    assert report.categories[0].partially_written is True
    assert world.saved() == {"mcp.raced": {"headers": {"X-Saved": "keep"}}}
    assert world.direct.list_installed_mcps(bot_id="bot-1", owner_id="owner", actor_id="owner") == {"mcp.first", "mcp.raced"}
