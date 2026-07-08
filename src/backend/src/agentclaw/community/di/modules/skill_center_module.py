"""SkillCenterModule — production singletons & factories for skill_center.

Replaces the module-globals in
``core/skill_center/dependencies/skills.py`` (``_skill_repo``,
``_skill_set_repo``, ``_device_plugin``, ``_skill_repo_sync_plugin``,
plus the legacy ``git_sync_service`` global at the bottom of
``services/git_sync.py``).

Production bindings are wired here. The ``test`` / ``singlebox`` profiles
install :class:`TestingSkillCenterModule` (via ``modules_for``) to
override these with the local stubs.

Three categories of binding:

- **Plugin / repo singletons**: ``SkillRepository``,
  ``SkillSetRepository``, ``DeviceAccessor``, ``SkillRepoSyncPlugin``,
  ``GitSyncService``. Each ``@singleton @provider``.
- **Service factories**: ``SkillServiceFactory``,
  ``SkillSetServiceFactory``, ``SkillParameterServiceFactory``. Each
  factory holds the @inject-supplied singletons and mints a fresh
  service per ``create()`` call with caller-supplied request-scoped
  arguments (paths, IDs).
- **Stateless services**: ``SkillAuthService`` is a clean singleton with
  no per-call state — bound directly.

This module never branches on mode. Local / test boots layer
``TestingSkillCenterModule`` on top to override the database-mode-keyed
plugin_api and repos.
"""
from __future__ import annotations

