"""Failure and compatibility corners at the aggregate Apply seam."""

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DefaultSkillSetRef,
    DesiredCapabilities,
)
from agentclaw.community.core.models.skill import SkillSet, SkillSetSkill
from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion
from ._capabilities_world import CapabilityWorld
from .test_unified_capabilities import apply, local_skill


@pytest.mark.asyncio
async def test_default_restore_refuses_current_direct_different_asset_name_before_write():
    world = CapabilityWorld()
    direct = world.shared("review", "direct")
    default = world.shared("review", "default")
    with world.db.transactional_orm_session() as session:
        group = SkillSet(
            name="default",
            user_id="",
            bolt_id="",
            engine_type="moltis",
            is_default=True,
            env="dev",
        )
        session.add(group)
        session.flush()
        session.add(
            SkillSetSkill(skill_set_id=group.id, skill_id=int(default["id"]), env="dev")
        )
        session.add(
            DefaultSkillsetSkillExclusion(
                bot_id="bot-1",
                user_id="entity-1",
                skill_set_id=group.id,
                skill_id=int(default["id"]),
            )
        )
    await world.activation.claim_apply_skill(
        skill_id=direct["id"],
        bot_id="bot-1",
        owner_id="entity-1",
        actor_id="owner",
        apply_id="seed",
    )
    before = world.snapshot()
    target = DesiredCapabilities(
        skills=Declaration(()), skill_sets=Declaration((DefaultSkillSetRef(),))
    )
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert preview.categories[0].aborted
    assert world.snapshot() == before


@pytest.mark.asyncio
async def test_same_apply_removed_set_local_is_cleaned_only_after_all_control_writes_succeed():
    from agentclaw.community.core.apply_engine.desired_state import DesiredSkillSet

    world = CapabilityWorld()
    target = DesiredCapabilities(
        skill_sets=Declaration(
            (DesiredSkillSet("expert", False, (local_skill(world, "obsolete"),), ()),)
        )
    )
    assert (
        not (await apply(world.ctx, world.materialiser, target)).categories[0].aborted
    )
    empty = DesiredCapabilities(skills=Declaration(()), skill_sets=Declaration(()))
    preview = await apply(world.ctx, world.materialiser, empty, dry_run=True)
    assert preview.categories[0].removals == ("skill_sets/expert", "skills/obsolete")
    result = await apply(world.ctx, world.materialiser, empty)
    assert not result.categories[0].aborted
    assert world.snapshot().sets == ()
    assert world.uploads.packages == {}
    assert world.snapshot().skills == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("device", [False, True])
async def test_skill_set_delegates_select_projection_for_the_delivery_family(device):
    from agentclaw.community.core.apply_engine.skill_set_apply_delegates import (
        DeviceSkillSetApply,
    )
    from agentclaw.community.core.skill_center.skill_set_apply import (
        ResolvedSkillSetWrite,
        DefaultExclusionChange,
    )

    world = CapabilityWorld()
    port = DeviceSkillSetApply(world.management) if device else world.sets
    result = await port.replace_apply_skill_sets(
        scope=world.snapshot().scope,
        actor_id="owner",
        sets=(ResolvedSkillSetWrite("expert"),),
        remove_names=(),
        default_change=DefaultExclusionChange(),
        platform_default_codes=frozenset(),
    )
    assert result.created == ("expert",)
    assert bool(world.runtime.scopes) == device
    assert (result.runtime_projection["status"] == "SKIPPED") == (not device)


@pytest.mark.asyncio
async def test_retained_set_protects_mcp_override_but_same_apply_removed_set_releases_it():
    from agentclaw.community.core.apply_engine.desired_state import DesiredSkillSet

    world = CapabilityWorld()
    from agentclaw.community.core.models.mcp import BotMCPConfig

    with world.db.transactional_orm_session() as session:
        session.add(
            BotMCPConfig(
                bot_id="bot-1",
                owner_id="entity-1",
                server_code="mcp.one",
                config='{"headers":{"Saved":"value"}}',
                env="dev",
            )
        )
    target = DesiredCapabilities(
        skill_sets=Declaration((DesiredSkillSet("expert", True, (), ("mcp.one",)),))
    )
    assert (
        not (await apply(world.ctx, world.materialiser, target)).categories[0].aborted
    )
    assert (
        not (
            await apply(
                world.ctx, world.materialiser, DesiredCapabilities(mcp=Declaration(()))
            )
        )
        .categories[0]
        .aborted
    )
    assert world.snapshot().mcp_overrides == {
        "mcp.one": {"headers": {"Saved": "value"}}
    }
    target = DesiredCapabilities(skill_sets=Declaration(()), mcp=Declaration(()))
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert preview.categories[0].removals == ("mcp/mcp.one", "skill_sets/expert")
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    assert world.snapshot().mcp_overrides == {}
    assert world.snapshot().installed_mcp_codes == frozenset()


