"""The MCP-owned effective-state port resolves through its production binding."""

from unittest.mock import MagicMock

from injector import Binder, Injector, InstanceProvider, Module

from agentclaw.community.core.mcp.effective_mcp_state_reader_protocol import (
    EffectiveMCPStateReaderProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.mcp_default_exclusion import (
    MCPDefaultExclusionReaderProtocol,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilitySnapshot,
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.policies.platform_default_mcp import (
    PlatformDefaultMcpPolicy,
)
from agentclaw.community.core.skill_center.services.effective_mcp_state_reader import (
    EffectiveMCPStateReader,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset
from agentclaw.community.di.modules.mcp_module import McpModule


class _Dependencies(Module):
    def __init__(self) -> None:
        self.capabilities = MagicMock()
        self.exclusions = MagicMock()
        self.bots = MagicMock()
        self.defaults = MagicMock()

    def configure(self, binder: Binder) -> None:
        binder.bind(
            BotCapabilityStateReaderProtocol,
            to=InstanceProvider(self.capabilities),
        )
        binder.bind(
            MCPDefaultExclusionReaderProtocol,
            to=InstanceProvider(self.exclusions),
        )
        binder.bind(BotRepository, to=InstanceProvider(self.bots))
        binder.bind(
            PlatformDefaultMcpPolicy,
            to=InstanceProvider(self.defaults),
        )


def test_production_binding_unions_all_runtime_mcp_sources_and_exclusions():
    dependencies = _Dependencies()
    dependencies.capabilities.active_capabilities.return_value = (
        BotCapabilitySnapshot(
            bot_id="bot-1",
            owner_id="owner",
            skills=(
                RegisteredSkillAsset(
                    skill_id=1,
                    name="qa",
                    git_path="local://qa",
                    mcp_dependencies=({"server_code": "mcp.dependency"},),
                ),
            ),
            installed_mcp_server_codes=frozenset({"mcp.explicit"}),
        )
    )
    dependencies.exclusions.list_excluded_server_codes.return_value = frozenset(
        {"mcp.excluded"}
    )
    dependencies.defaults.server_codes_for.return_value = frozenset(
        {"mcp.default", "mcp.excluded"}
    )
    reader = Injector([McpModule(), dependencies]).get(
        EffectiveMCPStateReaderProtocol
    )

    codes = reader.effective_mcp_server_codes(
        bot_id="bot-1",
        owner_id="owner",
        bot={
            "bot_id": "bot-1",
            "owner_id": "owner",
            "env": "dev",
            "active_engine": "openclaw",
        },
    )

    assert isinstance(reader, EffectiveMCPStateReader)
    assert codes == frozenset(
        {"mcp.explicit", "mcp.default", "mcp.dependency"}
    )
