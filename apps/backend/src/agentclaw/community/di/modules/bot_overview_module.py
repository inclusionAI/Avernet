"""BotOverviewModule — the Bot overview service and its per-engine providers."""

from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.api.bot_overview_service import BotOverviewServiceProtocol
from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AICODING_ENGINE_TYPE,
    CLAUDE_CODE_ENGINE_TYPE,
)
from agentclaw.community.core.bot_overview.engines.aicoding import (
    AICodingBotOverviewProvider,
)
from agentclaw.community.core.bot_overview.provider import BotOverviewProviderRegistry
from agentclaw.community.core.bot_overview.service import BotOverviewService
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    TemplateRepository,
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
    def provide_bot_overview_service(
        self,
        bot_repo: BotRepository,
        providers: BotOverviewProviderRegistry,
    ) -> BotOverviewServiceProtocol:
        return BotOverviewService(bot_repo=bot_repo, providers=providers)
