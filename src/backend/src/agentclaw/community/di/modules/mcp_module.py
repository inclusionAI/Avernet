"""McpModule — production singletons for the mcp module.

Bindings:

- ``UserMCPConfigRepository`` — database-mode-keyed; production binds
  the ZDAS impl, :class:`TestingMcpModule` overrides with the SQLite impl.
- ``MCPAuthPlugin`` is bound **per profile** by the infrastructure mcp_center
  column module (corp=Prod, community=permissive, test=Local) — not here, so the
  base module pulls no ``plugins.prod`` auth import.
- The four services (``MCPMarketService``, ``MCPAuthService``,
  ``MCPConfigService``, ``MCPSyncService``) — mode-agnostic
  ``@singleton`` providers with ``@inject``.

``BotMCPProvider`` is satisfied structurally by ``SkillSetService``
(see ``core/mcp/services/repositories.py:15``). The provider here
calls ``SkillSetServiceFactory.create()`` with no args — preserving
exact legacy behaviour from
``core/mcp/dependencies/__init__.py:get_mcp_sync_service``. The
no-arg service is stateless w.r.t. the four MCP-related Protocol
methods (callers always pass explicit ``entity_id`` / ``bot_id`` /
``engine_type``).
"""
from __future__ import annotations

from typing import Callable

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.mcp_auth_service import MCPAuthServiceProtocol
from agentclaw.community.api.mcp_config_service import MCPConfigServiceProtocol
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.mcp.services.auth_service import MCPAuthService
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.mcp.services.market_service import MCPMarketService
from agentclaw.community.core.mcp.services.repositories import BotMCPProvider
from agentclaw.community.core.repository.protocols.bot import UserMCPConfigRepository
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.core.repository.implementations.bot.user_mcp_config import UserMCPConfigRepository as UnifiedUserMCPConfigRepository


logger = get_logger()


class McpModule(Module):
    """Production bindings for the mcp module."""

    def configure(self, binder: Binder) -> None:
        """Register the four MCP services as singletons.

        For three of them, ``@inject`` on ``__init__`` is enough — the
        injector handles construction by reading the typed params.
        ``MCPSyncService`` is wired via the explicit ``@provider`` method
        below because it needs lazy thunks to break a construction-time
        DI cycle (see ``mcp_sync_service`` provider docstring).
        """
        binder.bind(MCPMarketService, to=MCPMarketService, scope=singleton)
        binder.bind(MCPAuthService, to=MCPAuthService, scope=singleton)
        binder.bind(MCPConfigService, to=MCPConfigService, scope=singleton)
        # MCP delivery now rides the ``DeviceSyncPlugin`` boundary (folded in
        # from the former ``DeviceMCPSyncPlugin``); ``MCPSyncService`` resolves a
        # per-bot plugin via ``DeviceContextResolver`` + ``DeviceSyncDispatcher``.
        # No separate device-MCP-sync binding.
        # Single unified ORM repository for both runtimes — differs only
        # by the injected ``DatabasePlugin`` (``orm_session()``). No
        # ``TestingMcpModule`` override.
        binder.bind(
            UserMCPConfigRepository,
            to=UnifiedUserMCPConfigRepository,
            scope=singleton,
        )
        # ``MCPAuthPlugin`` is bound per-profile (corp=Prod, community=permissive,
        # test=Local) by the infrastructure mcp_center column module — not here,
        # so the base module carries no plugins.prod auth import.
    # ── Cross-Protocol bindings ───────────────────────────────────────────

    @singleton
    @provider
    @inject
    def bot_mcp_provider(self, factory: SkillSetServiceFactory) -> BotMCPProvider:
        # Mirrors legacy get_mcp_sync_service: call create() with no
        # args. The resulting SkillSetService is stateless w.r.t. the
        # BotMCPProvider Protocol methods (each takes entity_id /
        # bot_id / engine_type as method args; self-state fallbacks
        # never fire because callers pass explicit values).
        return factory.create()

    # Cycle break: MCPSyncService receives this lazy lambda instead of
    # BotMCPProvider directly, so its construction doesn't transitively
    # trigger SkillSetServiceFactory → BotMCPProvider resolution.
    @singleton
    @provider
    @inject
    def bot_mcp_provider_factory(
        self, injector: Injector
    ) -> Callable[[], BotMCPProvider]:
        return lambda: injector.get(BotMCPProvider)

    @singleton
    @provider
    @inject
    def mcp_sync_service(
        self,
        mcp_provider_factory: Callable[[], BotMCPProvider],
        mcp_center: MCPCenterPlugin,
        user_mcp_config_repo: UserMCPConfigRepository,
        passport_update: PassportPlugin,
        mcp_config_service: MCPConfigService,
        bot_repository: BotRepository,
        injector: Injector,
    ) -> MCPSyncService:
        """Cycle break: lazy thunks for ``DeviceContextResolver`` /
        ``DeviceSyncDispatcher`` so ``MCPSyncService`` construction does not
        transitively trigger the
        ``MCPSyncService → DeviceContextResolver → ArcaConnInfoBuilder
        → DeviceService → BotService → SkillSetServiceFactory → MCPSyncService``
        DI cycle. Same手法 as ``bot_mcp_provider_factory`` above and
        ``SkillSetServiceFactory.resolver_provider``.
        """
        return MCPSyncService(
            mcp_provider_factory=mcp_provider_factory,
            mcp_center=mcp_center,
            user_mcp_config_repo=user_mcp_config_repo,
            passport_update=passport_update,
            mcp_config_service=mcp_config_service,
            bot_repository=bot_repository,
            resolver_provider=lambda: injector.get(DeviceContextResolver),
            device_sync_dispatcher_provider=lambda: injector.get(DeviceSyncDispatcher),
        )

    @singleton
    @provider
    @inject
    def _mcp_auth_service_protocol(self, svc: MCPAuthService) -> MCPAuthServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _mcp_config_service_protocol(self, svc: MCPConfigService) -> MCPConfigServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _mcp_market_service_protocol(self, svc: MCPMarketService) -> MCPMarketServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _mcp_sync_service_protocol(self, svc: MCPSyncService) -> MCPSyncServiceProtocol:
        return svc
