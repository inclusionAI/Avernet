"""DevicesModule — neutral (profile-independent) device bindings.

Replaces the triple double-checked-locking pattern in
``core/devices/dependencies/device_dep.py`` (``_repository_instance``,
``_device_service_instance``, ``_baas_device_service_instance``) with
clean ``@singleton @provider`` bindings.

This module is **vendor-free** — it binds only the provider-agnostic device
pieces shared by every profile: the unified ORM repos, the conn-info builders,
the ``DeviceContextResolver``, the BaaS device service, the oss-to-nas
migration/switch services, and the ARCA-rollout policy/config (DRM-backed via
the injected ``DRMReaderPlugin``, which degrades to defaults off-corp). It is
installed in the base list for every profile and imports **no**
``plugins.prod`` (B6 T25).

The provider-specific device runtime is supplied by the infrastructure column:
``infrastructure/corp/devices.py`` (:class:`CorpDevicesModule`) binds the
Moltis ``DeviceConnectionManagerPlugin``, the real ``ArcaSandboxFactory``,
prod ARCA+BaaS ``DeviceServiceRouter``; the
test column's :class:`TestingDevicesModule` binds the local equivalents; the
community column's :class:`CommunityDevicesModule` binds a baas-only
``DeviceServiceRouter`` + a noop connection manager (B9 — no ARCA/OSS container
runtime, which is BaaS-team-owned).

``LocalProcessManager`` is bound unconditionally — the "Local" in its
name refers to the host-side process supervisor (used by adapter /
openclaw process management), not the device-provider type and not
the runtime mode. Both prod and local boots need it.

Cross-module callers keep using the transitional shims in
``core/devices/dependencies/device_dep.py``, which now delegate to the
injector.
"""
from __future__ import annotations

from typing import cast  # noqa: UP035 - injector binding key must match provider side

from injector import Module, inject, provider, singleton