import dataclasses
from typing import Callable

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.git_sync_service import GitSyncServiceProtocol
from agentclaw.community.api.skill_auth_service import SkillAuthServiceProtocol
from agentclaw.community.api.skill_batch_sync_service import SkillBatchSyncServiceProtocol
from agentclaw.community.api.skill_center_sync_service import SkillCenterSyncServiceProtocol
from agentclaw.community.api.skill_member_service import SkillMemberServiceProtocol
from agentclaw.community.api.skill_parameter_service_factory import SkillParameterServiceFactoryProtocol
from agentclaw.community.api.skill_propagation_service import SkillPropagationServiceProtocol
from agentclaw.community.api.skill_publish_service import SkillPublishServiceProtocol
from agentclaw.community.api.skill_scan_service import SkillScanServiceProtocol
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_activator_factory import SkillSetActivatorFactoryProtocol
from agentclaw.community.api.skill_set_service_factory import SkillSetServiceFactoryProtocol
from agentclaw.community.api.skill_set_switcher_factory import SkillSetSwitcherFactoryProtocol
from agentclaw.community.di import config as cfg
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.skill_center.services.git_sync import GitSyncConfig, GitSyncService
from agentclaw.community.core.skill_center.services.repositories import (
    SkillCategoryRepository,
    SkillMemberRepository,
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.skill_center.services.skill_auth_service import SkillAuthService
from agentclaw.community.core.skill_center.services.skill_batch_sync_service import (
    SkillBatchSyncService,
)
from agentclaw.community.core.skill_center.services.skill_center_sync_service import (
    SkillCenterSyncService,
    SkillCenterSyncLogRepository,
)
from agentclaw.community.core.skill_center.services.skill_member_service import SkillMemberService
from agentclaw.community.core.skill_center.services.market_sync import MarketSyncService
from agentclaw.community.core.skill_center.services.skill_cache import MarketCache
from agentclaw.community.core.skill_center.services.skill_scan import SkillScanService
from agentclaw.community.core.skill_center.services.skill_propagation_service import (
    SkillPropagationLogRepository,
    SkillPropagationService,
)
from agentclaw.community.core.skill_center.services.skill_publish_service import SkillPublishService
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.core.skill_center.factories import (
    SkillParameterServiceFactory,
    SkillServiceFactory,
    SkillSetServiceFactory,
)
from agentclaw.community.core.skill_center.services.skill_set_service import (
    SkillSetActivatorFactory,
    SkillSetSwitcherFactory,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.skill_center.services.skill_symlink_listener import (
    SkillSymlinkListener,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.core.devices.services.device_filesystem_dispatcher import (
    DeviceFilesystemDispatcher,
    DeviceFileSystemResolver,
)
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.plugin_api.impl_registry import IMPL_REGISTRY, Mode
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.plugin_api.skill_scanner import SkillScannerPlugin
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
from agentclaw.community.plugins.skill_center_sync_log_repository import (
    SkillCenterSyncLogRepository as UnifiedSkillCenterSyncLogRepository,
)
from agentclaw.community.plugins.skill_propagation_log_repository import (
    SkillPropagationLogRepository as UnifiedSkillPropagationLogRepository,
)
from agentclaw.community.utils.singlebox_coverage_proxy import wrap_for_singlebox_coverage


logger = get_logger()


# ── Module ─────────────────────────────────────────────────────────────────


class SkillCenterModule(Module):
    """Production singletons + factories for skill_center."""

    def configure(self, binder: Binder) -> None:
        # ``SkillServiceFactory`` / ``SkillSetServiceFactory`` /
        # ``SkillParameterServiceFactory`` now live in
        # ``core.skill_center.factories`` so ``api/`` and ``core/`` can
        # reference the types without importing this DI module. They
        # depend on the runtime-keyed device dispatchers (which stay
        # here because they bridge to ``plugins``), so they are
        # wired via explicit ``@provider`` methods below rather than
        # ``binder.bind`` — the provider supplies the dispatcher; the
        # injector never introspects the factory class's type hints.
        #
        # Classes with ``@inject __init__`` and all deps already bound —
        # the injector can construct them on its own. We just need to
        # declare the singleton scope.
        # MarketCache holds an in-process ``_memory_cache`` dict that must
        # be shared across all consumers. Bind as a singleton so every
        # injection returns the same instance.
        binder.bind(MarketCache, to=MarketCache, scope=singleton)
        # ``GitSyncConfig.__init__`` reads YAML + env vars; bind as a
        # singleton so the file/env scan happens once.
        binder.bind(GitSyncConfig, to=GitSyncConfig, scope=singleton)
        binder.bind(SkillMemberService, to=SkillMemberService, scope=singleton)
        binder.bind(SkillAuthService, to=SkillAuthService, scope=singleton)
        # ``DeviceFilesystemDispatcher`` 与 ``DeviceSyncDispatcher`` 都通过
        # 显式 ``@provider`` 提供 (见下方)：前者 teclaw 分支用 lazy OSS thunk,
        # 后者用 lazy ``ConfigComposer`` thunk 解 DI 循环;两者都不靠 binder.bind
        # 自动构造。
        # Protocol-to-concrete bindings. SkillRepository /
        # SkillSetRepository are now single unified ORM bodies that
        # run on prod OceanBase and SQLite via the injected
        # DatabasePlugin (faithful prod port).
        # Skill repositories are provided by explicit provider methods below.
        # That keeps singlebox coverage evidence at the DI/plugin boundary
        # instead of leaking recorder calls into HTTP handlers or core services.
        # The ``DeviceAccessor`` (corp → ArcaDeviceAccessor) + the per-provider
        # prod device plugins are bound per-profile by the device column
        # (``CorpDevicesModule``); test binds ``LocalDeviceAccessor`` via
        # ``TestingSkillCenterModule``; community leaves it unbound (B6 T26).
        # ``SkillRepoSyncPlugin`` is bound per-profile by the infrastructure
        # skill_center column module (corp=Prod, community/test=Local) — not here.
        binder.bind(
            SkillPropagationLogRepository,
            to=UnifiedSkillPropagationLogRepository,
            scope=singleton,
        )
        binder.bind(
            SkillCenterSyncLogRepository,
            to=UnifiedSkillCenterSyncLogRepository,
            scope=singleton,
        )
        binder.bind(
            SkillBatchSyncService,
            to=SkillBatchSyncService,
            scope=singleton,
        )
        binder.bind(
            SkillPublishService,
            to=SkillPublishService,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def skill_repository(self, db: DatabasePlugin) -> SkillRepository:
        from agentclaw.community.plugins.skill_repository import (
            SkillRepository as UnifiedSkillRepository,
        )

        return wrap_for_singlebox_coverage(
            UnifiedSkillRepository(db),
            {
                "get_by_id": "SkillRepository create/list/get/update/delete",
                "get_by_uuid": "SkillRepository create/list/get/update/delete",
                "get_by_git_path": "SkillRepository create/list/get/update/delete",
                "get_by_link_name": "SkillRepository create/list/get/update/delete",
                "list_skills": "SkillRepository create/list/get/update/delete",
                "create": "SkillRepository create/list/get/update/delete",
                "update": "SkillRepository create/list/get/update/delete",
                "delete": "SkillRepository create/list/get/update/delete",
                "delete_by_name_with_cascade": "SkillRepository create/list/get/update/delete",
                "update_risk_tags": "SkillRepository create/list/get/update/delete",
                "update_mcp_dependencies": "SkillRepository create/list/get/update/delete",
                "delete_by_bot_id": "SkillRepository create/list/get/update/delete",
                "get_active_skills_by_bot": "SkillRepository create/list/get/update/delete",
            },
        )

    @singleton
    @provider
    @inject
    def skill_set_repository(self, db: DatabasePlugin) -> SkillSetRepository:
        from agentclaw.community.plugins.skill_repository import (
            SkillSetRepository as UnifiedSkillSetRepository,
        )

        return wrap_for_singlebox_coverage(
            UnifiedSkillSetRepository(db),
            {
                "get_by_id": "SkillSetRepository create/list/get/update/delete",
                "get_default": "SkillSetRepository create/list/get/update/delete",
                "list_all": "SkillSetRepository create/list/get/update/delete",
                "create": "SkillSetRepository create/list/get/update/delete",
                "update": "SkillSetRepository create/list/get/update/delete",
                "delete": "SkillSetRepository create/list/get/update/delete",
                "add_skill_to_set": "SkillSetRepository create/list/get/update/delete",
                "get_skills_in_set": "SkillSetRepository create/list/get/update/delete",
                "remove_skill_from_set": "SkillSetRepository create/list/get/update/delete",
                "delete_by_bot_id": "SkillSetRepository create/list/get/update/delete",
                "set_active_skill_set": "SkillSetRepository create/list/get/update/delete",
                "activate_skill_set": "SkillSetRepository create/list/get/update/delete",
                "deactivate_skill_set": "SkillSetRepository create/list/get/update/delete",
                "get_active_skill_set": "SkillSetRepository create/list/get/update/delete",
            },
        )

    @singleton
    @provider
    @inject
    def skill_member_repository(self, db: DatabasePlugin) -> SkillMemberRepository:
        from agentclaw.community.plugins.skill_member_repository import (
            SkillMemberRepository as UnifiedSkillMemberRepository,
        )

        return wrap_for_singlebox_coverage(
            UnifiedSkillMemberRepository(db),
            {
                "get_members_by_skill_uuid": "SkillMemberRepository add/list/update/delete",
                "get_member": "SkillMemberRepository add/list/update/delete",
                "add_member": "SkillMemberRepository add/list/update/delete",
                "remove_member": "SkillMemberRepository add/list/update/delete",
                "update_member_role": "SkillMemberRepository add/list/update/delete",
                "is_member": "SkillMemberRepository add/list/update/delete",
                "get_member_role": "SkillMemberRepository add/list/update/delete",
                "get_skill_uuids_by_user_id": "SkillMemberRepository add/list/update/delete",
                "has_admin_role": "SkillMemberRepository add/list/update/delete",
            },
        )

    @singleton
    @provider
    @inject
    def skill_category_repository(self, db: DatabasePlugin) -> SkillCategoryRepository:
        from agentclaw.community.plugins.skill_category_repository import (
            SkillCategoryRepository as UnifiedSkillCategoryRepository,
        )

        return wrap_for_singlebox_coverage(
            UnifiedSkillCategoryRepository(db),
            {
                "list_active": "SkillCategoryRepository create/list/update/delete",
                "get_by_code": "SkillCategoryRepository create/list/update/delete",
                "get_by_path": "SkillCategoryRepository create/list/update/delete",
                "create": "SkillCategoryRepository create/list/update/delete",
                "update_by_path": "SkillCategoryRepository create/list/update/delete",
                "update": "SkillCategoryRepository create/list/update/delete",
                "list_descendant_codes": "SkillCategoryRepository create/list/update/delete",
                "get_skills_by_category": "SkillCategoryRepository create/list/update/delete",
            },
        )

    @singleton
    @provider
    @inject
    def device_filesystem_dispatcher(
        self, injector: Injector
    ) -> DeviceFilesystemDispatcher:
        """Per-bot device-filesystem dispatcher (core routing holder).

        ``oss_provider`` and ``composer_provider`` are lazy thunks: only teclaw bots
        resolve them, so local/dev boots that never hit teclaw don't trigger the OSS
        client's remote secret fetch or build the composer.
        ``device_sync_dispatcher_provider`` resolves the prod ``DeviceSyncDispatcher``
        to build the ``TeclawDeviceSyncPlugin`` for the whole-artifact redeliver;
        ``composer_provider`` yields the composer whose ``oss_location`` derives
        the OSS write key (== the artifact ref). All cycle-safe — the
        composer/oss are resolved lazily on first teclaw edit, not at construction."""
        return DeviceFilesystemDispatcher(
            device_plugin=injector.get(DeviceAccessor),
            resolve=injector.get(DeviceFileSystemResolver),
            resolver_provider=lambda: injector.get(DeviceContextResolver),
        )

    @singleton
    @provider
    @inject
    def git_sync_service(
        self,
        cache_plugin: CachePlugin,
        skill_service_factory: SkillServiceFactory,
        config: GitSyncConfig,
        oss_storage: ObjectStoragePlugin,
        secret_resolver: SecretResolver,
        secret_names: cfg.SecretNamesConfig,
    ) -> GitSyncService:
        # Explicit provider: GitSyncService.__init__ types
        # SkillServiceFactory under TYPE_CHECKING (to avoid circular import
        # with this module), so binder.bind(...) can't resolve the
        # annotation via get_type_hints(). The provider supplies the
        # already-resolved dep directly. ``secret_resolver`` lets the service
        # resolve the source repo URL through the active runtime implementation
        # (corp secret backend, local singlebox config, etc.).
        # The source repo URL (a PAT-bearing secret) is mandatory ONLY for the
        # prod secret backend; every other deployment (local/singlebox, community
        # OSS) tolerates its absence and degrades git-sync to a no-op. Key on the
        # mode of the *bound* resolver's class: strict iff it is the PROD impl.
        # (corp → ProdSecretResolver=PROD → strict; test → LocalSecretResolver=
        # LOCAL → permissive; community → CommunitySecretResolver, unregistered →
        # permissive.)
        _bound_modes = {
            entry.mode for entry in IMPL_REGISTRY
            if entry.cls is secret_resolver.__class__
        }
        allow_missing_repo_url = Mode.PROD not in _bound_modes
        return GitSyncService(
            cache_plugin=cache_plugin,
            skill_service_factory=skill_service_factory,
            config=config,
            oss_storage=oss_storage,
            secret_resolver=secret_resolver,
            allow_missing_repo_url=allow_missing_repo_url,
            repo_url_secret_name=secret_names.aiworkbench_repo_url,
        )

    @singleton
    @provider
    @inject
    def market_sync_service(
        self,
        cache_plugin: CachePlugin,
        skill_service_factory: SkillServiceFactory,
    ) -> MarketSyncService:
        # Same rationale as ``git_sync_service``: TYPE_CHECKING-only ref
        # to SkillServiceFactory inside MarketSyncService.__init__.
        return MarketSyncService(
            cache_plugin=cache_plugin,
            skill_service_factory=skill_service_factory,
        )

    @singleton
    @provider
    @inject
    def skill_scan_service(
        self,
        cache_plugin: CachePlugin,
        skill_repo: SkillRepository,
        sync_service: SkillCenterSyncService,
        scanner: SkillScannerPlugin,
        skill_scan_cfg: cfg.SkillScanConfig,
    ) -> SkillScanService:
        # The scanner SDK + credential live behind ``SkillScannerPlugin`` (bound
        # per profile); this service is orchestration-only. ``enabled`` still
        # comes from the YAML-driven ``SkillScanConfig``.
        svc = SkillScanService(
            cache_plugin=cache_plugin,
            skill_repository=skill_repo,
            skill_center_sync_service=sync_service,
            scanner=scanner,
            config=dataclasses.asdict(skill_scan_cfg),
        )
        # The legacy ``get_skill_scan_service`` contextmanager called
        # ``start()`` before every scan and ``stop()`` after. As a DI
        # singleton we start once at construction; the service methods
        # guard on ``_ensure_started()`` and would otherwise RuntimeError.
        # ``start()`` swallows SDK-init failures (returns False), so this
        # is safe in local/test where the scan SDK is absent.
        svc.start()
        return svc

    # ``MCPConfigService`` and ``MCPAuthService`` are owned by
    # :class:`McpModule` (Task 16); skill_center services receive them
    # via @inject from the injector, no transitional bridge needed.

    # ── Service factories & stateless services ──────────────────────
    # ``SkillServiceFactory`` / ``SkillSetServiceFactory`` /
    # ``SkillParameterServiceFactory`` live in
    # ``core.skill_center.factories``. They take the runtime-keyed
    # device dispatchers (which stay in this DI module), so they are
    # wired here via explicit ``@provider`` methods: the injector reads
    # *these* signatures, never the relocated class's type hints, so
    # the ``core -> di`` boundary stays intact.

    # ``device_sync_dispatcher`` is bound by the per-profile infrastructure
    # column (``infrastructure/{corp,test,community}/device_sync.py``) under the
    # neutral ``DeviceSyncDispatcher`` key (B6 T24) — corp/test build the
    # prod ``DeviceSyncDispatcher``, community a no-op. This business module no
    # longer imports ``plugins.prod`` for it; consumers below resolve the key.

    @singleton
    @provider
    @inject
    def skill_service_factory(
        self,
        skill_repo: SkillRepository,
        skill_repo_sync: SkillRepoSyncPlugin,
        category_repo: SkillCategoryRepository,
        device_fs_dispatcher: DeviceFilesystemDispatcher,
        market_cache: MarketCache,
        git_sync_service_factory: Callable[[], GitSyncService],
    ) -> SkillServiceFactory:
        return SkillServiceFactory(
            skill_repo=skill_repo,
            skill_repo_sync=skill_repo_sync,
            category_repo=category_repo,
            device_fs_dispatcher=device_fs_dispatcher,
            market_cache=market_cache,
            git_sync_service_factory=git_sync_service_factory,
        )

    @singleton
    @provider
    @inject
    def skill_set_service_factory(
        self,
        skill_repo: SkillRepository,
        skill_set_repo: SkillSetRepository,
        mcp_center: MCPCenterPlugin,
        mcp_config_service: MCPConfigService,
        skill_service_factory: SkillServiceFactory,
        mcp_sync_service: MCPSyncService,
        bot_repo: BotRepository,
        device_plugin: DeviceAccessor,
        path_factory: WorkspacePathFactory,
        injector: Injector,
    ) -> SkillSetServiceFactory:
        # resolver / device_sync_dispatcher 走 lazy thunk:防止构造期 DI 循环
        # ``BotService → SkillSetServiceFactory → DeviceContextResolver
        # → ArcaConnInfoBuilder → DeviceService → BotService``。
        return SkillSetServiceFactory(
            skill_repo=skill_repo,
            skill_set_repo=skill_set_repo,
            mcp_center=mcp_center,
            mcp_config_service=mcp_config_service,
            skill_service_factory=skill_service_factory,
            resolver_provider=lambda: injector.get(DeviceContextResolver),
            device_sync_dispatcher_provider=lambda: injector.get(DeviceSyncDispatcher),
            mcp_sync_service=mcp_sync_service,
            bot_repo=bot_repo,
            device_plugin=device_plugin,
            path_factory=path_factory,
        )

    @singleton
    @provider
    @inject
    def skill_set_service_factory_provider(
        self, injector: Injector
    ) -> Callable[[], SkillSetServiceFactory]:
        """Lazy ``Callable[[], SkillSetServiceFactory]`` for cycle-breaking.

        DeviceAccessor impls that need ``SkillSetServiceFactory`` at hook-time
        inject this instead of the eager type, because the eager dep
        closes a cycle through ``DeviceFilesystemDispatcher → DeviceAccessor``.
        See ``LocalDeviceAccessor.__init__`` for the canonical user.
        """
        return lambda: injector.get(SkillSetServiceFactory)

    @singleton
    @provider
    @inject
    def skill_parameter_service_factory(
        self,
        resolver: DeviceContextResolver,
        device_fs_dispatcher: DeviceFilesystemDispatcher,
    ) -> SkillParameterServiceFactory:
        return SkillParameterServiceFactory(
            resolver=resolver,
            device_fs_dispatcher=device_fs_dispatcher,
        )

    @singleton
    @provider
    @inject
    def skill_center_sync_service(
        self,
        skill_center_client: SkillCenterClient,
        sync_log_repo: SkillCenterSyncLogRepository,
        skill_repo: SkillRepository,
        cache_plugin: CachePlugin,
        skill_scan_service_provider: Callable[[], SkillScanService],
    ) -> SkillCenterSyncService:
        from agentclaw.community.core.skill_center.feature_flags import (
            get_skill_center_flags,
        )

        flags = get_skill_center_flags()
        logger.info("[NEW-ARCH] SkillCenterSyncService initialized")
        return SkillCenterSyncService(
            skill_center_client=skill_center_client,
            sync_log_repo=sync_log_repo,
            skill_repo=skill_repo,
            cache_plugin=cache_plugin,
            skill_scan_service_provider=skill_scan_service_provider,
            nas_sync_enabled=flags.nas_sync_enabled,
        )

    # Cycle-breaker: ``SkillScanService.__init__`` needs
    # ``SkillCenterSyncService`` (passed by its provider above), and
    # ``SkillCenterSyncService.scan_after_sync`` needs to construct a
    # ``SkillScanService`` to scan a freshly-synced skill. Injecting the
    # service directly closes the cycle at graph-build time. We expose a
    # ``Callable[[], SkillScanService]`` instead so the lookup is deferred
    # until ``scan_after_sync`` actually runs — by then both singletons
    # exist and the lambda just returns the cached instance.
    @singleton
    @provider
    @inject
    def skill_scan_service_factory(
        self, injector: Injector
    ) -> Callable[[], SkillScanService]:
        return lambda: injector.get(SkillScanService)

    # Cycle break: GitSyncService → SkillServiceFactory → SkillService.
    # SkillService takes this lazy lambda so its construction doesn't
    # transitively pull GitSyncService.
    @singleton
    @provider
    @inject
    def git_sync_service_factory(
        self, injector: Injector
    ) -> Callable[[], GitSyncService]:
        return lambda: injector.get(GitSyncService)

    @singleton
    @provider
    def skill_propagation_service(
        self,
        log_repo: SkillPropagationLogRepository,
        resolver: DeviceContextResolver,
        device_sync_dispatcher: DeviceSyncDispatcher,
        sync_service: SkillCenterSyncService,
        skill_set_repo: SkillSetRepository,
        skill_set_service_factory: SkillSetServiceFactory,
    ) -> SkillPropagationService:
        return SkillPropagationService(
            log_repo=log_repo,
            sync_service=sync_service,
            skill_set_repo=skill_set_repo,
            resolver=resolver,
            device_sync_dispatcher=device_sync_dispatcher,
            skill_set_service_factory=skill_set_service_factory,
        )

    @singleton
    @provider
    def skill_set_activator_factory(
        self,
        skill_set_factory: SkillSetServiceFactory,
        resolver: DeviceContextResolver,
        device_sync_dispatcher: DeviceSyncDispatcher,
        device_plugin: DeviceAccessor,
        path_factory: WorkspacePathFactory,
    ) -> SkillSetActivatorFactory:
        """Construct the per-request ``SkillSetActivator`` factory."""
        return SkillSetActivatorFactory(
            skill_set_factory=skill_set_factory,
            resolver=resolver,
            device_sync_dispatcher=device_sync_dispatcher,
            device_plugin=device_plugin,
            path_factory=path_factory,
        )

    @singleton
    @provider
    @inject
    def skill_set_switcher_factory(
        self,
        skill_set_factory: SkillSetServiceFactory,
        resolver: DeviceContextResolver,
        device_sync_dispatcher: DeviceSyncDispatcher,
        device_plugin: DeviceAccessor,
        path_factory: WorkspacePathFactory,
        device_fs_dispatcher: DeviceFilesystemDispatcher,
    ) -> SkillSetSwitcherFactory:
        """Construct the per-request ``SkillSetSwitcher`` factory.

        Wired via ``@provider`` because ``SkillSetSwitcherFactory``
        types its deps as TYPE_CHECKING forward refs.

        ``device_fs_dispatcher`` (added plan-05) routes
        ``SkillSetSwitcher._cleanup_all_non_reserved_items`` via the
        DeviceFileSystem (singlebox → BaaS, contract tests →
        pathlib) instead of direct ``shutil.rmtree``.
        """
        return SkillSetSwitcherFactory(
            skill_set_factory=skill_set_factory,
            resolver=resolver,
            device_sync_dispatcher=device_sync_dispatcher,
            device_plugin=device_plugin,
            path_factory=path_factory,
            device_fs_dispatcher=device_fs_dispatcher,
        )

    @singleton
    @provider
    def skill_symlink_listener(
        self,
        bot_repo: BotRepository,
        skill_set_factory: SkillSetServiceFactory,
        resolver: DeviceContextResolver,
        device_sync_dispatcher: DeviceSyncDispatcher,
    ) -> SkillSymlinkListener:
        return SkillSymlinkListener(
            bot_repo=bot_repo,
            skill_set_factory=skill_set_factory,
            resolver=resolver,
            device_sync_dispatcher=device_sync_dispatcher,
        )

    # ── Service API Protocol aliases ────────────────────────────────────
    # Each @provider below resolves the concrete singleton and returns it
    # under the Protocol type. Adapters use ``Injected(<X>Protocol)``;
    # internal modules can still resolve the concrete class directly.

    @singleton
    @provider
    @inject
    def _git_sync_service_protocol(self, svc: GitSyncService) -> GitSyncServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_auth_service_protocol(self, svc: SkillAuthService) -> SkillAuthServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_batch_sync_service_protocol(
        self, svc: SkillBatchSyncService
    ) -> SkillBatchSyncServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_center_sync_service_protocol(
        self, svc: SkillCenterSyncService
    ) -> SkillCenterSyncServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_member_service_protocol(
        self, svc: SkillMemberService
    ) -> SkillMemberServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_parameter_service_factory_protocol(
        self, svc: SkillParameterServiceFactory
    ) -> SkillParameterServiceFactoryProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_propagation_service_protocol(
        self, svc: SkillPropagationService
    ) -> SkillPropagationServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_publish_service_protocol(
        self, svc: SkillPublishService
    ) -> SkillPublishServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_scan_service_protocol(
        self, svc: SkillScanService
    ) -> SkillScanServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_service_factory_protocol(
        self, svc: SkillServiceFactory
    ) -> SkillServiceFactoryProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_set_service_factory_protocol(
        self, svc: SkillSetServiceFactory
    ) -> SkillSetServiceFactoryProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_set_activator_factory_protocol(
        self, svc: SkillSetActivatorFactory
    ) -> SkillSetActivatorFactoryProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _skill_set_switcher_factory_protocol(
        self, svc: SkillSetSwitcherFactory
    ) -> SkillSetSwitcherFactoryProtocol:
        return svc
