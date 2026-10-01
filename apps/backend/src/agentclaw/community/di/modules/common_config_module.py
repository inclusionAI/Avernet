"""CommonConfigModule — production singletons for common_config."""

from __future__ import annotations

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.beta_quota_service import BetaQuotaServiceProtocol
from agentclaw.community.api.common_config_service import CommonConfigServiceProtocol
from agentclaw.community.core.common_config import (
    BetaQuotaService,
    CommonConfigRepositoryProtocol,
    CommonConfigService,
    CommonWhiteListService,
)
from agentclaw.community.core.repository.implementations.config.common_config import (
    CommonConfigRepository,
)


from agentclaw.community.core.repository.protocols.config import (
    BotCommonConfigRepositoryProtocol,
)
from agentclaw.community.core.common_config.bot_config_protocol import (
    BotCommonConfigServiceProtocol,
    BotStoragePolicyProtocol,
)
from agentclaw.community.core.common_config.bot_config_service import (
    BotCommonConfigService,
    BotStoragePolicyService,
)
from agentclaw.community.core.repository.implementations.config.bot_common_config import (
    BotCommonConfigRepository,
)


class CommonConfigModule(Module):
    """Production bindings for common_config."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            BotCommonConfigRepositoryProtocol,
            to=BotCommonConfigRepository,
            scope=singleton,
        )
        binder.bind(
            BotCommonConfigServiceProtocol, to=BotCommonConfigService, scope=singleton
        )
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

    @singleton
    @provider
    @inject
    def bot_storage_policy_service(
        self,
        repository: BotCommonConfigRepositoryProtocol,
        bot_config: BotCommonConfigServiceProtocol,
        common_config: CommonConfigServiceProtocol,
        injector: Injector,
    ) -> BotStoragePolicyService:
        from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy import (
            ArcaBotCreateBaasRolloutPolicy,
        )
        from agentclaw.community.core.devices.services.baas_template_resolver import (
            SystemConfigBaasTemplateResolver,
        )
        from agentclaw.community.core.system_config.service import SystemConfigService
        from agentclaw.community.api.baas_service import BaasServiceProtocol
        from agentclaw.community.utils.env_utils import get_current_env

        template_resolver = SystemConfigBaasTemplateResolver(
            injector.get(SystemConfigService)
        )
        # Lazy access avoids the composer -> policy -> BaaS construction cycle.
        return BotStoragePolicyService(
            repository,
            bot_config,
            common_config,
            env=get_current_env(),
            select_provider=lambda **kw: (
                injector.get(ArcaBotCreateBaasRolloutPolicy)
                .decide(**kw)
                .target_provider
            ),
            resolve_template=template_resolver.resolve_template,
            get_template=lambda uuid: injector.get(
                BaasServiceProtocol
            ).get_device_template(uuid),
        )

    @singleton
    @provider
    @inject
    def bot_storage_policy_protocol(
        self, service: BotStoragePolicyService
    ) -> BotStoragePolicyProtocol:
        return service
