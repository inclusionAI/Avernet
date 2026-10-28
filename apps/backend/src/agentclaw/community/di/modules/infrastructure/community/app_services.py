"""App-services concern — community binding.

Neutral no-op code-platform / BotChat services and generic current-knowledge
selection routes. The community build has no private platform integrations. The corp ``WorkspaceHostingService`` / ``WorkspaceHostingClient``
are not bound — they are not router-facing and nothing resolvable in the community
column depends on them. Corp-free.
"""

from __future__ import annotations

from agentclaw.community.api.bot_chat_service import BotChatServiceProtocol
from agentclaw.community.api.code_platform_service import CodePlatformServiceProtocol
from agentclaw.community.di.optional_routers import OptionalRouters
from injector import Binder, Module, provider, singleton


class CommunityAppServicesModule(Module):
    """community: neutral external services plus generic knowledge selection routes."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.adapters.http.bot_knowledge.router import router

        binder.bind(
            OptionalRouters,
            to=OptionalRouters(routers=[router]),
            scope=singleton,
        )

    @singleton
    @provider
    def antcode_service(self) -> CodePlatformServiceProtocol:
        from agentclaw.community.plugins.community.app_services import (
            NoopCodePlatformService,
        )

        return NoopCodePlatformService()

    @singleton
    @provider
    def bot_chat_service(self) -> BotChatServiceProtocol:
        from agentclaw.community.plugins.community.app_services import (
            NoopBotChatService,
        )

        return NoopBotChatService()
