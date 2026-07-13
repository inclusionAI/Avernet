"""TestDevicesModule — corp-free local/SQLite overrides for the devices module (B11).

The corp-free counterpart of ``TestingDevicesModule`` (which imports
``agentclaw.corp`` for the MagicMock ARCA sandbox factory, ``config_corp``, and the
prod device-adapter transport). This module is used by the ``test``/``singlebox``
column so those profiles ship corp-free; ``corp_test`` keeps ``TestingDevicesModule``.

Differences from ``TestingDevicesModule``:

- ``aidesktop_root`` comes from the neutral ``path_factory._get_aidesktop_root()``
  (same env > YAML > default precedence as the corp ``DeviceLocalConfig``) instead
  of injecting ``config_corp.DeviceLocalConfig``.
- No ``ArcaSandboxFactory`` provider — nothing in the community/test graph injects
  it (it is a corp arca-device dependency), so it is simply dropped.
- ``DeviceAdapterTransport`` binds the community ``InMemoryDeviceAdapterTransport``
  unconditionally (the corp ``HttpDeviceAdapterTransport`` singlebox branch is
  dropped; see memory ``project-corp-singlebox-prod-modules`` for the deferred
  corp-singlebox follow-up).
"""
from __future__ import annotations

from typing import Callable, cast  # noqa: UP035 - injector binding key must match provider side

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.baas_service import BaasServiceProtocol
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.services.data_init_service import DataInitService
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
from agentclaw.community.core.devices.services.baas_publish_poller import BaasPublishPoller
from agentclaw.community.core.devices.services.device_service import (
    LOCAL_DEVICE_PROVIDER,
    DeviceService,
)
from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.core.devices.services.local_device_accessor import LocalDeviceAccessor
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.notify.protocol import NotifyBotLister
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.workspace.path_factory import _get_aidesktop_root
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient
from agentclaw.community.plugins.local.local_device_lifecycle import LocalDeviceLifecycle


logger = get_logger()


def configure_local_device_test_runtime(binder: Binder) -> None:
    """Bind the local device runtime shared by TEST and CORP_TEST.

    Keep these bindings together so the corp-flavored test module cannot drift
    from the corp-free module when device concerns move between DI modules.
    SINGLEBOX does not call this helper; it uses ``SingleboxDevicesModule`` and
    the real local BaaS device runtime instead.
    """
    from agentclaw.community.plugin_api.device_connection_manager import (
        DeviceConnectionManagerPlugin,
    )
    from agentclaw.community.plugins.local.device_connection_manager import (
        NoopDeviceConnectionManagerPlugin,
    )

    binder.bind(
        DeviceConnectionManagerPlugin,
        to=NoopDeviceConnectionManagerPlugin,
        scope=singleton,
    )
    binder.bind(LocalDeviceAccessor, to=LocalDeviceAccessor, scope=singleton)
    binder.bind(DeviceAccessor, to=LocalDeviceAccessor, scope=singleton)
    binder.bind(LocalDeviceLifecycle, to=LocalDeviceLifecycle, scope=singleton)


class TestDevicesModule(Module):
    """Corp-free SQLite + local-mode overrides for devices (test / singlebox)."""

    def configure(self, binder: Binder) -> None:
        """Rebind the prod Moltis ``DeviceConnectionManagerPlugin`` to the Noop.

        ``DevicesModule`` unconditionally binds the prod Moltis impl; local/SQLite
        boots have no remote device gateway, so rebind to the Noop impl here (a
        binding in this module wins over the prod module's).
        """
        configure_local_device_test_runtime(binder)

    @singleton
    @provider
    @inject
    def device_service(
        self,
        repository: DeviceBindingRepository,
        baas_service: BaasService,
        publish_poller: BaasPublishPoller,
        oss_record_repo: OssToNasRecordRepository,
        bot_repository: BotRepository,
        bot_service: BotService,
        mcp_sync_service: MCPSyncService,
        arca_baas_rollout_policy: ArcaBotCreateBaasRolloutPolicy,
        data_init_service_factory: Callable[[], DataInitService],
        token_vault: TokenVault,
        passport_plugin: PassportPlugin,
        sandbox_client: SandboxRuntimeClient,
    ) -> DeviceService:
        """Local-only ``DeviceServiceRouter`` build (singlebox via BaaS)."""
        from agentclaw.community.core.devices.services.local_device_service import (
            LocalDeviceService,
        )

        bot_query = cast(BotQueryProtocol, bot_repository)
        bot_sync = cast(BotSyncProtocol, bot_service)
        mcp_sync = cast(McpSyncProtocol, mcp_sync_service)

        local_service = LocalDeviceService(
            repository,
            baas_service=baas_service,
            publish_poller=publish_poller,
            # aidesktop_root via the neutral path_factory (env > YAML > default),
            # matching the corp DeviceLocalConfig precedence without the corp import.
            config={"aidesktop_root": str(_get_aidesktop_root())},
            bot_query=bot_query,
            bot_sync=bot_sync,
            oss_record_repo=oss_record_repo,
            mcp_sync=mcp_sync,
            vault=token_vault,
        )

        providers: dict[str, DeviceService] = {
            LOCAL_DEVICE_PROVIDER: local_service,
        }

        for _svc in providers.values():
            _svc._data_init_service_provider = data_init_service_factory

        return DeviceServiceRouter(
            repository=repository,
            bot_query=bot_query,
            providers=providers,
            default_provider_key=LOCAL_DEVICE_PROVIDER,
            arca_baas_rollout_policy=arca_baas_rollout_policy,
            passport_plugin=passport_plugin,
            sandbox_client=sandbox_client,
        )

    @singleton
    @provider
    def arca_bot_create_baas_rollout_policy(self) -> ArcaBotCreateBaasRolloutPolicy:
        """Local/test rollout seam routes directly to LocalDeviceService."""

        class _LocalArcaBotCreateBaasRolloutPolicy(ArcaBotCreateBaasRolloutPolicy):
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
                    target_provider=LOCAL_DEVICE_PROVIDER,
                    reason="local_create_policy",
                    engine_bucket=self.normalize_engine_bucket(
                        engine_type=engine_type,
                        template_type=template_type,
                    ),
                )

        return _LocalArcaBotCreateBaasRolloutPolicy()

    @singleton
    @provider
    @inject
    def baas_publish_poller(
        self,
        baas_service: BaasService,
        injector: Injector,
    ) -> BaasPublishPoller:
        """BaasPublishPoller — DeviceService lazy provider to avoid a cycle."""
        return BaasPublishPoller(
            baas_service=baas_service,
            device_service_provider=lambda: injector.get(DeviceService),
        )

    @singleton
    @provider
    @inject
    def notify_bot_lister(
        self,
        bot_repository: BotRepository,
    ) -> NotifyBotLister:
        from agentclaw.community.core.notify.local_bot_lister import LocalNotifyBotLister

        return LocalNotifyBotLister(bot_repository=bot_repository)

    @singleton
    @provider
    def device_adapter_transport(
        self, baas_service: BaasServiceProtocol
    ) -> DeviceAdapterTransport:
        """Corp-free: the community ``InMemoryDeviceAdapterTransport`` always.

        The gateway contract tests (Rule 13/25) need the in-memory store, and the
        corp-free column has no corp ``HttpDeviceAdapterTransport``. The corp
        singlebox real-transport branch is deferred (see memory
        ``project-corp-singlebox-prod-modules``).
        """
        from agentclaw.community.plugins.local.device_adapter_transport import (
            InMemoryDeviceAdapterTransport,
        )

        return InMemoryDeviceAdapterTransport()
