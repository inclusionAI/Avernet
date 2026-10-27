"""Real composition roots resolve risk application input without fabricating MCPs."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.api.bot_public_risk_service import BotPublicRiskServiceProtocol
from agentclaw.community.core.bot_public_risk.application import (
    RiskInputBuilder as RiskInputProtocol,
)
from agentclaw.community.core.bot_public_risk.input_builder import (
    RiskInputBuilder,
    McpProjectionUnavailable,
)
from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile


@pytest.mark.parametrize("profile", [DeployProfile.TEST, DeployProfile.COMMUNITY])
def test_actual_composition_resolves_risk_service_and_fails_closed_on_missing_mcp_projection(
    profile,
):
    injector = build_injector(profile=profile)
    service = injector.get(BotPublicRiskServiceProtocol)
    builder = injector.get(RiskInputProtocol)
    assert isinstance(builder, RiskInputBuilder)
    assert service._inputs is builder
    builder.identities = Mock()
    builder.identities.list_draft_call_types.return_value = {}
    builder.mcps = Mock()
    builder.mcps.collect_bot_active_mcps.return_value = [
        {"server_code": "local-mcp", "name": "Local MCP"}
    ]
    builder.capabilities = Mock()
    bot = {
        "id": 1,
        "bot_id": "default",
        "entity_id": "entity",
        "env": "dev",
        "owner_id": "owner",
        "active_engine": "openclaw",
        "bot_name": "Bot",
        "creator_id": "owner",
        "ext": {"passport": {"agent_code": "agent"}},
    }
    with pytest.raises(
        McpProjectionUnavailable, match="MCP risk projection is not configured"
    ):
        builder.build(
            bot=bot,
            target={"visibility": "public", "public_scope": "user", "view_depts": []},
        )
    builder.capabilities.active_skill_assets.assert_not_called()

    service._repository = Mock()
    service._repository.get_by_request.return_value = None
    service._tasks = Mock()
    resolved = SimpleNamespace(bot_pk=1, current_owner_id="owner", bot=bot)
    with pytest.raises(
        McpProjectionUnavailable, match="MCP risk projection is not configured"
    ):
        service.submit(
            bot=resolved,
            actor_id="owner",
            surface="openapi",
            body={
                "client_request_id": "r1",
                "visibility": "public",
                "public_scope": "user",
                "view_depts": [],
            },
        )
    service._tasks.start.assert_not_called()
