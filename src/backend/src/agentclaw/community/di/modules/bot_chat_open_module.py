"""Profile-independent bindings for the Bot Logs OpenAPI query stack."""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_chat_service import OpenBotChatServiceProtocol
from agentclaw.community.core.bot_chat.open_service import OpenBotChatService
from agentclaw.community.core.repository.implementations.chat.open import OpenBotChatRepository


class BotChatOpenModule(Module):
    """Bind the OpenAPI service in every deployment profile, including CORP."""

    def configure(self, binder: Binder) -> None:
        binder.bind(OpenBotChatRepository, to=OpenBotChatRepository, scope=singleton)
        binder.bind(OpenBotChatService, to=OpenBotChatService, scope=singleton)

    @singleton
    @provider
    @inject
    def open_bot_chat_service(
        self, service: OpenBotChatService
    ) -> OpenBotChatServiceProtocol:
        return service
