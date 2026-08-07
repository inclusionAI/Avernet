"""The Bot Logs stack resolves from the profile-independent DI module."""

from injector import Binder, Injector, Module, singleton

from agentclaw.community.api.bot_chat_service import OpenBotChatServiceProtocol
from agentclaw.community.core.bot_chat.open_service import OpenBotChatService
from agentclaw.community.core.bot_chat.repository import OpenBotChatRepository
from agentclaw.community.di.modules.bot_chat_open_module import BotChatOpenModule
from agentclaw.community.di.container import (
    build_injector,
    eager_check_critical_bindings,
)
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.plugin_api.database import DatabasePlugin


class _DatabaseModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(DatabasePlugin, to=object(), scope=singleton)


def test_profile_independent_open_bot_chat_bindings_resolve():
    injector = Injector([_DatabaseModule(), BotChatOpenModule()])

    service = injector.get(OpenBotChatServiceProtocol)

    assert isinstance(service, OpenBotChatService)
    assert isinstance(service._repository, OpenBotChatRepository)


def test_open_bot_chat_binding_is_in_the_startup_critical_set():
    injector = build_injector(profile=DeployProfile.TEST)

    eager_check_critical_bindings(injector)

    assert isinstance(injector.get(OpenBotChatServiceProtocol), OpenBotChatService)