from agentclaw.community.api.device_service import DeviceServiceProtocol
from agentclaw.community.api.oss_to_nas_migration_service import OssToNasMigrationServiceProtocol
from agentclaw.community.api.oss_to_nas_switch_service import OssToNasSwitchServiceProtocol
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.services.template_service import TemplateService
from agentclaw.community.core.devices.protocols import (
    BotQueryProtocol,
    BotSyncProtocol,
    McpSyncProtocol,
)
from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
    OssToNasRecordRepository,
)
from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_config import (
    ArcaBotCreateBaasRolloutConfigProvider,
)
from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy import (
    ArcaBotCreateBaasRolloutPolicy,
)
from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceService
from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
    BaasPublishTaskLifecycle,
)
from agentclaw.community.core.devices.services.baas_template_resolver import (
    SystemConfigBaasTemplateResolver,
)
from agentclaw.community.core.devices.services.conn_info_builders.arca_builder import (
    ArcaConnInfoBuilder,
)
from agentclaw.community.core.devices.services.conn_info_builders.baas_builder import (
    BaasConnInfoBuilder,
)
from agentclaw.community.core.devices.services.conn_info_builders.local_builder import (
    LocalConnInfoBuilder,
)
from agentclaw.community.core.devices.services.conn_info_builders.teclaw_builder import (
    TeclawConnInfoBuilder,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.devices.services.oss_to_nas_migration_service import (
    OssToNasMigrationService,
)
from agentclaw.community.core.devices.services.oss_to_nas_switch_service import (
    OssToNasSwitchService,
)
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.notify.bot_lister import RepositoryNotifyBotLister
from agentclaw.community.core.notify.protocol import NotifyBotLister
from agentclaw.community.core.bot_collaborator.repository.protocol import (
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.system_config import (
    SystemConfigService,
)
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.drm import DRMReaderPlugin
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient
from agentclaw.community.plugins.device_repository import (
    DeviceRepository as UnifiedDeviceRepository,
)
from agentclaw.community.plugins.local.process_manager import LocalProcessManager


logger = get_logger()


class DevicesModule(Module):
    """Neutral, profile-independent bindings for the devices module."""

    def configure(self, binder) -> None:
        from agentclaw.community.plugins.oss_to_nas_record_repository import (
            OssToNasRecordRepository as UnifiedOssToNasRecordRepository,
        )

        # Unified ORM repo (one body, ZDAS + SQLite). @inject ctor takes
        # the bound DatabasePlugin; prod vs test differ only by which
        # DatabasePlugin is bound (ZdasDB / SqliteDB).
        binder.bind(
            OssToNasRecordRepository,
            to=UnifiedOssToNasRecordRepository,
            scope=singleton,
        )
        # Single unified ORM impl — runs on prod OceanBase and SQLite
        # via the injected DatabasePlugin (@inject ctor). The LAST
        # DB-repo twin pair in the codebase, unified in S5.
        binder.bind(
            DeviceBindingRepository,
            to=UnifiedDeviceRepository,
            scope=singleton,
        )
        # Per-bot device-filesystem construction fn (baas/arca/teclaw/local). Every
        # impl is a neutral ``core`` filesystem and the arca branch reaches ARCA only
        # through the injected ``SandboxRuntimeClient`` seam, so this resolver is
        # bound once here for all profiles (B9) — corp/community/test differ only by
        # which ``SandboxRuntimeClient`` is installed. The core
        # ``DeviceFilesystemDispatcher`` holds this and delegates.
        from agentclaw.community.core.devices.services.device_filesystem_dispatcher import (
            DeviceFileSystemResolver,
        )
        from agentclaw.community.core.devices.services.device_filesystem_resolver import (
            DefaultDeviceFileSystemResolver,
        )

        binder.bind(
            DeviceFileSystemResolver,
            to=DefaultDeviceFileSystemResolver,
            scope=singleton,
        )
        # NOTE: the ``DeviceSyncDispatcher`` seam is a Protocol bound per-profile
        # by the device column (corp/test → prod impl, community → no-op) — not
        # here; there is no neutral base binding (no logic to hold).

    @singleton
    @provider
    def local_process_manager(self) -> LocalProcessManager:
        return LocalProcessManager.instance()

    @singleton
    @provider
    @inject
    def notify_bot_lister(
        self,
        binding_repo: DeviceBindingRepository,
        bot_repo: BotRepository,
        collaborator_repo: CollaboratorRepositoryProtocol,
    ) -> NotifyBotLister:
        # Neutral default: resolve notify targets from active device bindings.
        # Works for corp (ARCA/BaaS bindings) and community (BaaS bindings)
        # alike. The test/singlebox column overrides this with the bots-table
        # LocalNotifyBotLister (last-installed-wins).
        # Collaborator bots are folded in via the collaborator repo so that
        # the notify endpoint covers bots the user collaboratively manages,
        # mirroring /api/bots/by-owner-or-collaborator.
        #
        # collaborator_repo is intentionally a hard (non-optional) dependency:
        # injector 0.24 does NOT honor `= None` defaults on @inject params
        # (it always resolves the annotated type), so making it optional-by-
        # default would not change resolution. It is safe because
        # BotCollaboratorModule is base-installed for EVERY profile
        # (di/container.py), so CollaboratorRepositoryProtocol is always
        # bound whenever this provider is the winning binding. The
        # singlebox/test columns override notify_bot_lister with
        # LocalNotifyBotLister (no collaborator dep) instead.
        return RepositoryNotifyBotLister(
            binding_repo=binding_repo,
            bot_repo=bot_repo,
            collaborator_repo=collaborator_repo,
        )

    @singleton
    @provider
    @inject
    def arca_bot_create_baas_rollout_config_provider(
        self,
        drm_reader: DRMReaderPlugin,
    ) -> ArcaBotCreateBaasRolloutConfigProvider:
        return ArcaBotCreateBaasRolloutConfigProvider(drm_reader=drm_reader)

    @singleton
    @provider
    @inject
    def arca_bot_create_baas_rollout_policy(
        self,
        config_provider: ArcaBotCreateBaasRolloutConfigProvider,
    ) -> ArcaBotCreateBaasRolloutPolicy:
        return ArcaBotCreateBaasRolloutPolicy(config_provider=config_provider)

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
        template_service: TemplateService,
    ) -> BaasDeviceService:
        # Explicit provider: BaasDeviceService takes ``bot_query`` /
        # ``bot_sync`` / ``mcp_sync`` typed as Protocols, which
        # BotRepository, BotService and MCPSyncService satisfy
        # structurally. The injector resolves the concrete bindings;
        # the cast() calls are runtime no-ops that just convey intent
        # to type checkers.
        return BaasDeviceService(
            repository=repository,
            baas_service=baas_service,
            bot_query=cast(BotQueryProtocol, bot_repository),
            bot_sync=cast(BotSyncProtocol, bot_service),
            oss_record_repo=oss_record_repo,
            mcp_sync=cast(McpSyncProtocol, mcp_sync_service),
            template_resolver=SystemConfigBaasTemplateResolver(system_config_service),
            vault=token_vault,
            task_queue_service=task_queue_service,
            template_service=template_service,
        )

    @singleton
    @provider
    @inject
    def baas_publish_task_lifecycle(
        self,
        registry: HandlerRegistry,
        repository: DeviceBindingRepository,
        baas_service: BaasService,
        task_queue_service: TaskQueueService,
        baas_device_service: BaasDeviceService,
        bot_repository: BotRepository,
        publish_repository: BotPublishRepositoryProtocol,
        template_service: TemplateService,
    ) -> BaasPublishTaskLifecycle:
        return BaasPublishTaskLifecycle(
            registry=registry,
            binding_repository=repository,
            baas_service=baas_service,
            task_queue_service=task_queue_service,
            baas_device_service=baas_device_service,
            bot_repository=bot_repository,
            publish_repository=publish_repository,
            template_service=template_service,
        )

    @singleton
    @provider
    @inject
    def oss_to_nas_migration_service(
        self,
        oss_to_nas: cfg.OssToNasConfig,
    ) -> OssToNasMigrationService:
        return OssToNasMigrationService(oss_to_nas_config=oss_to_nas)

    @singleton
    @provider
    @inject
    def _device_service_protocol(self, svc: DeviceService) -> DeviceServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _oss_to_nas_migration_service_protocol(
        self, svc: OssToNasMigrationService
    ) -> OssToNasMigrationServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _oss_to_nas_switch_service_protocol(
        self, svc: OssToNasSwitchService
    ) -> OssToNasSwitchServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def oss_to_nas_switch_service(
        self,
        bot_repo: BotRepository,
        bot_service: BotService,
        record_repo: OssToNasRecordRepository,
        device_binding_repo: DeviceBindingRepository,
        oss_to_nas: cfg.OssToNasConfig,
    ) -> OssToNasSwitchService:
        return OssToNasSwitchService(
            bot_repo=bot_repo,
            bot_service=bot_service,
            record_repo=record_repo,
            device_binding_repo=device_binding_repo,
            oss_to_nas_config=oss_to_nas,
        )

    # ── DeviceContextResolver + 4 ConnInfoBuilders ────────────────────
    # Step 1 收口: provider 解析点 + 4 个 provider 专用 conn_info 构造器。
    # 注册在此 module 是因为 resolver/builder 都活在 core/devices/services/
    # 下,跟 DeviceService 同源。testing 环境不需要 override —— builder 只依赖
    # device_service / baas_service,后者两条链路在 TestingDevicesModule 已分别
    # 接管,builder 自身的行为对 prod/local 是同构的。

    @singleton
    @provider
    @inject
    def arca_conn_info_builder(
        self, device_service: DeviceService
    ) -> ArcaConnInfoBuilder:
        return ArcaConnInfoBuilder(device_service=device_service)

    @singleton
    @provider
    @inject
    def baas_conn_info_builder(
        self,
        baas_service: BaasService,
        bot_repository: BotRepository,
        device_binding_repository: DeviceBindingRepository,
        sandbox_client: SandboxRuntimeClient,
    ) -> BaasConnInfoBuilder:
        return BaasConnInfoBuilder(
            baas_service=baas_service,
            bot_repository=bot_repository,
            device_binding_repository=device_binding_repository,
            sandbox_client=sandbox_client,
        )

    @singleton
    @provider
    @inject
    def teclaw_conn_info_builder(
        self, baas_service: BaasService
    ) -> TeclawConnInfoBuilder:
        return TeclawConnInfoBuilder(baas_service=baas_service)

    @singleton
    @provider
    @inject
    def local_conn_info_builder(
        self, device_service: DeviceService
    ) -> LocalConnInfoBuilder:
        return LocalConnInfoBuilder(device_service=device_service)

    @singleton
    @provider
    @inject
    def device_context_resolver(
        self,
        binding_repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        arca_builder: ArcaConnInfoBuilder,
        baas_builder: BaasConnInfoBuilder,
        teclaw_builder: TeclawConnInfoBuilder,
        local_builder: LocalConnInfoBuilder,
    ) -> DeviceContextResolver:
        return DeviceContextResolver(
            binding_repository=binding_repository,
            bot_repository=bot_repository,
            arca_builder=arca_builder,
            baas_builder=baas_builder,
            teclaw_builder=teclaw_builder,
            local_builder=local_builder,
        )
