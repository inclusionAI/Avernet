"""Manifest installs do not depend on personal MCP invocation rights."""

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DefaultSkillSetRef,
    DesiredCapabilities,
    DesiredMcpServer,
    DesiredSkillSet,
)
from agentclaw.community.core.skill_center.mcp_apply_policy import (
    manifest_mcp_install_scope,
)
from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion
from ._capabilities_world import CapabilityWorld
from .test_unified_capabilities import apply


@pytest.mark.asyncio
@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
async def test_manifest_installs_without_personal_permission(direct, dry_run):
    world = CapabilityWorld(device=True)

    def unavailable(*_):
        raise RuntimeError("personal permission service unavailable")

    world.auth.check_mcp_permission_detail = unavailable
    world.center.check_mcp_permission_detail = unavailable
    target = (
        DesiredCapabilities(mcp=Declaration((DesiredMcpServer("denied"),)))
        if direct
        else DesiredCapabilities(
            skill_sets=Declaration(
                (DesiredSkillSet("ordinary", True, (), ("denied",)),)
            )
        )
    )
    with manifest_mcp_install_scope():
        result = await apply(world.ctx, world.materialiser, target, dry_run=dry_run)
    assert not result.categories[0].aborted
    if not dry_run:
        assert "denied" in world.snapshot().installed_mcp_codes


@pytest.mark.asyncio
async def test_manifest_restores_default_when_permission_service_is_unavailable():
    world = CapabilityWorld(device=True)

    def unavailable(*_):
        raise RuntimeError("personal permission service unavailable")

    world.auth.check_mcp_permission_detail = unavailable
    world.center.check_mcp_permission_detail = unavailable
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
            SkillSetMCPServer(
                skill_set_id=group.id, server_code="denied", name="denied", env="dev"
            )
        )
        session.add(
            DefaultSkillsetMcpExclusion(
                user_id="entity-1",
                bot_id="bot-1",
                skill_set_id=group.id,
                server_code="denied",
            )
        )
    with manifest_mcp_install_scope():
        result = await apply(
            world.ctx,
            world.materialiser,
            DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),))),
        )
    assert not result.categories[0].aborted
    assert "denied" in world.snapshot().installed_mcp_codes


@pytest.mark.asyncio
async def test_manifest_still_rejects_missing_catalog_entry():
    world = CapabilityWorld()
    world.center.get_mcp_detail = lambda _: None
    with manifest_mcp_install_scope():
        result = await apply(
            world.ctx,
            world.materialiser,
            DesiredCapabilities(mcp=Declaration((DesiredMcpServer("missing"),))),
        )
    assert result.categories[0].aborted
    assert not world.snapshot().installed_mcp_codes
