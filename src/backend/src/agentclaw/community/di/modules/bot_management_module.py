"""BotManagementModule — production singletons for bot_management.

Replaces the lazy module-level globals
(``_bot_repo`` / ``_cleanup_service``) and the ``BotService`` PEP-562
hack in ``core/bot_management/services/bot_service.py`` with proper
``@singleton @provider`` bindings.

``BotRepository`` is the database-mode-keyed binding here; production
binds the ZDAS impl. Local + test boots get the SQLite impl via
``TestingDatabaseModule`` (database-mode-keyed override); this module
itself stays mode-agnostic — no dedicated testing module exists here.

``BotService`` and ``BotCleanupService`` are mode-agnostic singletons
constructed by the injector via ``@inject`` on their constructors —
they pick up the swapped repositories transparently.
"""
from __future__ import annotations

from typing import Annotated, Callable

from injector import (
    Binder,
    CallError,
    Injector,
    Module,
    UnsatisfiedRequirement,
    inject,
    provider,
    singleton,
)

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.create_bot_for_others_service import (
    CreateBotForOthersServiceProtocol,
)
from agentclaw.community.api.data_init_service import DataInitServiceProtocol
from agentclaw.community.api.default_bot_passport_repair_service import (
    DefaultBotPassportRepairServiceProtocol,
)
from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.api.render_screen_service import RenderScreenServiceProtocol
from agentclaw.community.core.bot_collaborator.protocols import (
    BotServiceProtocol as CoreBotServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.repository.protocol import CollaboratorRepositoryProtocol
from agentclaw.community.core.bot_management.render_screen.repositories import RenderScreenRepository
from agentclaw.community.core.bot_management.render_screen.services.render_screen_service import (
    RenderScreenService,
)
from agentclaw.community.core.bot_management.repository.protocol import (
    BotRepository,
    BotRestartLockRepositoryProtocol,
)
from agentclaw.community.core.bot_management.repository.template_repository_protocol import TemplateRepository
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.services.cleanup_service import BotCleanupService
from agentclaw.community.core.bot_management.services.create_bot_for_others_service import (
    CreateBotForOthersService,
)
from agentclaw.community.core.bot_management.services.data_init_service import DataInitService
from agentclaw.community.core.bot_management.services.default_bot_passport_repair_service import (
    DefaultBotPassportRepairService,
)
from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_service import WorkspaceHostingService
from agentclaw.community.core.bot_management.services.teclaw_provision_service import (
    TeclawProvisionService,
)
from agentclaw.community.core.bot_management.services.teclaw_publish_task_handler import (
    TeclawPublishTaskLifecycle,
)
from agentclaw.community.core.bot_management.services.template_service import TemplateService
from agentclaw.community.core.cron.services.aicoding.cron_auto_setup import CronAutoSetupService
from agentclaw.community.core.desktop_bot.device_status_client import DeviceStatusClient
from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
    OssToNasRecordRepository,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.baas_template_resolver import (
    SystemConfigBaasTemplateResolver,
)
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.resources.repository.protocol import ResourceRepositoryProtocol
from agentclaw.community.core.service_bot.repository.bot_publish_repository import BotPublishRepositoryProtocol
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifactProducerRouter,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.system_config import SystemConfigService
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.services.skill_set_service import (
    SkillSetActivatorFactory,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.drm import DRMReaderPlugin
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.http_client import QUALIFIER_BCN, HttpClient
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
from agentclaw.community.plugins.bot_repository import (
    BotRepository as UnifiedBotRepository,
)
from agentclaw.community.plugins.bot_restart_lock_repository import BotRestartLockRepository
from agentclaw.community.plugins.render_screen_repository import (
    RenderScreenRepository as UnifiedRenderScreenRepository,
)
from agentclaw.community.plugins.template_repository import (
    TemplateRepository as UnifiedTemplateRepository,
)
from agentclaw.community.utils.singlebox_coverage_proxy import wrap_for_singlebox_coverage


logger = get_logger()


def _optional_get(injector: Injector, key: type):
    """Resolve ``key`` from ``injector``, or ``None`` if it can't be provided.

    Used for corp-only collaborators (e.g. WorkspaceHostingService) that the
    community column does not install — the base-list BotService provider then
    passes ``None`` and BotService guards the use sites (B8).

    Catches both ``UnsatisfiedRequirement`` (an unbound Protocol) and
    ``CallError`` (injector attempting implicit construction of an unbound
    concrete class whose transitive deps — e.g. WorkspaceHostingConfig's required fields —
    aren't provided in this column). Either way the optional dependency is
    absent → ``None``.

    Trade-off: catching ``CallError`` means that in a corp boot where ``key``
    IS bound but its constructor genuinely fails, this returns ``None`` instead
    of surfacing the error at boot — the failure then shows up as a clear
    ``BotServiceError`` at the ``_require_workspace_hosting`` guard on first use. Acceptable
    for the current sole caller (WorkspaceHostingService → WorkspaceHostingConfig, a frozen
    dataclass exercised by the corp DI-profile suite); if a corp-critical
    consumer is ever added, put it in ``eager_check_critical_bindings`` instead.
    """
    try:
        return injector.get(key)
    except (UnsatisfiedRequirement, CallError):
        return None


class BotManagementModule(Module):
    """Production bindings for bot_management."""

    def configure(self, binder: Binder) -> None:
        # Classes with ``@inject __init__`` and all deps already bound —
        # the injector can construct them on their own. We just need to
        # declare the singleton scope.
        binder.bind(BotCleanupService, to=BotCleanupService, scope=singleton)
        # ``BcnService`` is built by the ``bcn_service`` provider below — it needs
        # the bcn-qualified HttpClient injected.
        binder.bind(RenderScreenService, to=RenderScreenService, scope=singleton)
        # BotRepository: single unified ORM impl — runs on prod
        # OceanBase and SQLite via the injected DatabasePlugin.
        # RenderScreenRepository was already unified earlier.
        # BotRepository is provided below so singlebox can observe real
        # repository usage at the DI/plugin boundary.
        binder.bind(
            TemplateRepository, to=UnifiedTemplateRepository, scope=singleton
        )
        binder.bind(
            RenderScreenRepository,
            to=UnifiedRenderScreenRepository,
            scope=singleton,
        )
        # BotRestartLockRepository: single unified ORM impl (prod OceanBase +
        # local SQLite via DatabasePlugin). UNIQUE(env, entity_id, bot_id) is
        # the restart idempotency lock body.
        binder.bind(
            BotRestartLockRepositoryProtocol,
            to=BotRestartLockRepository,
            scope=singleton,
        )
        # TemplateService: constructed with injected TemplateRepository.
        binder.bind(TemplateService, to=TemplateService, scope=singleton)
        # CronAutoSetupService: constructed with injected dependencies.
        binder.bind(CronAutoSetupService, to=CronAutoSetupService, scope=singleton)
        # WorkspaceHostingService (DIMA applicationCoding hosting) is corp-only —
        # bound by CorpAppServicesModule (corp) / TestingAicodingModule stub
        # (test). Community does not install it; BotService resolves it
        # optionally and guards via _require_workspace_hosting (B8).

    @singleton
    @provider
    @inject
    def bot_repository(self, db: DatabasePlugin) -> BotRepository:
        return wrap_for_singlebox_coverage(
            UnifiedBotRepository(db),
            {
                "insert": "BotRepository create/read/search/update/delete",
                "get_by_id_and_owner": "BotRepository create/read/search/update/delete",
                "get_live_by_id_owner_and_env": "BotRepository explicit-env passport repair",
                "update_ext_by_id_owner_and_env": "BotRepository explicit-env passport repair",
                "get_by_id": "BotRepository create/read/search/update/delete",
                "list_by_owner": "BotRepository create/read/search/update/delete",
                "list_by_owner_or_collaborator": "BotRepository create/read/search/update/delete",
                "list_by_entity": "BotRepository create/read/search/update/delete",
                "list_by_conditions": "BotRepository create/read/search/update/delete",
                "list_by_search": "BotRepository create/read/search/update/delete",
                "list_domain_bots": "BotRepository create/read/search/update/delete",
                "update_by_owner": "BotRepository create/read/search/update/delete",
                "soft_delete_by_owner": "BotRepository create/read/search/update/delete",
                "count_by_owner": "BotRepository create/read/search/update/delete",
                "exists_by_owner_and_bot_id": "BotRepository create/read/search/update/delete",
                "exists_by_bot_name": "BotRepository create/read/search/update/delete",
                "get_by_bot_name": "BotRepository create/read/search/update/delete",
                "get_by_binding_id": "BotRepository create/read/search/update/delete",
                "get_device_provider_by_bot_id_and_owner": "BotRepository create/read/search/update/delete",
                "get_device_provider_by_bot_id": "BotRepository create/read/search/update/delete",
                "search_bots": "BotRepository create/read/search/update/delete",
            },
        )

    @singleton
    @provider
    @inject
    def default_bot_passport_repair_service(
        self,
        repository: BotRepository,
        passport_plugin: PassportPlugin,
        auth_relationship_plugin: AuthRelationshipPlugin,
        skill_set_factory: SkillSetServiceFactory,
    ) -> DefaultBotPassportRepairServiceProtocol:
        return DefaultBotPassportRepairService(
            repository=repository,
            passport_plugin=passport_plugin,
            auth_relationship_plugin=auth_relationship_plugin,
            skill_set_factory=skill_set_factory,
        )

    @singleton
    @provider
    @inject
    def create_bot_for_others_service(
        self,
        repository: BotRepository,
        bot_service: BotService,
        passport_plugin: PassportPlugin,
        auth_relationship_plugin: AuthRelationshipPlugin,
        skill_set_factory: SkillSetServiceFactory,
    ) -> CreateBotForOthersServiceProtocol:
        return CreateBotForOthersService(
            repository=repository,
            bot_service=bot_service,
            passport_plugin=passport_plugin,
            auth_relationship_plugin=auth_relationship_plugin,
            skill_set_factory=skill_set_factory,
        )

    @singleton
    @provider
    @inject
    def bcn_service(
        self,
        http_client: Annotated[HttpClient, QUALIFIER_BCN],
        bcn_config: cfg.BcnConfig,
    ) -> BcnService:
        """``BcnService`` with the bcn-qualified HttpClient + BcnConfig injected."""
        return BcnService(http_client=http_client, config=bcn_config)

    @singleton
    @provider
    @inject
    def bot_service(
        self,
        repository: BotRepository,
        allocation_config: cfg.DeviceAllocationConfig,
        workspace_hosting_config: cfg.WorkspaceHostingConfig,
        device_binding_repo: DeviceBindingRepository,
        skill_set_factory: SkillSetServiceFactory,
        cleanup_service: BotCleanupService,
        bcn_service: BcnService,
        bot_publish_repo: BotPublishRepositoryProtocol,
        passport_plugin: PassportPlugin,
        oss_record_repo: OssToNasRecordRepository,
        bot_publish_service_provider: Callable[[], BotPublishService],
        device_service_provider: Callable[[], DeviceService],
        path_factory: WorkspacePathFactory,
        template_service: TemplateService,
        collaborator_repo: CollaboratorRepositoryProtocol,
        restart_lock_repo: BotRestartLockRepositoryProtocol,
        teclaw_provision_service_provider: Callable[[], TeclawProvisionService],
        baas_config: cfg.BaasConfig,
        policy_service: PolicyServiceProtocol,
        system_config_service: SystemConfigService,
        drm_reader: DRMReaderPlugin,
        task_queue_service: TaskQueueService,
        injector: Injector,
    ) -> BotService:
        # Explicit provider: ``BotService.__init__`` types several
        # collaborators under TYPE_CHECKING (skill-center factory,
        # cleanup service, etc.) to avoid circular imports, so
        # ``binder.bind`` can't resolve those forward refs.
        return BotService(
            repository=repository,
            allocation_config=allocation_config,
            workspace_hosting_config=workspace_hosting_config,
            device_binding_repo=device_binding_repo,
            skill_set_factory=skill_set_factory,
            cleanup_service=cleanup_service,
            bcn_service=bcn_service,
            bot_publish_repo=bot_publish_repo,
            passport_plugin=passport_plugin,
            oss_record_repo=oss_record_repo,
            bot_publish_service_provider=bot_publish_service_provider,
            device_service_provider=device_service_provider,
            path_factory=path_factory,
            template_service=template_service,
            # DIMA hosting is corp-only — resolve optionally (None in community).
            workspace_hosting_service=_optional_get(injector, WorkspaceHostingService),
            collaborator_repo=collaborator_repo,
            restart_lock_repo=restart_lock_repo,
            teclaw_provision_service_provider=teclaw_provision_service_provider,
            drm_reader=drm_reader,
            # Leaf BaaS reader for the by-owner list's live desktop status.
            # Built directly here (no service deps) so the list stays off the
            # DesktopBotService → DeviceService → BotService edge — no DI cycle.
            device_status_client=DeviceStatusClient.from_baas_config(baas_config),
            cron_auto_setup_service_provider=lambda: injector.get(CronAutoSetupService),
            policy_service=policy_service,
            baas_template_resolver=SystemConfigBaasTemplateResolver(system_config_service),
            # Lazy (cycle-safe): baas bot 原地重启走 BaaSService.restart_bot。
            baas_service_provider=lambda: injector.get(BaasService),
            task_queue_service=task_queue_service,
        )

    @singleton
    @provider
    @inject
    def data_init_service(
        self,
        resource_repo: ResourceRepositoryProtocol,
        device_service: DeviceService,
        skill_set_factory: SkillSetServiceFactory,
        skill_set_activator_factory: SkillSetActivatorFactory,
        device_plugin: DeviceAccessor,
        bot_service_provider: Callable[[], BotService],
        skill_repo_sync: SkillRepoSyncPlugin,
        resolver: DeviceContextResolver,
        bcsfuse: cfg.BcsFuseConfig,
        ecb: cfg.EcbConfig,
    ) -> DataInitService:
        # Explicit provider for the same TYPE_CHECKING forward-ref reason.
        # Rule 14: ``SkillRepoSyncPlugin`` impls own the path resolution —
        # each knows its own layout, no is_local probe needed here.
        #
        # ``resolver`` 是全仓唯一 provider 解析点,_get_engine_connection
        # 走 (bot_id, owner_id) → resolver.resolve_for_bot 而不是旧的
        # device_service.get_device_connection_v2(binding_id, ...)。
        from agentclaw.community.utils.env_utils import get_current_env

        is_pre = get_current_env() == "pre"
        bcsfuse_base_url = bcsfuse.base_url_pre if is_pre else bcsfuse.base_url
        ecb_base_url = ecb.base_url_pre if is_pre else ecb.base_url
        return DataInitService(
            resource_repo=resource_repo,
            device_service=device_service,
            skill_set_factory=skill_set_factory,
            skill_set_activator_factory=skill_set_activator_factory,
            device_plugin=device_plugin,
            bot_service_provider=bot_service_provider,
            skill_md_path=skill_repo_sync.get_data_init_skill_md_path(),
            resolver=resolver,
            bcsfuse_base_url=bcsfuse_base_url,
            ecb_base_url=ecb_base_url,
        )

    # ── Lazy factory providers — cycle-breakers for BotService ──────────
    # BotPublishService.__init__ takes BotService; DeviceService is
    # constructed with BotService as its ``bot_sync`` protocol. Both close
    # the graph if injected directly. Exposing them as ``Callable[[], T]``
    # defers lookup until after construction, so the cycle never
    # materialises during DI graph build.
    @singleton
    @provider
    @inject
    def bot_publish_service_factory(
        self, injector: Injector
    ) -> Callable[[], BotPublishService]:
        return lambda: injector.get(BotPublishService)

    @singleton
    @provider
    @inject
    def device_service_factory(
        self, injector: Injector
    ) -> Callable[[], DeviceService]:
        return lambda: injector.get(DeviceService)

    @singleton
    @provider
    @inject
    def bot_service_factory(
        self, injector: Injector
    ) -> Callable[[], BotService]:
        return lambda: injector.get(BotService)

    @singleton
    @provider
    @inject
    def teclaw_publish_task_lifecycle(
        self,
        registry: HandlerRegistry,
        baas_service: BaasService,
        device_binding_repo: DeviceBindingRepository,
    ) -> TeclawPublishTaskLifecycle:
        return TeclawPublishTaskLifecycle(
            registry=registry,
            baas_service=baas_service,
            device_binding_repo=device_binding_repo,
        )

    @singleton
    @provider
    @inject
    def teclaw_provision_service(
        self,
        baas_service: BaasService,
        deploy_artifact_producer_router: DeployArtifactProducerRouter,
        device_binding_repo: DeviceBindingRepository,
        task_queue_service: TaskQueueService,
        bot_repository: BotRepository,
        baas_config: cfg.BaasConfig,
    ) -> TeclawProvisionService:
        # Eager teclaw container provisioning at bot creation. teclaw_template_uuid
        # is a placeholder until the teclaw owner registers the real template.
        # Uses the deploy-artifact producer (same path as the publish build), not
        # the composer directly, so provision/publish share the engine_ext seam.
        return TeclawProvisionService(
            baas_service=baas_service,
            deploy_artifact_producer_router=deploy_artifact_producer_router,
            device_binding_repo=device_binding_repo,
            task_queue_service=task_queue_service,
            bot_repository=bot_repository,
            teclaw_template_uuid=baas_config.teclaw_template_uuid,
        )

    @singleton
    @provider
    @inject
    def teclaw_provision_service_factory(
        self, injector: Injector
    ) -> Callable[[], TeclawProvisionService]:
        # Lazy (cycle-safe): TeclawProvisionService -> producer/BaasService graphs
        # transitively reach BotService.
        return lambda: injector.get(TeclawProvisionService)

    @singleton
    @provider
    @inject
    def data_init_service_factory(
        self, injector: Injector
    ) -> Callable[[], DataInitService]:
        # Lazy: DeviceService.report_device_status (on device SUCCEEDED)
        # resolves this at call time to run bot data-init. Eager
        # injection would cycle (DataInitService → bot_service → …).
        return lambda: injector.get(DataInitService)

    @singleton
    @provider
    @inject
    def _render_screen_service_protocol(
        self, svc: RenderScreenService
    ) -> RenderScreenServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _bot_service_protocol(self, svc: BotService) -> BotServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _core_bot_service_protocol(self, svc: BotService) -> CoreBotServiceProtocol:
        """Bind core layer's BotServiceProtocol."""
        return svc

    @singleton
    @provider
    @inject
    def _data_init_service_protocol(
        self, svc: DataInitService
    ) -> DataInitServiceProtocol:
        return svc
