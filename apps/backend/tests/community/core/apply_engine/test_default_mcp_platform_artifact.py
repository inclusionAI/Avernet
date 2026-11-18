"""Default supply reaches the Teclaw artifact even when invocation is denied."""

import pytest

from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.config_compose.models import ComposeRequest
from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion
from ._mcp_config_support import McpWorld
from .test_mcp_artifact_contract import _artifact_composer


@pytest.mark.asyncio
async def test_restored_denied_default_is_in_platform_artifact(tmp_path):
    world = McpWorld(platform=True)
    world.center.check_mcp_permission_detail = lambda *_: {
        "has_permission": False,
        "access_level": "PRIVATE",
    }
    with world.db.transactional_orm_session() as session:
        group = SkillSet(
            name="Default",
            user_id=None,
            bolt_id="",
            engine_type="teclaw",
            is_default=True,
            is_active=True,
            env="dev",
        )
        session.add(group)
        session.flush()
        session.add(
            SkillSetMCPServer(
                skill_set_id=group.id,
                server_code="mcp.denied",
                name="Denied",
                env="dev",
            )
        )
        session.add(
            DefaultSkillsetMcpExclusion(
                user_id="entity-1",
                bot_id="bot-1",
                skill_set_id=group.id,
                server_code="mcp.denied",
            )
        )
    before = _artifact_composer(world, tmp_path).compose(
        ComposeRequest(
            entity_id="entity-1", user_id="owner", bot_id="bot-1", engine_type="teclaw"
        )
    )
    assert "mcp.denied" not in {server.server_code for server in before.mcp.servers}
    assert (await world.apply_manifest([])).status is ApplyStatus.SUCCEEDED
    snapshot = world.sets.read_apply_snapshot(
        bot_id="bot-1", owner_id="entity-1", actor_id="owner"
    )
    assert "mcp.denied" in snapshot.installed_mcp_codes
    artifact = _artifact_composer(world, tmp_path).compose(
        ComposeRequest(
            entity_id="entity-1",
            user_id="owner",
            bot_id="bot-1",
            engine_type="teclaw",
        )
    )
    server = next(
        server for server in artifact.mcp.servers if server.server_code == "mcp.denied"
    )
    assert server.endpoint == "https://center.example/prod/mcp.denied"
    assert server.transport == "http"
    assert (await world.apply_manifest([])).status is ApplyStatus.SUCCEEDED
    assert (
        "mcp.denied"
        in world.sets.read_apply_snapshot(
            bot_id="bot-1", owner_id="entity-1", actor_id="owner"
        ).installed_mcp_codes
    )
    again = _artifact_composer(world, tmp_path).compose(
        ComposeRequest(
            entity_id="entity-1", user_id="owner", bot_id="bot-1", engine_type="teclaw"
        )
    )
    assert any(server.server_code == "mcp.denied" for server in again.mcp.servers)
