"""Singlebox BaaS-only device runtime bindings."""

from __future__ import annotations

from typing import cast  # noqa: UP035 - injector binding key matches provider side

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.protocols import BotQueryProtocol
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy import (
    ArcaBotCreateBaasRolloutDecision,
    ArcaBotCreateBaasRolloutPolicy,
)
from agentclaw.community.core.devices.services.baas_device_accessor import (
    BaasDeviceAccessor,
)
from agentclaw.community.core.devices.services.baas_device_service import (
    BaasDeviceService,
)
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.core.devices.services.device_service import (
    BAAS_DEVICE_PROVIDER,
    DeviceService,
)
from agentclaw.community.core.devices.services.device_service_router import (
    DeviceServiceRouter,
)
from agentclaw.community.core.notify.protocol import NotifyBotLister
from agentclaw.community.core.system_config import SystemConfigService
from agentclaw.community.di import config as cfg
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)
from agentclaw.community.plugin_api.device_connection_manager import (
    DeviceConnectionManagerPlugin,
)
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient
from agentclaw.community.di.modules.infrastructure.singlebox.template_config import (
    SingleboxBaasTemplateConfigLifecycle,
)


class _SingleboxAllBaasRolloutPolicy(ArcaBotCreateBaasRolloutPolicy):
    """Singlebox has no ARCA provider, so every allocation is BaaS-owned."""

    def __init__(self) -> None:
        pass

    def decide(
        self,
        *,
        user_id: str,
        bot_type: str,
        engine_type: str,
        template_type: str,
    ) -> ArcaBotCreateBaasRolloutDecision:
        return ArcaBotCreateBaasRolloutDecision(
            target_provider=BAAS_DEVICE_PROVIDER,
            reason="singlebox_baas_only",
            engine_bucket=self.normalize_engine_bucket(
                engine_type=engine_type,
                template_type=template_type,
            ),
        )


class SingleboxDevicesModule(Module):
    """Bind singlebox to the real BaaS domain provider over local HTTP."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.local.device_connection_manager import (
            NoopDeviceConnectionManagerPlugin,
        )
        from agentclaw.community.plugins.local.device_adapter_transport import (
            InMemoryDeviceAdapterTransport,
        )

        binder.bind(
            DeviceConnectionManagerPlugin,
            to=NoopDeviceConnectionManagerPlugin,
            scope=singleton,
        )
        binder.bind(BaasDeviceAccessor, to=BaasDeviceAccessor, scope=singleton)
        binder.bind(
            DeviceAdapterTransport,
            to=InMemoryDeviceAdapterTransport,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def device_accessor(self, accessor: BaasDeviceAccessor) -> DeviceAccessor:
        """Expose the concrete singleton through the device-access boundary."""
        return accessor

    @singleton
    @provider
    @inject
    def singlebox_baas_template_config_lifecycle(
        self,
        config_service: SystemConfigService,
        baas: cfg.BaasConfig,
    ) -> SingleboxBaasTemplateConfigLifecycle:
        return SingleboxBaasTemplateConfigLifecycle(
            config_service=config_service,
            template_uuid=baas.template_uuid,
        )

    @singleton
    @provider
    @inject
    def device_service(
        self,
        baas_device_service: BaasDeviceService,
        repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        bot_publish_repo: BotPublishRepositoryProtocol,
        passport_plugin: PassportPlugin,
        sandbox_client: SandboxRuntimeClient,
    ) -> DeviceService:
        bot_query = cast(BotQueryProtocol, bot_repository)
        providers: dict[str, DeviceService] = {
            BAAS_DEVICE_PROVIDER: baas_device_service,
        }
        return DeviceServiceRouter(
            repository=repository,
            bot_query=bot_query,
            providers=providers,
            default_provider_key=BAAS_DEVICE_PROVIDER,
            arca_baas_rollout_policy=_SingleboxAllBaasRolloutPolicy(),
            passport_plugin=passport_plugin,
            sandbox_client=sandbox_client,
            publish_repo=bot_publish_repo,
            bot_repo=bot_repository,
        )

    @singleton
    @provider
    @inject
    def notify_bot_lister(self, bot_repository: BotRepository) -> NotifyBotLister:
        from agentclaw.community.core.notify.local_bot_lister import (
            LocalNotifyBotLister,
        )

        return LocalNotifyBotLister(bot_repository=bot_repository)
