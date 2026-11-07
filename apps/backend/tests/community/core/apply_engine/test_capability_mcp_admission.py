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
from .test_unified_capabilities import apply, local_skill


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
    validation = world.config.validate_bot_override(
        user_id="owner", server_code="mcp.one",
        config=before.mcp_overrides["mcp.one"], engine_type="moltis",
    )
    assert validation["valid"] is True
    assert validation["mcp_data"]["serverCode"] == "mcp.one"
    assert validation["mcp_data"]["endpoints"][0]["url"] == "https://center.example/pre/mcp.one"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("default", [False, True])
@pytest.mark.parametrize("url", [None, ""])
@pytest.mark.parametrize("dry_run", [True, False])
async def test_set_endpoint_without_url_aborts_before_local_upload(
    monkeypatch, default, url, dry_run,
):
    world, _ = _pre_override_world(default=default)
    detail = world.center.get_mcp_detail("mcp.one")
    for endpoint in detail["endpoints"]:
        if url is None:
            endpoint.pop("url")
        else:
            endpoint["url"] = url
    monkeypatch.setattr(world.center, "get_mcp_detail", lambda _code: detail)
    target = DesiredCapabilities(
        skills=Declaration((local_skill(world, "draft"),)),
        skill_sets=Declaration((
            DefaultSkillSetRef()
            if default else DesiredSkillSet("expert", True, (), ("mcp.one",)),
        )),
    )
    before = world.snapshot()

    report = await apply(world.ctx, world.materialiser, target, dry_run=dry_run)

    category = report.categories[0]
    assert category.aborted
    assert any(
        "MCP_NO_COMPATIBLE_SECURE_ENDPOINT" in (entry.reason or "")
        for entry in category.entries
    )
    assert world.uploads.packages == {}
    assert world.skills.get_by_git_path("local://draft") is None
    assert world.snapshot() == before
    assert world.runtime.scopes == []
