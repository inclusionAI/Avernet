"""Offline delivery binding for tests; no separate coordination implementation."""
from collections.abc import Mapping
from dataclasses import dataclass

from injector import Binder, Module, provider, singleton

from agentclaw.community.core.bot_collaborator.manager_sync.service import BotManagerSyncService
from agentclaw.community.core.bot_management.ownership_sync.service import BotOwnershipSyncService
from agentclaw.community.core.repository.protocols.bot.manager_sync import ManagerSyncReadRepository
from agentclaw.community.core.repository.protocols.bot.ownership_sync import OwnershipSyncReadRepository
from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig
from agentclaw.community.di.modules.bot_manager_sync_module import ManagerSyncStack
from agentclaw.community.di.modules.collaboration_repair_module import (
    CollaborationRepairPlugins,
)
from agentclaw.community.plugin_api.bcs_collaboration_metadata import BcsCollaborationMetadataPlugin
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipPlugin
from agentclaw.community.plugins.local.bcs_collaboration_metadata import RecordingBcsCollaborationMetadataPlugin
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin
from agentclaw.community.plugins.local.bcs_ownership import RecordingBcsOwnershipPlugin


@dataclass(frozen=True)
class RecordingCollaborationRepairPlugins(CollaborationRepairPlugins):
    """Local/test delivery source: the recording fakes, no BCN or credentials.

    Hands back the injector's recording instances (they hold no async client
    and no loop affinity), so an acceptance flow can inspect the repairs a
    request delivered exactly as it inspects background sync deliveries.

    The factory's per-request ``aclose()`` is **inert** for these shared
    recording singletons: they stay shared across requests, and a sticky
    ``closed`` flag on the recording fake is expected, not a bug.
    """

    metadata_plugin: BcsCollaborationMetadataPlugin
    ownership_plugin: BcsOwnershipPlugin
    managers_plugin: BcsManagersPlugin

    @property
    def available(self) -> bool:
        return True

    @property
    def reason(self) -> str:
        return ""

    def metadata(
        self, profile_headers: Mapping[str, str]
    ) -> BcsCollaborationMetadataPlugin:
        return self.metadata_plugin

    def ownership(self) -> BcsOwnershipPlugin:
        return self.ownership_plugin

    def managers(self) -> BcsManagersPlugin:
        return self.managers_plugin


class TestBotManagerSyncModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(BcsManagersPlugin, to=RecordingBcsManagersPlugin, scope=singleton)
        binder.bind(BcsOwnershipPlugin, to=RecordingBcsOwnershipPlugin, scope=singleton)
        binder.bind(
            BcsCollaborationMetadataPlugin,
            to=RecordingBcsCollaborationMetadataPlugin,
            scope=singleton,
        )

    @singleton
    @provider
    def manager_sync_service(
        self, reader: ManagerSyncReadRepository, bcs: BcsManagersPlugin,
    ) -> BotManagerSyncService:
        return BotManagerSyncService(reader=reader, bcs=bcs)

    @singleton
    @provider
    def ownership_sync_service(
        self, reader: OwnershipSyncReadRepository, bcs: BcsOwnershipPlugin,
    ) -> BotOwnershipSyncService:
        return BotOwnershipSyncService(reader=reader, bcs=bcs)

    @singleton
    @provider
    def manager_sync_stack(
        self, config: ManagerSyncConfig, bcs: BcsManagersPlugin,
    ) -> ManagerSyncStack:
        if not config.enabled:
            return ManagerSyncStack.unavailable("disabled")
        ownership = (
            self.__injector__.get(BcsOwnershipPlugin) if config.ownership_enabled else None
        )
        return ManagerSyncStack.available_with(bcs, ownership=ownership)

    @singleton
    @provider
    def collaboration_repair_plugins(
        self,
        metadata: BcsCollaborationMetadataPlugin,
        ownership: BcsOwnershipPlugin,
        managers: BcsManagersPlugin,
    ) -> CollaborationRepairPlugins:
        """Local profile delivery: recordings only, so no private service."""
        return RecordingCollaborationRepairPlugins(metadata, ownership, managers)
