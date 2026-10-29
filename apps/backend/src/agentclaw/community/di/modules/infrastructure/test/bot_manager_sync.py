"""Offline delivery binding for tests; no separate coordination implementation."""
from injector import Binder, Module, provider, singleton

from agentclaw.community.core.bot_collaborator.manager_sync.service import BotManagerSyncService
from agentclaw.community.core.repository.protocols.bot.manager_sync import ManagerSyncReadRepository
from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig
from agentclaw.community.di.modules.bot_manager_sync_module import ManagerSyncStack
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin


class TestBotManagerSyncModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(BcsManagersPlugin, to=RecordingBcsManagersPlugin, scope=singleton)

    @singleton
    @provider
    def manager_sync_service(
        self, reader: ManagerSyncReadRepository, bcs: BcsManagersPlugin,
    ) -> BotManagerSyncService:
        return BotManagerSyncService(reader=reader, bcs=bcs)

    @singleton
    @provider
    def manager_sync_stack(
        self, config: ManagerSyncConfig, bcs: BcsManagersPlugin,
    ) -> ManagerSyncStack:
        if not config.enabled:
            return ManagerSyncStack.unavailable("disabled")
        return ManagerSyncStack.available_with(bcs)
