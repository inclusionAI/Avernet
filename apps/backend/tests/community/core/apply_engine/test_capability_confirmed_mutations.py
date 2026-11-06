"""Only actual committed Bot mutations confirm a partial Apply write."""

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
from .test_unified_capabilities import apply


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "recovery"])
@pytest.mark.parametrize("operation", ["create", "remove", "noop", "precommit"])
async def test_set_only_failure_preserves_actual_commit_receipt(
    failure, operation, monkeypatch
):
    world = CapabilityWorld(device=failure == "recovery")
    target = DesiredCapabilities(
        skill_sets=Declaration((DesiredSkillSet("expert", True, (), ()),))
    )
    materialiser = world.materialiser
    if operation == "noop":
        resolved = await materialiser.resolve(world.ctx, (target,))
        stale_plan = await materialiser.plan(world.ctx, resolved.intents)

        class ConcurrentNoop:
            async def resolve(self, ctx, entries):
                return await world.materialiser.resolve(ctx, entries)

            async def plan(self, ctx, intents):
                return stale_plan

            async def write(self, ctx, plan):
                return await world.materialiser.write(ctx, plan)

        materialiser = ConcurrentNoop()
    if operation in {"remove", "noop"}:
        seeded = await apply(world.ctx, world.materialiser, target)
        assert seeded.status.value == "SUCCEEDED"
    if operation == "remove":
        target = DesiredCapabilities(skill_sets=Declaration(()))

    def fail(**_kwargs):
        raise RuntimeError("injected persistence failure")

    if failure == "audit":
        monkeypatch.setattr(world.management, "_audit", fail)
    else:
        async def pending(**_kwargs):
            raise RuntimeError("runtime unavailable")

        monkeypatch.setattr(world.runtime, "apply_plan", pending)
        monkeypatch.setattr(world.management._flow._recovery, "ensure", fail)
    if operation == "precommit":
        monkeypatch.setattr(world.repo, "replace_apply_skill_sets", fail)
    result = await apply(world.ctx, materialiser, target)
    committed = operation in {"create", "remove"}
    assert result.status.value == ("PARTIAL" if committed else "FAILED")
    assert result.categories[0].confirmed_partial is committed
    assert {group.name for group in world.snapshot().sets} == (
        {"expert"} if operation in {"create", "noop"} else set()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["noop", "created", "alias_replace", "alias_clear"])
async def test_later_claim_failure_confirms_partial_only_after_an_actual_prior_mutation(
    first, monkeypatch
):
    world = CapabilityWorld()
    who = dict(bot_id="bot-1", owner_id="entity-1", actor_id="owner", apply_id="seed")
    if first != "created":
        await world.activation.claim_apply_mcp(
            **who, server_code="mcp.first", config=None
        )
    config = {"headers": {"X-Old": "legacy"}} if first == "alias_replace" else None
    if first.startswith("alias"):
        with world.db.transactional_orm_session() as session:
            session.add(
                BotMCPConfig(
                    bot_id="bot-1",
                    owner_id="owner",
                    server_code="mcp.first",
                    config=json.dumps({"headers": {"X-Old": "legacy"}}),
                    env="dev",
                )
            )
            if first == "alias_clear":
                session.add(
                    BotMCPConfig(
                        bot_id="bot-1",
                        owner_id="entity-1",
                        server_code="mcp.first",
                        config="{}",
                        env="dev",
                    )
                )
    claim = world.repo.claim_apply_mcp

    def fail_second(**kwargs):
        if kwargs["server_code"] == "mcp.second":
            raise RuntimeError("database failed before mutation commit")
        return claim(**kwargs)

    monkeypatch.setattr(world.repo, "claim_apply_mcp", fail_second)
    target = DesiredCapabilities(
        mcp=Declaration(
            (
                DesiredMcpServer(
                    "mcp.first", McpOverride.from_config(config) if config else None
                ),
                DesiredMcpServer("mcp.second"),
            )
        )
    )
    result = await apply(world.ctx, world.materialiser, target)
    changed = first != "noop"
    assert result.status.value == ("PARTIAL" if changed else "FAILED")
    assert result.categories[0].confirmed_partial is changed
    assert result.categories[0].removals == ()
    assert world.snapshot().installed_mcp_codes == frozenset({"mcp.first"})
    if first.startswith("alias"):
        with world.db.orm_session() as session:
            assert {row.owner_id for row in session.query(BotMCPConfig)} == (
                {"entity-1"} if config else set()
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [None, {}])
async def test_unknown_command_receipt_never_claims_a_confirmed_partial_write(
    receipt, monkeypatch
):
    world = CapabilityWorld()
    claim = world.activation.claim_apply_mcp

    async def unknown_first(**kwargs):
        if kwargs["server_code"] == "mcp.second":
            raise RuntimeError("database failed before mutation commit")
        await claim(**kwargs)
        return receipt

    monkeypatch.setattr(world.activation, "claim_apply_mcp", unknown_first)
    target = DesiredCapabilities(
        mcp=Declaration((DesiredMcpServer("mcp.first"), DesiredMcpServer("mcp.second")))
    )
    result = await apply(world.ctx, world.materialiser, target)
    assert result.status.value == "FAILED"
    assert result.categories[0].partially_written
    assert not result.categories[0].confirmed_partial
    assert world.snapshot().installed_mcp_codes == frozenset({"mcp.first"})


