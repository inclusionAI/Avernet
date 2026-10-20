"""Bindings for managing a bot's acli market CLIs."""
from __future__ import annotations

from typing import Annotated

from injector import Injector, Module, inject, provider, singleton

from agentclaw.community.api.acli_cli_service import AcliCliServiceProtocol
from agentclaw.community.core.acli_clis.arca_port import ArcaAcliPort
from agentclaw.community.core.acli_clis.market import AcliMarketSearch
from agentclaw.community.core.acli_clis.service import AcliCliService
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.mcp.services.cli_capabilities import (
    CliCapabilityManifestResolver,
)
from agentclaw.community.core.mcp.services.cli_passport_scope import (
    CliPassportScopeReconciler,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLockRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.identity import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules.config_module import _block
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from agentclaw.community.plugin_api.http_client import QUALIFIER_GENERAL, HttpClient
from agentclaw.community.plugin_api.passport import PassportPlugin


class AcliClisModule(Module):
    """Compose ``AcliCliService`` from the Passport writer and the engine port."""

    @singleton
    @provider
    def acli_market_config(self) -> cfg.AcliMarketConfig:
        """The acli market's search URL — empty unless the deployment overlay sets
        ``user_config.acli_market.search_url`` (it differs per environment)."""
        search_url = _block("acli_market").get("search_url", "")
        return cfg.AcliMarketConfig(
            search_url=search_url.strip() if isinstance(search_url, str) else "",
        )

    @singleton
    @provider
    @inject
    def acli_cli_service(
        self,
        bot_repository: BotRepository,
        lock_repository: BotCollabLockRepositoryProtocol,
        identity_repository: CallerIdentityRepositoryProtocol,
        passport_plugin: PassportPlugin,
        transport: DeviceAdapterTransport,
        market_config: cfg.AcliMarketConfig,
        http_client: Annotated[HttpClient, QUALIFIER_GENERAL],
        collaborators: CollaboratorServiceProtocol,
        injector: Injector,
    ) -> AcliCliServiceProtocol:
        # Resolved here rather than injected: importing the resolver at module
        # import time closes a cycle through the device services package.
        from agentclaw.community.core.devices.services.device_context_resolver import (
            DeviceContextResolver,
        )
        manifest_resolver = CliCapabilityManifestResolver()
        return AcliCliService(
            bot_repository=bot_repository,
            lock_repository=lock_repository,
            identity_repository=identity_repository,
            scope_reconciler=CliPassportScopeReconciler(
                passport_plugin=passport_plugin,
                identity_repository=identity_repository,
                manifest_resolver=manifest_resolver,
            ),
            manifest_resolver=manifest_resolver,
            port=ArcaAcliPort(
                resolver=injector.get(DeviceContextResolver),
                transport=transport,
            ),
            # The market search needs no container: it calls the configured
            # market API with the general client (no base URL of its own).
            market=AcliMarketSearch(
                http_client=http_client, search_url=market_config.search_url
            ),
            collaborators=collaborators,
        )


__all__ = ["AcliClisModule"]
