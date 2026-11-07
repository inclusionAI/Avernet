"""Normal capability reports reflect committed commands and Local package writes."""

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DesiredCapabilities,
    DesiredMcpServer,
    DesiredSkill,
    DesiredSkillSet,
    ContentRef,
    LocalSkillPackage,
    RepoSkillPath,
)
from agentclaw.community.core.models.mcp import BotMCPConfig
from ._capabilities_world import CapabilityWorld
from .test_unified_capabilities import apply, local_skill


def _outcomes(result):
    return [(entry.identity, entry.outcome.value) for entry in result.entries]


async def _plan(world, target):
    resolved = await world.materialiser.resolve(world.ctx, (target,))
    assert not resolved.failures
    return await world.materialiser.plan(world.ctx, resolved.intents)


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_install", [False, True])
async def test_direct_receipts_distinguish_creation_from_concurrent_noop(concurrent_install):
    world = CapabilityWorld()
    shared = world.shared("review", "review")
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("review", RepoSkillPath("review")),)),
        mcp=Declaration((DesiredMcpServer("mcp.one"),)),
    )
    plan = await _plan(world, target)
    assert [entry.outcome for entry in plan.entries] == ["created", "created"]
    if concurrent_install:
        await world.activation.activate_skill(
            bot_id="bot-1", owner_id="entity-1", actor_id="owner", skill_id=shared["id"],
        )
        await world.activation.activate_mcp(
            bot_id="bot-1", owner_id="entity-1", actor_id="owner", server_code="mcp.one",
        )
    result = await world.materialiser.write(world.ctx, plan)
    expected = "unchanged" if concurrent_install else "created"
    assert _outcomes(result) == [("skills/review", expected), ("mcp/mcp.one", expected)]
    assert not result.removals


@pytest.mark.asyncio
async def test_direct_receipts_report_actual_replacement_after_a_stale_creation_plan():
    world = CapabilityWorld()
    world.shared("review", "new")
    previous = world.shared("review", "old")
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("review", RepoSkillPath("new")),)),
        mcp=Declaration((DesiredMcpServer("mcp.one"),)),
    )
    plan = await _plan(world, target)
    assert [entry.outcome for entry in plan.entries] == ["created", "created"]
    await world.activation.activate_skill(
        bot_id="bot-1", owner_id="entity-1", actor_id="owner", skill_id=previous["id"],
    )
    await world.activation.activate_mcp(
        bot_id="bot-1", owner_id="entity-1", actor_id="owner", server_code="mcp.one",
    )
    await world.activation.set_mcp_override(
        bot_id="bot-1", owner_id="entity-1", actor_id="owner", server_code="mcp.one",
        config={"headers": {"X-Old": "value"}},
    )
    result = await world.materialiser.write(world.ctx, plan)
    assert _outcomes(result) == [("skills/review", "updated"), ("mcp/mcp.one", "updated")]
    assert world.snapshot().mcp_overrides == {}


@pytest.mark.asyncio
async def test_equivalent_mcp_alias_cleanup_reports_unchanged_and_preserves_effective_config():
    world = CapabilityWorld()
    await world.activation.activate_mcp(
        bot_id="bot-1", owner_id="entity-1", actor_id="owner", server_code="mcp.one",
    )
    with world.db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            bot_id="bot-1", owner_id="owner", server_code="mcp.one", config='{}', env="dev",
        ))
    result = await apply(
        world.ctx, world.materialiser,
        DesiredCapabilities(mcp=Declaration((DesiredMcpServer("mcp.one"),))),
    )
    assert _outcomes(result.categories[0]) == [("mcp/mcp.one", "unchanged")]
    assert world.snapshot().mcp_overrides == {}


@pytest.mark.asyncio
async def test_local_upload_reports_actual_replacement_when_its_claim_is_already_installed():
    world = CapabilityWorld()
    target = DesiredCapabilities(skills=Declaration((local_skill(world, "local-one"),)))
    stale_plan = await _plan(world, target)
    assert stale_plan.entries[0].outcome == "created"
    first = await apply(world.ctx, world.materialiser, target)
    assert _outcomes(first.categories[0]) == [("skills/local-one", "created")]
    raced = await world.materialiser.write(world.ctx, stale_plan)
    assert _outcomes(raced) == [("skills/local-one", "updated")]
    again = await apply(world.ctx, world.materialiser, target)
    assert _outcomes(again.categories[0]) == [("skills/local-one", "updated")]


@pytest.mark.asyncio
async def test_direct_claim_of_a_dependency_keeps_its_effective_outcome_unchanged():
    world = CapabilityWorld()
    shared = world.shared("review", "review", dependencies=("mcp.one",))
    await world.activation.activate_skill(
        bot_id="bot-1", owner_id="entity-1", actor_id="owner", skill_id=shared["id"],
    )
    target = DesiredCapabilities(mcp=Declaration((DesiredMcpServer("mcp.one"),)))
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert _outcomes(preview.categories[0]) == [("mcp/mcp.one", "unchanged")]
    actual = await apply(world.ctx, world.materialiser, target)
    assert _outcomes(actual.categories[0]) == [("mcp/mcp.one", "unchanged")]
    assert world.snapshot().installed_mcp_codes == frozenset({"mcp.one"})


@pytest.mark.asyncio
async def test_set_reports_local_package_replacement_with_unchanged_membership():
    import hashlib
    from agentclaw.community.core.content_store import canonical_tree_bytes

    world = CapabilityWorld()
    member = local_skill(world, "local-one")
    target = DesiredCapabilities(
        skill_sets=Declaration((DesiredSkillSet("expert", True, (member,), ()),))
    )
    first = await apply(world.ctx, world.materialiser, target)
    assert _outcomes(first.categories[0]) == [("skill_sets/expert", "created")]
    before = world.snapshot()
    original_package = world.uploads.packages["local-one"]
    tree = canonical_tree_bytes([
        ("SKILL.md", b"---\nname: local-one\ndescription: Skill package\n---\nBody v2")
    ])
    digest = "sha256:" + hashlib.sha256(tree).hexdigest()
    world.content.blobs[digest] = tree
    replacement = DesiredSkill("local-one", LocalSkillPackage(ContentRef(digest)))
    target = DesiredCapabilities(
        skill_sets=Declaration((DesiredSkillSet("expert", True, (replacement,), ()),))
    )
    result = await apply(world.ctx, world.materialiser, target)
    assert _outcomes(result.categories[0]) == [("skill_sets/expert", "updated")]
    assert world.uploads.packages["local-one"] != original_package
    assert world.snapshot().sets == before.sets
    assert world.snapshot().installed_skill_ids == frozenset(before.sets[0].skill_ids)
