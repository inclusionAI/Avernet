"""DI bindings for Space-scoped Bot quota enforcement."""

from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.api.bot_quota_service import BotQuotaServiceProtocol
from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.core.bot_management.services.bot_quota_service import (
    BotQuotaService,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.di import config as cfg
from agentclaw.community.plugin_api.cache import CachePlugin


class BotQuotaModule(Module):
    @singleton
    @provider
    @inject
    def bot_quota_service(
        self,
        repository: BotRepository,
        allocation_config: cfg.DeviceAllocationConfig,
        policy_service: PolicyServiceProtocol,
        cache: CachePlugin,
        space_access: SpaceAccessServiceProtocol,
    ) -> BotQuotaServiceProtocol:
        return BotQuotaService(
            repository=repository,
            allocation_config=allocation_config,
            policy_service=policy_service,
            cache=cache,
            space_access=space_access,
        )
