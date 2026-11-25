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
)
from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile


@pytest.mark.parametrize("profile", [DeployProfile.TEST, DeployProfile.COMMUNITY])
def test_actual_composition_resolves_risk_service_and_builds_mcp_identity(
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
    builder.capabilities.active_capabilities.return_value = SimpleNamespace(
        owner_id="entity", skills=(), installed_mcp_server_codes=frozenset()
    )
    data, _ = builder.build(
        bot=bot,
        target={"visibility": "public", "public_scope": "user", "view_depts": []},
    )
    item = data["agent_info"]["resourceManifest"][0]
    assert item["resourcePlatform"] == "MCPCenter"
    assert item["resourceConfig"] == {"identityMode": "OWNER"}
