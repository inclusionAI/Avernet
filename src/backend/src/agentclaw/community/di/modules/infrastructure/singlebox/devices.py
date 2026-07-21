"""Singlebox BaaS-only device runtime bindings."""

from __future__ import annotations

from dataclasses import replace
from typing import cast  # noqa: UP035 - injector binding key matches provider side
from urllib.parse import urlsplit

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.devices.models import (
    DeviceConnectionInfo,
    OperatorContext,
)
from agentclaw.community.core.devices.protocols import (
    BotQueryProtocol,
    BotSyncProtocol,
    McpSyncProtocol,
)
from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
    OssToNasRecordRepository,
)
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
from agentclaw.community.core.devices.services.baas_template_resolver import (
    SystemConfigBaasTemplateResolver,
)
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.core.devices.services.device_service import (
    BAAS_DEVICE_PROVIDER,
    LOCAL_DEVICE_PROVIDER,
    DeviceService,
)
from agentclaw.community.core.devices.services.device_service_router import (
    DeviceServiceRouter,
)
from agentclaw.community.core.notify.protocol import NotifyBotLister
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.system_config import SystemConfigService
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
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


class SingleboxBaasDeviceService(BaasDeviceService):
    """Project loopback BaaS connections onto the frontend local transport."""

    @staticmethod
    def _is_loopback_target(target: str) -> bool:
        if not target:
            return False
        try:
            host = urlsplit(f"//{target}").hostname
        except ValueError:
            host = None
        if host is None and target.count(":") >= 2:
            host = target.rsplit(":", 1)[0]
            if host.startswith("[") and host.endswith("]"):
                host = host[1:-1]
        return host in {"localhost", "127.0.0.1", "::1"}

    def get_device_connection(
        self,
        *,
        binding_id: int,
        operator: OperatorContext,
        port: int | None = None,
        ttl: int | None = None,
        device_uuid: str | None = None,
        ws_conn_mode: str | None = None,
    ) -> DeviceConnectionInfo:
        connection = super().get_device_connection(
            binding_id=binding_id,
            operator=operator,
            port=port,
            ttl=ttl,
            device_uuid=device_uuid,
            ws_conn_mode=ws_conn_mode,
        )
        if (
            connection.type == BAAS_DEVICE_PROVIDER
            and self._is_loopback_target(connection.target)
        ):
            return replace(connection, type=LOCAL_DEVICE_PROVIDER)
        return connection


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
    def baas_device_service(
        self,
        repository: DeviceBindingRepository,
        baas_service: BaasService,
        system_config_service: SystemConfigService,
        oss_record_repo: OssToNasRecordRepository,
        bot_repository: BotRepository,
        bot_service: BotService,
        mcp_sync_service: MCPSyncService,
        token_vault: TokenVault,
        task_queue_service: TaskQueueService,
    ) -> BaasDeviceService:
        """Keep BaaS lifecycle wiring while projecting local engine connections."""
        return SingleboxBaasDeviceService(
            repository=repository,
            baas_service=baas_service,
            bot_query=cast(BotQueryProtocol, bot_repository),
            bot_sync=cast(BotSyncProtocol, bot_service),
            oss_record_repo=oss_record_repo,
            mcp_sync=cast(McpSyncProtocol, mcp_sync_service),
            template_resolver=SystemConfigBaasTemplateResolver(system_config_service),
            vault=token_vault,
            task_queue_service=task_queue_service,
        )

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