@pytest.mark.asyncio
async def test_removal_already_completed_by_another_writer_does_not_confirm_partial(
    monkeypatch,
):
    world = CapabilityWorld()
    who = dict(bot_id="bot-1", owner_id="entity-1", actor_id="owner", apply_id="seed")
    for code in ("mcp.first", "mcp.second"):
        await world.activation.claim_apply_mcp(**who, server_code=code, config=None)

    # Simulate an independent actor between the public plan/write stages.
    class Barrier:
        async def resolve(self, ctx, entries):
            return await world.materialiser.resolve(ctx, entries)

        async def plan(self, ctx, intents):
            planned = await world.materialiser.plan(ctx, intents)
            await world.activation.remove_apply_mcp(**who, server_code="mcp.first")
            return planned

        async def write(self, ctx, plan):
            return await world.materialiser.write(ctx, plan)

    remove = world.repo.remove_apply_mcp

    def fail_second(**kwargs):
        if kwargs["server_code"] == "mcp.second":
            raise RuntimeError("database failed before removal commit")
        return remove(**kwargs)

    monkeypatch.setattr(world.repo, "remove_apply_mcp", fail_second)
    result = await apply(world.ctx, Barrier(), DesiredCapabilities(mcp=Declaration(())))
    assert result.status.value == "FAILED"
    assert not result.categories[0].confirmed_partial
    assert result.categories[0].removals == ()
    assert world.snapshot().installed_mcp_codes == frozenset({"mcp.second"})


@pytest.mark.asyncio
async def test_concurrent_direct_skill_claim_noop_does_not_confirm_partial(monkeypatch):
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        RepoSkillPath,
    )

    world = CapabilityWorld()
    asset = world.shared("review", "review")

    class Barrier:
        async def resolve(self, ctx, entries):
            return await world.materialiser.resolve(ctx, entries)

        async def plan(self, ctx, intents):
            planned = await world.materialiser.plan(ctx, intents)
            await world.activation.claim_apply_skill(
                bot_id="bot-1",
                owner_id="entity-1",
                actor_id="owner",
                apply_id="another-actor",
                skill_id=asset["id"],
            )
            return planned

        async def write(self, ctx, plan):
            return await world.materialiser.write(ctx, plan)

    def fail_mcp(**_kwargs):
        raise RuntimeError("database failed before MCP commit")

    monkeypatch.setattr(world.repo, "claim_apply_mcp", fail_mcp)
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("review", RepoSkillPath("review")),)),
        mcp=Declaration((DesiredMcpServer("mcp.second"),)),
    )
    result = await apply(world.ctx, Barrier(), target)
    assert result.status.value == "FAILED"
    assert not result.categories[0].confirmed_partial
    assert world.snapshot().installed_skill_ids == frozenset({int(asset["id"])})


@pytest.mark.parametrize("config", [None, {"headers": {"X-Old": "legacy"}}])
def test_alias_normalization_confirms_persistence_without_fabricating_effective_mcp_change(
    config,
):
    world = CapabilityWorld()
    arguments = dict(
        bot_id="bot-1",
        owner_id="entity-1",
        server_code="mcp.first",
        config=config,
        platform_default_codes=frozenset(),
        engine_type="moltis",
        default_engine_types=("moltis",),
    )
    world.repo.claim_apply_mcp(**arguments)
    with world.db.transactional_orm_session() as session:
        session.add(
            BotMCPConfig(
                bot_id="bot-1",
                owner_id="owner",
                server_code="mcp.first",
                config=json.dumps({"headers": {"X-Old": "legacy"}}),
                env="dev",
            )
        )
        if config is None:
            session.add(
                BotMCPConfig(
                    bot_id="bot-1",
                    owner_id="entity-1",
                    server_code="mcp.first",
                    config="{}",
                    env="dev",
                )
            )
    normalized = world.repo.claim_apply_mcp(**arguments)
    assert normalized.changed is True
    assert normalized.mcp_codes == normalized.updated_mcp_codes == frozenset()
    assert world.repo.claim_apply_mcp(**arguments).changed is False
