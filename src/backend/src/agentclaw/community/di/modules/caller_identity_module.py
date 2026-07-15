"""Production-safe bindings for Caller identity domain services."""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.caller_identity_service import (
    CallerIdentityServiceProtocol,
)
from agentclaw.community.api.caller_credential import CallerRuntimeUpdater
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.core.bot_collaborator.repository.protocol import (
    BotCollabLockRepositoryProtocol,
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.caller_identity.repository import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.core.caller_identity.service import CallerIdentityService
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.mcp.services.repositories import BotMCPProvider
from agentclaw.community.plugins.caller_identity_repository import (
    CallerIdentityRepository,
)


class CallerIdentityModule(Module):
    """Wire the unified repository and fail-closed domain service."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            CallerIdentityRepositoryProtocol,
            to=CallerIdentityRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def caller_identity_service(
        self,
        bot_repository: BotRepository,
        collaborator_repository: CollaboratorRepositoryProtocol,
        lock_repository: BotCollabLockRepositoryProtocol,
        mcp_provider: BotMCPProvider,
        repository: CallerIdentityRepositoryProtocol,
        mcp_sync_service: MCPSyncServiceProtocol,
    ) -> CallerIdentityService:
        """Construct the draft configuration and Agent Principal sync service."""
        return CallerIdentityService(
            bot_repository=bot_repository,
            collaborator_repository=collaborator_repository,
            lock_repository=lock_repository,
            mcp_provider=mcp_provider,
            repository=repository,
            mcp_sync_service=mcp_sync_service,
        )

    @singleton
    @provider
    @inject
    def caller_identity_service_protocol(
        self,
        service: CallerIdentityService,
    ) -> CallerIdentityServiceProtocol:
        return service

    @singleton
    @provider
    @inject
    def caller_runtime_updater(
        self,
        baas_service: BaasService,
    ) -> CallerRuntimeUpdater:
        """Use the existing BaaS singleton for the Caller outbound PUT."""
        return baas_service
