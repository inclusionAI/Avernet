"""Preserved MCP config and concurrency contracts through the aggregate Apply seam."""

import json

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DesiredCapabilities,
    DesiredMcpServer,
    DesiredSkillSet,
    McpOverride,
)
from agentclaw.community.core.models.mcp import BotMCPConfig
from ._capabilities_world import CapabilityWorld
from .test_capability_full_replace_contract import _Runtime
from .test_unified_capabilities import apply


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [{}, {"headers": {"X-Old": "legacy"}}])
async def test_explicit_equivalent_config_consumes_legacy_alias_at_aggregate_seam(
    explicit,
):
    world = CapabilityWorld()
    initial = DesiredCapabilities(mcp=Declaration((DesiredMcpServer("mcp.test"),)))
    assert (
        not (await apply(world.ctx, world.materialiser, initial)).categories[0].aborted
    )
    with world.db.transactional_orm_session() as session:
        session.add(
            BotMCPConfig(
                bot_id="bot-1",
                owner_id="owner",
                server_code="mcp.test",
                config=json.dumps({"headers": {"X-Old": "legacy"}}),
                env="dev",
            )
        )
        if not explicit:
            session.add(
                BotMCPConfig(
                    bot_id="bot-1",
                    owner_id="entity-1",
                    server_code="mcp.test",
                    config="{}",
                    env="dev",
                )
            )
    target = DesiredCapabilities(
        mcp=Declaration(
            (
                DesiredMcpServer(
                    "mcp.test", McpOverride.from_config(explicit) if explicit else None
                ),
            )
        )
    )
    result = await apply(world.ctx, world.materialiser, target)
    assert [(e.identity, e.outcome.value) for e in result.categories[0].entries] == [
        ("mcp/mcp.test", "unchanged")
    ]
    with world.db.orm_session() as session:
        rows = session.query(BotMCPConfig).all()
        assert {row.owner_id for row in rows} == ({"entity-1"} if explicit else set())


@pytest.mark.asyncio
async def test_set_activation_after_first_mcp_claim_stops_removal_and_keeps_confirmed_partial():
    class Runtime(_Runtime):
        async def apply_plan(self, **kwargs):
            await super().apply_plan(**kwargs)
            world.repo.set_skill_set_active(
                bot_id="bot-1",
                owner_id="entity-1",
                set_id=set_id,
                active=True,
                engine_type="moltis",
            )

    world = CapabilityWorld(device=True, runtime=Runtime())
    group = world.repo.create_set(
        bot_id="bot-1",
        owner_id="entity-1",
        name="ordinary",
        description=None,
        engine_type="moltis",
    )
    set_id = group["id"]
    world.repo.set_skill_set_active(
        bot_id="bot-1",
        owner_id="entity-1",
        set_id=set_id,
        active=False,
        engine_type="moltis",
    )
    world.repo.add_mcp(
        bot_id="bot-1",
        owner_id="entity-1",
        set_id=set_id,
        server_code="mcp.raced",
        platform_default_codes=frozenset(),
        name="raced",
        description=None,
        icon=None,
        engine_type="moltis",
    )
    with world.db.transactional_orm_session() as session:
        session.add(
            BotMCPConfig(
                bot_id="bot-1",
                owner_id="entity-1",
                server_code="mcp.raced",
                config=json.dumps({"headers": {"X-Saved": "keep"}}),
                env="dev",
            )
        )
    # This is the current explicit full target: the inactive ordinary Set is retained.
    target = DesiredCapabilities(
        skill_sets=Declaration(
            (DesiredSkillSet("ordinary", False, (), ("mcp.raced",)),)
        ),
        mcp=Declaration((DesiredMcpServer("mcp.first"),)),
    )
    result = await apply(world.ctx, world.materialiser, target)
    assert result.status.value == "PARTIAL"
    assert (
        result.categories[0].partially_written
        and result.categories[0].confirmed_partial
    )
    assert result.categories[0].removals == ()
    assert world.snapshot().mcp_overrides == {
        "mcp.raced": {"headers": {"X-Saved": "keep"}}
    }
    assert world.snapshot().installed_mcp_codes == frozenset({"mcp.first", "mcp.raced"})
