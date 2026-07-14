"""TestingDevicesModule — local/SQLite overrides for the devices module.

Overrides:

- ``DeviceService`` → a local-only ``DeviceServiceRouter`` build:
  ``Env.DEV``, ``sandbox_factory_getter=None``,
  ``oss_storage=None``. The router skips the Arca/Baas providers when
  those handles are missing, so local boots never reach Arca SDK code.

``DeviceBindingRepository`` is **not** overridden anymore: the
unified ORM body (``plugins/device_repository.py``, S5
2026-05-20) runs on whichever ``DatabasePlugin`` is bound — one impl
for both runtimes. (Was the LAST DB-repo twin pair in the codebase.)

``LocalProcessManager`` is **not** overridden — it's mode-agnostic
and bound by :class:`DevicesModule` for both runtimes.

``BaasDeviceService`` is **not** overridden — it talks to BaaS via
HTTP through the bound :class:`BaasService` (which is itself
mode-agnostic at the binding level; tests construct mocks directly
when they want to avoid the real HTTP path).
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
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.notify.protocol import NotifyBotLister
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.di.modules.infrastructure.test.devices import (
    configure_local_device_test_runtime,
)
from agentclaw.corp.di import config_corp as ccfg
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient
from agentclaw.corp.plugins.prod.arca_factory import ArcaSandboxFactory


logger = get_logger()


class TestingDevicesModule(Module):
    """SQLite + local-mode overrides for devices."""

    def configure(self, binder: Binder) -> None:
        """Override the prod Moltis ``DeviceConnectionManager`` binding.

        ``DevicesModule`` unconditionally binds the prod Moltis impl under
        the ``DeviceConnectionManagerPlugin`` key. Local/SQLite boots have
        no remote device gateway, so rebind to the Noop impl here (a binding
        in this module wins over the prod module's). Rule 20: Protocol must
        resolve to its ``local`` impl in single box.
        """
        configure_local_device_test_runtime(binder)
        # The device-filesystem resolver is now a neutral ``core`` binding in the
        # base ``DevicesModule`` (B9); the per-provider filesystems are driven by
        # test doubles / real-HTTP seams via the injected deps. No binding here.

    @singleton
    @provider
    def arca_sandbox_factory(self) -> ArcaSandboxFactory:
        """Stub ``ArcaSandboxFactory`` for test boots.

        The real factory's ``__init__`` constructs an Arca SDK
        ``SandboxConfig`` (which needs ``base_url``/``api_key``) and
        resolves a Mist secret reference. Neither is reachable in tests,
        so we hand consumers a ``MagicMock`` instead. Test code never
        exercises real Arca paths.
        """
        from unittest.mock import MagicMock

        return cast(ArcaSandboxFactory, MagicMock(spec=ArcaSandboxFactory))

    # BaasService is no longer overridden here. It is a plain core service
    # (one impl) bound by ``ServiceBotModule.baas_service`` with a
    # config-driven ``baas_api_base`` (localhost:8890 in dev) and the
    # baas-qualified ``HttpClient`` (``LocalHttpClient`` under pytest, real
    # ``HttpxClient`` in singlebox). The former ``LocalBaasService`` override
    # carried no behavior beyond pinning that URL, which config already does.

    # DeviceBindingRepository is now a single unified ORM body
    # (plugins/device_repository.py) bound by DevicesModule; it
    # runs on whichever DatabasePlugin is bound, so test boots need
    # no SQLite override here (TestingDatabaseModule already swaps
    # DatabasePlugin -> SqliteDB). The LAST DB-repo twin pair was
    # unified in S5 (2026-05-20).

    # OssToNasRecordRepository is now a single unified ORM body
    # (plugins/oss_to_nas_record_repository.py) bound by
    # DevicesModule; it runs on whichever DatabasePlugin is bound, so
    # test boots need no SQLite override here (TestingDatabaseModule
    # already swaps DatabasePlugin -> SqliteDB).

    @singleton
    @provider
    @inject
    def device_service(
        self,
        repository: DeviceBindingRepository,
        baas_service: BaasService,
        publish_poller: BaasPublishPoller,
        device_local: ccfg.DeviceLocalConfig,
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
            config={"aidesktop_root": device_local.aidesktop_root},
            bot_query=bot_query,
            bot_sync=bot_sync,
            oss_record_repo=oss_record_repo,
            mcp_sync=mcp_sync,
            vault=token_vault,
        )

        # providers dict key 必须与 LocalDeviceService._do_allocate 写入
        # ac_entity_device_binding.device_provider 列的值一致，
        # 否则 DeviceServiceRouter 路由 fallback 到 default。
        # 此处沿用 "local" 是过渡决策；终态会改成 "baas"——见
        # local_device_service.py _do_allocate 顶部的完整决策注释 + 下游影响清单。
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
        """Local/test rollout seam routes directly to LocalDeviceService.

        Local boots do not read production DRM. They still pass through the
        same router field so the router behavior stays identical, but this
        seam always targets the locally registered provider.
        """

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
        """BaasPublishPoller — DeviceService 用 lazy provider 避免循环依赖。"""
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
        """pytest → InMemory mock; singlebox → 真 HttpDeviceAdapterTransport。

        历史: 这里无条件绑 InMemoryDeviceAdapterTransport 满足 contract tests
        (Rule 13/25)。但 ``TestingDevicesModule`` 同时被 pytest 和 singlebox
        装载 (两者都满足 LOCAL+SQLITE),无条件 mock 拦截了 singlebox 真链路 ——
        backend /api/cron 永远拿到 InMemory 空 store,跟 chat 工具创建的真
        engine cron 对不上 (R2 笔记 next_run_at_ms 固定值的根因)。

        分流: pytest 进程 (gateway rule13 等 contract tests 走 HTTP endpoint,
        必须 DI 层 mock) 返 InMemory; singlebox 返真 transport — 它按
        LocalDeviceFileSystem 同款模式 per-request 调 baas get_http_info(path=...)
        拿完整 engine URL,backend 请求容器统一走 baas。
        """
        import sys

        from agentclaw.community.plugins.local.device_adapter_transport import (
            InMemoryDeviceAdapterTransport,
        )
        from agentclaw.corp.plugins.prod.device_adapter_transport import (
            HttpDeviceAdapterTransport,
        )

        # pytest 在导入测试模块前必然 import "pytest"; singlebox 启动只
        # import agentclaw 包。最便宜可靠的 pytest 在场检测,不依赖 fixture 时序。
        if "pytest" in sys.modules:
            return InMemoryDeviceAdapterTransport()
        return HttpDeviceAdapterTransport(baas_service)
