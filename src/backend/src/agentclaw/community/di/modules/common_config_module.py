"""CommonConfigModule — production singletons for common_config."""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.beta_quota_service import BetaQuotaServiceProtocol
from agentclaw.community.api.common_config_service import CommonConfigServiceProtocol
from agentclaw.community.core.common_config import (
    BetaQuotaService,
    CommonConfigRepositoryProtocol,
    CommonConfigService,
    CommonWhiteListService,
)
from agentclaw.community.core.repository.implementations.config.common_config import CommonConfigRepository


class CommonConfigModule(Module):
    """Production bindings for common_config."""

    def configure(self, binder: Binder) -> None:
        binder.bind(CommonConfigService, to=CommonConfigService, scope=singleton)
        binder.bind(CommonWhiteListService, to=CommonWhiteListService, scope=singleton)
        binder.bind(BetaQuotaService, to=BetaQuotaService, scope=singleton)
        binder.bind(
            CommonConfigRepositoryProtocol,
            to=CommonConfigRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def _common_config_service_protocol(
        self, svc: CommonConfigService
    ) -> CommonConfigServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _beta_quota_service_protocol(
        self, svc: BetaQuotaService
    ) -> BetaQuotaServiceProtocol:
        return svc
