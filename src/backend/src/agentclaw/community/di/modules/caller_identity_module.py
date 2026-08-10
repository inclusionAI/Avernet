"""Production-safe bindings for Caller identity domain services."""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.caller_identity_service import (
    CallerIdentityServiceProtocol,
)
from agentclaw.community.api.caller_credential import (
    CallerRuntimeUpdater,
    CallerTokenProvider,
    UnavailableCallerTokenProvider,
)
from agentclaw.community.api.caller_iam_token_service import (
    CallerIamTokenServiceProtocol,
)
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import BotCollabLockRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.caller_identity.protocols import (
    CallerIdentityTokenExchangeProtocol,
    CallerRuntimeUpdaterProtocol,
    CallerTokenProviderProtocol,
)
from agentclaw.community.core.repository.protocols.identity import CallerIdentityRepositoryProtocol
from agentclaw.community.core.caller_identity.service import CallerIdentityService
from agentclaw.community.core.caller_identity.iam_token_service import (
    CallerIamTokenService,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.mcp.services.repositories import BotMCPProvider
from agentclaw.community.core.repository.implementations.identity.caller_identity import CallerIdentityRepository
from agentclaw.community.plugin_api.auth import AuthPlugin


class CallerIdentityModule(Module):
    """Wire the unified repository and fail-closed domain service."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            CallerIdentityRepositoryProtocol,
            to=CallerIdentityRepository,
            scope=singleton,
        )
        binder.bind(
            CallerTokenProvider,
            to=UnavailableCallerTokenProvider,
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
    def caller_identity_token_exchange_protocol(
        self,
        service: CallerIdentityService,
    ) -> CallerIdentityTokenExchangeProtocol:
        """Bind CallerIdentityService to core layer protocol."""
        return service

    @singleton
    @provider
    @inject
    def caller_token_provider_protocol(
        self,
        provider: CallerTokenProvider,
    ) -> CallerTokenProviderProtocol:
        """Bind CallerTokenProvider to core layer protocol."""
        return provider

    @singleton
    @provider
    @inject
    def caller_runtime_updater(
        self,
        baas_service: BaasService,
    ) -> CallerRuntimeUpdater:
        """Use the existing BaaS singleton for the Caller outbound PUT."""
        return baas_service

    @singleton
    @provider
    @inject
    def caller_runtime_updater_protocol(
        self,
        baas_service: BaasService,
    ) -> CallerRuntimeUpdaterProtocol:
        """Bind BaasService to core layer protocol."""
        return baas_service

    @singleton
    @provider
    @inject
    def caller_iam_token_service(
        self,
        caller_identity: CallerIdentityServiceProtocol,
        auth_plugin: AuthPlugin,
        token_provider: CallerTokenProvider,
        runtime_updater: CallerRuntimeUpdater,
    ) -> CallerIamTokenServiceProtocol:
        return CallerIamTokenService(
            caller_identity=caller_identity,
            auth_plugin=auth_plugin,
            token_provider=token_provider,
            runtime_updater=runtime_updater,
        )
