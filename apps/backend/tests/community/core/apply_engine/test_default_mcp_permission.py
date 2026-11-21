"""Default supply does not confer or require MCP invocation rights."""

import pytest

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DefaultSkillSetRef,
    DesiredCapabilities,
    DesiredMcpServer,
    DesiredSkillSet,
)
from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion
from ._capabilities_world import CapabilityWorld
from .test_unified_capabilities import apply


@pytest.mark.asyncio
@pytest.mark.parametrize("device", [False, True])
async def test_restore_default_without_invocation_permission(device):
    world = CapabilityWorld(device=device)

    def permission(_actor, code):
        return {"has_permission": code == "allowed", "access_level": "PRIVATE"}

    world.auth.check_mcp_permission_detail = permission
    world.center.check_mcp_permission_detail = permission
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
        for code in ("allowed", "denied"):
            session.add(
                SkillSetMCPServer(
                    skill_set_id=group.id, server_code=code, name=code, env="dev"
                )
            )
            session.add(
                DefaultSkillsetMcpExclusion(
                    user_id="entity-1",
                    bot_id="bot-1",
                    skill_set_id=group.id,
                    server_code=code,
                )
            )
    target = DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),)))
    before = world.snapshot()
    preview = await apply(world.ctx, world.materialiser, target, dry_run=True)
    assert not preview.categories[0].aborted
    assert world.snapshot() == before
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    assert world.snapshot().mcp_exclusions == frozenset()
    assert world.snapshot().installed_mcp_codes == frozenset({"allowed", "denied"})
    if device:
        assert any(scope.mcp for scope in world.runtime.scopes)
    else:
        assert (
            not world.runtime.scopes
        )  # Platform projection is orchestrated as a whole artifact.
    again = await apply(world.ctx, world.materialiser, target)
    assert not again.categories[0].aborted
    assert again.categories[0].entries[0].outcome.value == "unchanged"


@pytest.mark.asyncio
@pytest.mark.parametrize("direct", [False, True])
async def test_explicit_mcp_still_requires_permission(direct):
    world = CapabilityWorld()
    world.auth.check_mcp_permission_detail = lambda *_: {
        "has_permission": False,
        "access_level": "PRIVATE",
    }
    target = (
        DesiredCapabilities(mcp=Declaration((DesiredMcpServer("denied"),)))
        if direct
        else DesiredCapabilities(
            skill_sets=Declaration(
                (DesiredSkillSet("ordinary", True, (), ("denied",)),)
            )
        )
    )
    result = await apply(world.ctx, world.materialiser, target)
    assert result.categories[0].aborted
    assert not world.snapshot().installed_mcp_codes


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["auth_error", "incomplete", "config_error"])
async def test_default_preflight_fails_on_service_or_config_error(failure):
    world = CapabilityWorld()
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
                skill_set_id=group.id,
                server_code="default-mcp",
                name="default-mcp",
                env="dev",
            )
        )
        session.add(
            DefaultSkillsetMcpExclusion(
                user_id="entity-1",
                bot_id="bot-1",
                skill_set_id=group.id,
                server_code="default-mcp",
            )
        )
    if failure == "auth_error":

        def fail(*_args):
            raise RuntimeError("permission service unavailable")

        world.auth.check_mcp_permission_detail = fail
    elif failure == "incomplete":
        world.auth.check_mcp_permission_detail = lambda *_: {
            "has_permission": True,
            "access_level": "",
        }
    else:
        world.config.validate_bot_override = lambda **_: {
            "valid": False,
            "error": "invalid config",
        }
    before = world.snapshot()
    result = await apply(
        world.ctx,
        world.materialiser,
        DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),))),
    )
    assert result.categories[0].aborted
    assert world.snapshot() == before


@pytest.mark.asyncio
async def test_default_also_used_by_direct_skill_dependency_is_retained():
    from agentclaw.community.core.apply_engine.desired_state import (
        DesiredSkill,
        RepoSkillPath,
    )

    world = CapabilityWorld(device=True)
    world.auth.check_mcp_permission_detail = lambda *_: {
        "has_permission": False,
        "access_level": "PRIVATE",
    }
    world.center.check_mcp_permission_detail = world.auth.check_mcp_permission_detail
    world.shared("review", "review", dependencies=({"code": "shared-mcp"},))
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
                skill_set_id=group.id,
                server_code="shared-mcp",
                name="shared-mcp",
                env="dev",
            )
        )
        session.add(
            DefaultSkillsetMcpExclusion(
                user_id="entity-1",
                bot_id="bot-1",
                skill_set_id=group.id,
                server_code="shared-mcp",
            )
        )
    target = DesiredCapabilities(
        skill_sets=Declaration((DefaultSkillSetRef(),)),
        skills=Declaration((DesiredSkill("review", RepoSkillPath("review")),)),
        mcp=Declaration(()),
    )
    result = await apply(world.ctx, world.materialiser, target)
    assert not result.categories[0].aborted
    assert "shared-mcp" in world.snapshot().installed_mcp_codes
    assert any("shared-mcp" in scope.claimed_mcp for scope in world.runtime.scopes)


@pytest.mark.asyncio
async def test_write_service_failure_does_not_clear_exclusion():
    world = CapabilityWorld()
    world.auth.check_mcp_permission_detail = lambda *_: {
        "has_permission": False,
        "access_level": "PRIVATE",
    }

    def fail(*_args):
        raise RuntimeError("permission service unavailable")

    world.center.check_mcp_permission_detail = fail
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
                skill_set_id=group.id,
                server_code="default-mcp",
                name="default-mcp",
                env="dev",
            )
        )
        session.add(
            DefaultSkillsetMcpExclusion(
                user_id="entity-1",
                bot_id="bot-1",
                skill_set_id=group.id,
                server_code="default-mcp",
            )
        )
    before = world.snapshot()
    result = await apply(
        world.ctx,
        world.materialiser,
        DesiredCapabilities(skill_sets=Declaration((DefaultSkillSetRef(),))),
    )
    assert result.categories[0].aborted
    assert world.snapshot() == before
