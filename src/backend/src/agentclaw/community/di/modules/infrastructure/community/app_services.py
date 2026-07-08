"""App-services concern — community binding.

Neutral no-op code-platform / BotChat services (the community build has no git
code-platform integration and no Langfuse-backed trace store) + an empty
``OptionalRouters`` (no
extra routers). The corp ``WorkspaceHostingService`` / ``WorkspaceHostingClient``
are not bound — they are not router-facing and nothing resolvable in the community
column depends on them. Corp-free.
"""
from __future__ import annotations

from injector import Binder, Module, provider, singleton

from agentclaw.community.api.code_platform_service import CodePlatformServiceProtocol
from agentclaw.community.api.bot_chat_service import BotChatServiceProtocol
from agentclaw.community.di.optional_routers import OptionalRouters


class CommunityAppServicesModule(Module):
    """community: neutral no-op code-platform / BotChat + empty OptionalRouters."""

    def configure(self, binder: Binder) -> None:
        # No extra routers in the community build.
        binder.bind(OptionalRouters, to=OptionalRouters(), scope=singleton)

    @singleton
    @provider
    def antcode_service(self) -> CodePlatformServiceProtocol:
        from agentclaw.community.plugins.community.app_services import NoopCodePlatformService

        return NoopCodePlatformService()

    @singleton
    @provider
    def bot_chat_service(self) -> BotChatServiceProtocol:
        from agentclaw.community.plugins.community.app_services import NoopBotChatService

        return NoopBotChatService()