@pytest.mark.asyncio
async def test_center_uuid_and_legacy_by_name_reuse_ready_without_market_refresh():
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        CenterSkillByCode,
        CenterSkillByName,
        CenterSkillRef,
    )

    world = CapabilityWorld()
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("weather", CenterSkillByCode("weather")),))
    )
    assert (
        not (await apply(world.ctx, world.materialiser, target)).categories[0].aborted
    )
    asset = world.skills.get_by_git_path("center://weather")
    for source in (
        CenterSkillRef(asset["skill_uuid"]),
        CenterSkillByName(),
        CenterSkillByCode("weather"),
    ):
        result = await apply(
            world.ctx,
            world.materialiser,
            DesiredCapabilities(skills=Declaration((DesiredSkill("weather", source),))),
        )
        assert [
            (e.identity, e.outcome.value) for e in result.categories[0].entries
        ] == [("skills/weather", "unchanged")]
    assert (
        len(world.gateway.calls) == 1
    )  # Preparation consumes verified preview bytes without another fetch.


@pytest.mark.asyncio
async def test_other_engine_raw_local_membership_survives_current_collection_replace():
    from agentclaw.community.core.apply_engine.desired_state import DesiredSkillSet

    world = CapabilityWorld()
    target = DesiredCapabilities(
        skill_sets=Declaration(
            (
                DesiredSkillSet(
                    "expert", False, (local_skill(world, "shared-local"),), ()
                ),
            )
        )
    )
    assert (
        not (await apply(world.ctx, world.materialiser, target)).categories[0].aborted
    )
    asset_id = world.snapshot().sets[0].skill_ids[0]
    with world.db.transactional_orm_session() as session:
        other = SkillSet(
            name="other-engine",
            user_id="entity-1",
            bolt_id="bot-1",
            engine_type="hermes",
            is_active=False,
            env="dev",
        )
        session.add(other)
        session.flush()
        session.add(SkillSetSkill(skill_set_id=other.id, skill_id=asset_id, env="dev"))
    target = DesiredCapabilities(skills=Declaration(()), skill_sets=Declaration(()))
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert preview.categories[0].removals == ("skill_sets/expert",)
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    assert world.snapshot().sets == ()
    assert world.uploads.packages.keys() == {"shared-local"}


@pytest.mark.asyncio
async def test_direct_mcp_omitted_config_clears_and_nonempty_config_replaces_complete_override():
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredMcpServer,
        McpOverride,
    )

    world = CapabilityWorld()
    who = dict(bot_id="bot-1", owner_id="entity-1", actor_id="owner", apply_id="seed")
    await world.activation.claim_apply_mcp(
        **who,
        server_code="mcp.one",
        config={"headers": {"Old": "value", "Keep": "old"}},
    )
    target = DesiredCapabilities(
        mcp=Declaration(
            (
                DesiredMcpServer(
                    "mcp.one", McpOverride.from_config({"headers": {"Keep": "new"}})
                ),
            )
        )
    )
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    assert world.snapshot().mcp_overrides == {"mcp.one": {"headers": {"Keep": "new"}}}
    target = DesiredCapabilities(mcp=Declaration((DesiredMcpServer("mcp.one"),)))
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    assert world.snapshot().mcp_overrides == {}
    assert world.snapshot().installed_mcp_codes == frozenset({"mcp.one"})


@pytest.mark.asyncio
async def test_distinct_target_set_assets_same_runtime_name_fail_on_set_rows():
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        DesiredSkillSet,
        RepoSkillPath,
    )

    world = CapabilityWorld()
    world.shared("review", "one")
    world.shared("review", "two")
    target = DesiredCapabilities(
        skill_sets=Declaration(
            (
                DesiredSkillSet(
                    "one", True, (DesiredSkill("review", RepoSkillPath("one")),), ()
                ),
                DesiredSkillSet(
                    "two", True, (DesiredSkill("review", RepoSkillPath("two")),), ()
                ),
            )
        )
    )
    result = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert result.categories[0].aborted
    assert {e.identity for e in result.categories[0].entries} == {
        "skill_sets/one",
        "skill_sets/two",
    }
    assert world.snapshot().sets == ()


@pytest.mark.asyncio
async def test_direct_shared_replace_of_local_same_name_reports_update_without_runtime_name_removal():
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        RepoSkillPath,
    )

    world = CapabilityWorld()
    assert (
        not (
            await apply(
                world.ctx,
                world.materialiser,
                DesiredCapabilities(
                    skills=Declaration((local_skill(world, "review"),))
                ),
            )
        )
        .categories[0]
        .aborted
    )
    shared = world.shared("review", "review")
    target = DesiredCapabilities(
        skills=Declaration((DesiredSkill("review", RepoSkillPath("review")),))
    )
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert [(e.identity, e.outcome.value) for e in preview.categories[0].entries] == [
        ("skills/review", "updated")
    ]
    assert preview.categories[0].removals == ()
    result = await apply(world.ctx, world.materialiser, target)
    assert [(e.identity, e.outcome.value) for e in result.categories[0].entries] == [
        ("skills/review", "updated")
    ]
    assert result.categories[0].removals == ()
    assert world.snapshot().installed_skill_ids == frozenset({int(shared["id"])})


@pytest.mark.asyncio
async def test_policy_default_without_persistent_default_needs_no_installation_repair():
    from .test_unified_capabilities import rig

    _service, _sets, ctx, materialiser = rig()
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    preview = await apply(ctx, materialiser, target, dry_run=True)
    assert [(e.identity, e.outcome.value) for e in preview.categories[0].entries] == [
        ("skill_sets/default", "unchanged")
    ]
