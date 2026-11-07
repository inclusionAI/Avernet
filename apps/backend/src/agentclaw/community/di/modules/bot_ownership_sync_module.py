"""Ownership composition reuses the manager stack, dispatcher and worker loop."""

from injector import Binder, Module, provider, singleton

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.api.bot_ownership_sync import BotOwnershipSyncProtocol
from agentclaw.community.core.access.admin_scopes import super_admin
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
)
from agentclaw.community.core.bot_management.ownership_sync.notifications import (
    DisabledBotOwnershipSync,
)
from agentclaw.community.core.bot_management.ownership_sync.operations import (
    OwnershipSyncOperations,
)
from agentclaw.community.core.bot_management.ownership_sync.repair import (
    DisabledOwnershipSyncRepair,
    OwnershipSyncRepairService,
)
from agentclaw.community.core.bot_management.ownership_sync.service import (
    BotOwnershipSyncService,
)
from agentclaw.community.core.repository.implementations.bot.ownership_sync import (
    SqlOwnershipSyncReadRepository,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot.ownership_sync import (
    OwnershipSyncReadRepository,
)
from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig
from agentclaw.community.di.modules.bot_manager_sync_module import ManagerSyncStack


class BotOwnershipSyncModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            OwnershipSyncReadRepository,
            to=SqlOwnershipSyncReadRepository,
            scope=singleton,
        )

    @singleton
    @provider
    def ownership_sync_service(self) -> BotOwnershipSyncService:
        stack = self.__injector__.get(ManagerSyncStack)
        if stack.ownership is None:
            raise RuntimeError(
                "ownership sync unavailable: " + (stack.reason or "disabled")
            )
        return BotOwnershipSyncService(
            self.__injector__.get(OwnershipSyncReadRepository),
            stack.ownership,
        )

    @singleton
    @provider
    def ownership_notifier(self) -> BotOwnershipSyncProtocol:
        config = self.__injector__.get(ManagerSyncConfig)
        if not config.ownership_enabled:
            return DisabledBotOwnershipSync()
        if self.__injector__.get(ManagerSyncStack).ownership is None:
            return DisabledBotOwnershipSync()
        entry = self.__injector__.get(BotManagerSyncProtocol)
        if isinstance(entry, BotManagerSyncDispatcher):
            return entry
        return DisabledBotOwnershipSync()

    @singleton
    @provider
    def ownership_sync_repair(self) -> OwnershipSyncRepairService:
        config = self.__injector__.get(ManagerSyncConfig)
        if not config.ownership_enabled:
            return DisabledOwnershipSyncRepair()
        stack = self.__injector__.get(ManagerSyncStack)
        if not stack.available or stack.ownership is None:
            return DisabledOwnershipSyncRepair()
        return OwnershipSyncRepairService(
            reader=self.__injector__.get(OwnershipSyncReadRepository),
            notifier=self.__injector__.get(BotOwnershipSyncProtocol),
        )

    @singleton
    @provider
    def ownership_operations(
        self,
        bots: BotRepository,
        repair: OwnershipSyncRepairService,
        config: ManagerSyncConfig,
    ) -> OwnershipSyncOperations:
        return OwnershipSyncOperations(bots, repair, super_admin(), config.env)
