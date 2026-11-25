"""BotOverviewModule — the Bot overview service, its per-engine providers and session reader."""

from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.api.bot_overview_service import BotOverviewServiceProtocol
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AICODING_ENGINE_TYPE,
    CLAUDE_CODE_ENGINE_TYPE,
)
from agentclaw.community.core.bot_overview.engines.aicoding import (
    AICodingBotOverviewProvider,
)
from agentclaw.community.core.bot_overview.provider import BotOverviewProviderRegistry
from agentclaw.community.core.bot_overview.service import BotOverviewService
from agentclaw.community.core.bot_overview.session_activity import (
    BotSessionActivityReaderProtocol,
    EngineSessionActivityReader,
)
from agentclaw.community.core.mcp.effective_mcp_state_reader_protocol import (
    EffectiveMCPStateReaderProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    TemplateRepository,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)


class BotOverviewModule(Module):
    """Engines without a registered provider report only the neutral identity."""

    @singleton
    @provider
    def provide_bot_overview_provider_registry(
        self,
        template_repo: TemplateRepository,
    ) -> BotOverviewProviderRegistry:
        registry = BotOverviewProviderRegistry()
        aicoding = AICodingBotOverviewProvider(template_repo=template_repo)
        registry.register(AICODING_ENGINE_TYPE, aicoding)
        registry.register(CLAUDE_CODE_ENGINE_TYPE, aicoding)
        return registry

    @singleton
    @provider
    def provide_bot_session_activity_reader(
        self,
        relay: EngineRuntimeRelayProtocol,
    ) -> BotSessionActivityReaderProtocol:
        return EngineSessionActivityReader(relay=relay)

    @singleton
    @provider
    def provide_bot_overview_service(
        self,
        bot_repo: BotRepository,
        providers: BotOverviewProviderRegistry,
        capabilities: BotCapabilityStateReaderProtocol,
        mcps: EffectiveMCPStateReaderProtocol,
        sessions: BotSessionActivityReaderProtocol,
    ) -> BotOverviewServiceProtocol:
        return BotOverviewService(
            bot_repo=bot_repo,
            providers=providers,
            capabilities=capabilities,
            mcps=mcps,
            sessions=sessions,
        )
