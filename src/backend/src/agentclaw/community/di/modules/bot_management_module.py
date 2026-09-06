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
from agentclaw.community.api.bot_quota_service import BotQuotaServiceProtocol
from agentclaw.community.api.bot_space_service import BotSpaceServiceProtocol
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
from agentclaw.community.core.repository.protocols.bot import (
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot import RenderScreenRepository
from agentclaw.community.core.bot_collaborator.services.credentials_admins_writer import (
    DeviceCredentialsAdminsWriter,
)
from agentclaw.community.core.bot_management.render_screen.services.render_screen_service import (
    RenderScreenService,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestRepositoryProtocol,
    BotRestartLockRepositoryProtocol,
    BotStartupScriptRepositoryProtocol,
    ManifestContentRepositoryProtocol,
    SourceCredentialRepositoryProtocol,
)
from agentclaw.community.api.bot_startup_script_service import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_startup_script.protocols import (
    StartupScriptPurgeProtocol,
    TeclawEngineTestProtocol,
)
from agentclaw.community.core.bot_startup_script.services.startup_script_service import (
    BotStartupScriptService,
)
from agentclaw.community.api.bot_config_manifest_service import (
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.services.config_manifest_service import (
    BotConfigManifestService,
)
from agentclaw.community.api.bot_config_manifest_apply_service import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.api.mcp_auth_service import MCPAuthServiceProtocol
from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.apply.apply_task import (
    ApplyTaskLifecycle,
)
from agentclaw.community.core.bot_management.create_flow import (
    complete_manifest_creation,
)
from agentclaw.community.core.bot_config_manifest.create_job import (
    BotCreateWithManifestHandler,
    CreateJobLifecycle,
    enqueue_create_job,
    find_create_job,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    BotCreationManifestSeam,
)
from agentclaw.community.core.bot_config_manifest.services.config_manifest_apply_service import (
    BotConfigManifestApplyService,
)
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import EntryFetcher
from agentclaw.community.core.bot_config_manifest.cli_tools.service import CliToolPurger, CliToolServiceFactory
from agentclaw.community.core.ports.identity_file_port import (
    IdentityFilePort,
)
from agentclaw.community.core.bot_config_manifest.apply.resource_port import (
    ManifestResourcePort,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceClient,
)
from agentclaw.community.core.bot_management.manifest_seam import ManifestCreationSeam
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.local_skill_upload_service_protocol import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageValidator,
)
from agentclaw.community.core.bot_app_grant.services import (
    BotAppGrantService,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestApplyLockRepositoryProtocol,
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot import TemplateRepository
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.repository.protocols.identity import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.core.bot_management.services.bot_space_service import (
    BotSpaceService,
)
from agentclaw.community.core.bot_management.services.cleanup_service import (
    BotCleanupService,
)
from agentclaw.community.core.bot_management.services.create_bot_for_others_service import (
    CreateBotForOthersService,
)
from agentclaw.community.core.bot_management.services.data_init_service import (
    DataInitService,
)
from agentclaw.community.core.bot_management.services.default_bot_passport_repair_service import (
    DefaultBotPassportRepairService,
)
from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_service import (
    WorkspaceHostingService,
)
from agentclaw.community.core.bot_management.services.teclaw_provision_service import (
    TeclawProvisionService,
)
from agentclaw.community.core.bot_management.services.teclaw_publish_task_handler import (
    TeclawPublishTaskLifecycle,
)
from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_authorization_listener import (
    AicodingRestartAuthorizationBaasPublishListener,
)
from agentclaw.community.core.cron.services.aicoding.cron_auto_setup import (
    CronAutoSetupService,
)
from agentclaw.community.core.common_config.service import CommonConfigService
from agentclaw.community.core.desktop_bot.device_status_client import DeviceStatusClient
from agentclaw.community.core.repository.protocols.devices import (
    OssToNasRecordRepository,
)
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.baas_template_resolver import (
    SystemConfigBaasTemplateResolver,
)
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.repository.protocols.platform import (
    ResourceRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    BotPublishService,
)
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifactProducerRouter,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.system_config import SystemConfigService
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol as CoreBotRuntimeProjectorProtocol,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    TeclawPlatformBindings,
)
from agentclaw.community.core.bot_config_manifest.managed_files import ManagedFilesStore
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.drm import DRMReaderPlugin
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.http_client import QUALIFIER_BCN, HttpClient
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
from agentclaw.community.core.repository.implementations.bot.bot import (
    BotRepository as UnifiedBotRepository,
)
from agentclaw.community.core.repository.implementations.bot.restart_lock import (
    BotRestartLockRepository,
)
from agentclaw.community.core.repository.implementations.bot.startup_script import (
    BotStartupScriptRepository,
)
from agentclaw.community.core.repository.implementations.bot.config_manifest import (
    BotConfigManifestRepository,
)
from agentclaw.community.core.repository.implementations.bot.manifest_content import (
    ManifestContentRepository,
)
from agentclaw.community.core.repository.implementations.bot.source_credential import (
    SourceCredentialRepository,
)
from agentclaw.community.api.source_credential_service import (
    SourceCredentialServiceProtocol,
)
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.bot_config_manifest.credentials.service import (
    SourceCredentialService,
)

from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepository,
    BotConfigManifestApplyRepository,
)
from agentclaw.community.core.repository.implementations.bot.render_screen import (
    RenderScreenRepository as UnifiedRenderScreenRepository,
)
from agentclaw.community.core.repository.implementations.bot.template import (
    TemplateRepository as UnifiedTemplateRepository,
)


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
        binder.bind(TemplateRepository, to=UnifiedTemplateRepository, scope=singleton)
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
        # BotStartupScriptRepository: single unified ORM impl, same shape as the
        # restart lock above — UNIQUE(env, entity_id, bot_id) on
        # ac_bot_startup_script, one script per bot at most.
        binder.bind(
            BotStartupScriptRepositoryProtocol,
            to=BotStartupScriptRepository,
            scope=singleton,
        )
        binder.bind(
            SourceCredentialRepositoryProtocol,
            to=SourceCredentialRepository,
            scope=singleton,
        )
        # The Service API Protocol resolves to the same singleton as the concrete
        # class, so routers can Inject the Protocol per the http-adapter rule.
        binder.bind(
            BotStartupScriptService, to=BotStartupScriptService, scope=singleton
        )
        binder.bind(
            BotStartupScriptServiceProtocol,
            to=BotStartupScriptService,
            scope=singleton,
        )
        # The delete side, handed to ``BotCleanupService`` so a deleted bot takes
        # its stored script with it. Narrow on purpose: the deletion path removes
        # scripts, it never reads or writes one.
        binder.bind(
            StartupScriptPurgeProtocol,
            to=BotStartupScriptService,
            scope=singleton,
        )
        # BotConfigManifestRepository: single unified ORM impl, same shape as
        # the startup script above — UNIQUE(avernet_tenant, env, entity_id,
        # bot_id) on ac_bot_config_manifest, one manifest per bot at most.
        binder.bind(
            BotConfigManifestRepositoryProtocol,
            to=BotConfigManifestRepository,
            scope=singleton,
        )
        # ManifestContentRepository: the append-only provenance log behind
        # W11's content store. Only the repository binds now — the store
        # service itself is constructed by W4's apply wiring, which owns the
        # content_store_dir config read (same "declared machine part" shape
        # as W2's fetcher before an orchestrator consumed it).
        binder.bind(
            ManifestContentRepositoryProtocol,
            to=ManifestContentRepository,
            scope=singleton,
        )
        # Bound here rather than in a module of its own: the manifest service
        # shares this module's ``teclaw_engine_test_factory``, which is the one
        # definition of "runs in a teclaw container" and the only reason either
        # service needs a lazy provider at all.
        binder.bind(
            BotConfigManifestService, to=BotConfigManifestService, scope=singleton
        )
        binder.bind(
            BotConfigManifestServiceProtocol,
            to=BotConfigManifestService,
            scope=singleton,
        )
        # The apply engine's two tables, and the service over them. Bound here
        # rather than in a module of their own for the reason the manifest
        # service above is: they are the same feature's storage, and the apply
        # service depends on the document service directly.
        binder.bind(
            BotConfigManifestApplyRepositoryProtocol,
            to=BotConfigManifestApplyRepository,
            scope=singleton,
        )
        binder.bind(
            BotConfigManifestApplyLockRepositoryProtocol,
            to=BotConfigManifestApplyLockRepository,
            scope=singleton,
        )
        # ``BotConfigManifestApplyService`` is built by the provider below rather
        # than auto-constructed: its task-queue dependency is a lazy callable, and
        # the queue module imports the DI container at module scope, so the
        # annotation the injector would have to resolve cannot be imported here.
        # The Protocol is bound through a provider too: ``bind(Protocol,
        # to=ConcreteClass)`` builds a ClassProvider that instantiates the class
        # *directly*, bypassing the provider below and failing on the same
        # annotation.
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
        return UnifiedBotRepository(db)


    @singleton
    @provider
    @inject
    def restart_authorization_baas_publish_listener(
        self,
        repository: BotRepository,
        template_service: TemplateService,
        skill_set_factory: SkillSetServiceFactory,
        injector: Injector,
    ) -> AicodingRestartAuthorizationBaasPublishListener:
        return AicodingRestartAuthorizationBaasPublishListener(
            bot_repo=repository,
            template_service=template_service,
            skill_set_factory=skill_set_factory,
            runtime_reconciler_provider=lambda: injector.get(CoreBotRuntimeProjectorProtocol),
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
        common_config_service: CommonConfigService,
        caller_identity_repo: CallerIdentityRepositoryProtocol,
        bot_quota_service: BotQuotaServiceProtocol,
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
            # Lazy for symmetry with the providers above, not for a cycle:
            # BotAppGrantService reaches only repositories. Resolved on demand
            # so bot deletion is the only thing that pays for it.
            bot_app_grant_service_provider=lambda: injector.get(BotAppGrantService),
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
            baas_template_resolver=SystemConfigBaasTemplateResolver(
                system_config_service
            ),
            # Lazy (cycle-safe): baas bot 原地重启走 BaaSService.restart_bot。
            baas_service_provider=lambda: injector.get(BaasService),
            task_queue_service=task_queue_service,
            common_config_service=common_config_service,
            caller_identity_repo=caller_identity_repo,
            runtime_reconciler_provider=lambda: injector.get(CoreBotRuntimeProjectorProtocol),
            bot_quota_service=bot_quota_service,
        )

    @singleton
    @provider
    @inject
    def data_init_service(
        self,
        resource_repo: ResourceRepositoryProtocol,
        device_service: DeviceService,
        skill_set_factory: SkillSetServiceFactory,
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
    def device_service_factory(self, injector: Injector) -> Callable[[], DeviceService]:
        return lambda: injector.get(DeviceService)

    @singleton
    @provider
    @inject
    def bot_service_factory(self, injector: Injector) -> Callable[[], BotService]:
        return lambda: injector.get(BotService)

    @singleton
    @provider
    @inject
    def teclaw_publish_task_lifecycle(
        self,
        registry: HandlerRegistry,
        baas_service: BaasService,
        device_binding_repo: DeviceBindingRepository,
        passport_plugin: PassportPlugin,
        credentials_admins_writer: DeviceCredentialsAdminsWriter,
    ) -> TeclawPublishTaskLifecycle:
        return TeclawPublishTaskLifecycle(
            registry=registry,
            baas_service=baas_service,
            device_binding_repo=device_binding_repo,
            passport_plugin=passport_plugin,
            credentials_admins_writer=credentials_admins_writer,
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
    def teclaw_engine_test_factory(
        self, injector: Injector
    ) -> Callable[[], TeclawEngineTestProtocol]:
        """The narrow engine test BotStartupScriptService depends on.

        Same lazy shape as the factory below, and deliberately a *separate*
        binding: that service cannot name ``TeclawProvisionService`` in its
        annotations without closing an import cycle, so the composition root is
        where the concrete class and the narrow contract meet.
        """
        return lambda: injector.get(TeclawProvisionService)

    @singleton
    @provider
    @inject
    def manifest_script_service_factory(
        self, injector: Injector
    ) -> Callable[[], BotStartupScriptServiceProtocol]:
        """The startup-script service the ``script`` materialiser writes through.

        Lazy, and the three factories below share one reason: the apply service
        is constructed inside this module's graph, while the services its
        materialisers write through reach back through the bot-configuration
        graph. Resolving them at call time keeps that from closing a cycle at
        construction — the same shape ``teclaw_engine_test_factory`` above uses.
        """
        return lambda: injector.get(BotStartupScriptServiceProtocol)

    @singleton
    @provider
    @inject
    def manifest_task_queue_factory(
        self, injector: Injector
    ) -> Callable[[], TaskQueueService]:
        """The queue every apply now runs on.

        Lazy for a hard reason rather than symmetry with its neighbours:
        ``task_queue_service`` imports ``community.di`` at module scope, so an
        eager import from the apply service closes a cycle through the whole
        container graph.
        """
        return lambda: injector.get(TaskQueueService)

    @singleton
    @provider
    @inject
    def bot_config_manifest_apply_service(
        self,
        manifest_service: BotConfigManifestServiceProtocol,
        apply_repository: BotConfigManifestApplyRepositoryProtocol,
        lock_repository: BotConfigManifestApplyLockRepositoryProtocol,
        script_service_provider: Callable[[], BotStartupScriptServiceProtocol],
        activation_service_provider: Callable[[], DirectActivationServiceProtocol],
        mcp_auth_service_provider: Callable[[], MCPAuthServiceProtocol],
        # What W5's two fetch-consuming materialisers need, all supplied by
        # manifest_fetch_module as lazy factories for the same cycle reason the
        # three above are.
        identity_service_provider: Callable[[], IdentityFilePort],
        upload_service_provider: Callable[[], LocalSkillUploadServiceProtocol],
        capability_reader_provider: Callable[[], BotCapabilityStateReaderProtocol],
        package_validator_provider: Callable[[], SkillPackageValidator],
        entry_fetcher_provider: Callable[[], EntryFetcher],
        # W6's resources materialiser and W7's git transport, from the same
        # module and lazy for the same reason.
        resource_service_provider: Callable[[], ManifestResourcePort],
        cli_tool_service_factory: CliToolServiceFactory,  # W9, keyed by family
        git_client_provider: Callable[[], GitSourceClient],
        task_queue_provider: Callable[[], TaskQueueService],
        bot_repository: BotRepository,
        teclaw_engine_test_factory: Callable[[], TeclawEngineTestProtocol],
        manifest_config: cfg.BotConfigManifestConfig,
        teclaw_bindings: TeclawPlatformBindings,
    ) -> BotConfigManifestApplyService:
        return BotConfigManifestApplyService(
            manifest_service,
            apply_repository,
            lock_repository,
            script_service_provider,
            activation_service_provider,
            mcp_auth_service_provider,
            identity_service_provider,
            upload_service_provider,
            capability_reader_provider,
            package_validator_provider,
            entry_fetcher_provider,
            resource_service_provider,
            cli_tool_service_factory,
            git_client_provider,
            task_queue_provider,
            bot_repository,
            # W8: the delivery seam. The engine authority is the same factory
            # the capability resolver and the creation seam take; the switch is
            # the config cluster's, read once here.
            is_teclaw=lambda engine: teclaw_engine_test_factory().is_teclaw(engine),
            teclaw_platform_managed=manifest_config.teclaw_platform_managed,
            teclaw_platform_ports_provider=teclaw_bindings.platform_ports,
            redeliver=teclaw_bindings.redeliver,
        )

    @singleton
    @provider
    @inject
    def bot_config_manifest_apply_service_protocol(
        self, service: BotConfigManifestApplyService
    ) -> BotConfigManifestApplyServiceProtocol:
        """The Protocol every adapter injects, delegated to the one instance."""
        return service

    @singleton
    @provider
    @inject
    def bot_creation_manifest_seam(
        self,
        injector: Injector,
        manifest_service: BotConfigManifestServiceProtocol,
        apply_service: BotConfigManifestApplyService,
        script_service_provider: Callable[[], BotStartupScriptServiceProtocol],
        task_queue_provider: Callable[[], TaskQueueService],
        create_with_manifest_config: cfg.BotCreateWithManifestConfig,
    ) -> ManifestCreationSeam:
        """The operations bot creation asks of the manifest layer.

        Bound under the **Protocol** every consumer holds; the one place naming
        the implementation, since it is the one that builds it. The job's two
        operations are wired here rather than imported by the seam (``create_job``
        imports ``creation`` for the triggers), behind the same lazy queue provider.
        """
        return BotCreationManifestSeam(
            manifest_service=manifest_service,
            apply_service=apply_service,
            script_service_provider=script_service_provider,
            start_job=lambda **fields: enqueue_create_job(
                task_queue_provider(), **fields
            ),
            find_job=lambda **fields: find_create_job(
                task_queue_provider(), **fields
            ),
            authorization_window_seconds=create_with_manifest_config.authorization_window_seconds,
            purge_managed_files=lambda o, b: injector.get(ManagedFilesStore).purge_owner_bot(o, b),
            purge_cli_tools=injector.get(CliToolPurger),
            creation_sequence=lambda e: apply_service.delivery_for_engine(e).creation_sequence,
        )

    @singleton
    @provider
    @inject
    def bot_create_with_manifest_handler(
        self,
        injector: Injector,
        passport_plugin: PassportPlugin,
        auth_rel_plugin: AuthRelationshipPlugin,
        bot_repository: BotRepository,
    ) -> BotCreateWithManifestHandler:
        """The creation job's step machine.

        Its collaborators are resolved lazily: the handler is built while the
        injector is still walking bindings to discover lifecycles, and the
        creation graph it reaches into is large.
        """

        def _complete(job_payload: dict, *, provision: bool = True) -> None:
            complete_manifest_creation(
                job_payload,
                bot_service=injector.get(BotService),
                passport_plugin=passport_plugin,
                auth_rel_plugin=auth_rel_plugin,
                provision=provision,
            )

        return BotCreateWithManifestHandler(
            manifest_seam_provider=lambda: injector.get(ManifestCreationSeam),
            apply_service_provider=lambda: injector.get(
                BotConfigManifestApplyService
            ),
            bot_repository_provider=lambda: bot_repository,
            complete_authorization=_complete,
            passport_plugin_provider=lambda: passport_plugin,
            bot_service_provider=lambda: injector.get(BotService),
            # Read-only here: the job asks whether the owner relationship
            # actually landed, because completion writes it *after* the bot
            # record and a failure there would otherwise never be retried.
            auth_relationship_provider=lambda: auth_rel_plugin,
            # W8: the strategy decides the order a creation runs in.
            creation_sequence=lambda engine: injector.get(
                BotConfigManifestApplyService
            ).delivery_for_engine(engine).creation_sequence,
        )

    @singleton
    @provider
    @inject
    def manifest_create_job_lifecycle(
        self, registry: HandlerRegistry, injector: Injector
    ) -> CreateJobLifecycle:
        return CreateJobLifecycle(
            registry=registry,
            handler_provider=lambda: injector.get(BotCreateWithManifestHandler),
        )

    @singleton
    @provider
    @inject
    def manifest_apply_task_lifecycle(
        self,
        registry: HandlerRegistry,
        injector: Injector,
    ) -> ApplyTaskLifecycle:
        """Registers the apply handler at boot.

        The service is resolved lazily for the same cycle reason as its own queue
        dependency, and because the lifecycle is discovered by walking the
        injector's bindings — building the apply graph while that walk is in
        progress is exactly the ordering this avoids.
        """
        return ApplyTaskLifecycle(
            registry=registry,
            apply_service_provider=lambda: injector.get(BotConfigManifestApplyService),
        )

    @singleton
    @provider
    @inject
    def manifest_activation_service_factory(
        self, injector: Injector
    ) -> Callable[[], DirectActivationServiceProtocol]:
        """The per-bot activation service the ``mcp`` materialiser converges through."""
        return lambda: injector.get(DirectActivationServiceProtocol)

    @singleton
    @provider
    @inject
    def manifest_mcp_auth_service_factory(
        self, injector: Injector
    ) -> Callable[[], MCPAuthServiceProtocol]:
        """The permission service the ``mcp`` materialiser asks before writing.

        The *same* service ``DirectActivationService`` consults, so apply's
        up-front check cannot give a different answer from the one the write
        would get.
        """
        return lambda: injector.get(MCPAuthServiceProtocol)

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
    def _bot_space_service_protocol(
        self, svc: BotSpaceService
    ) -> BotSpaceServiceProtocol:
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

    @singleton
    @provider
    @inject
    def _source_credential_service_protocol(
        self,
        repository: SourceCredentialRepositoryProtocol,
        vault: TokenVault,
    ) -> SourceCredentialServiceProtocol:
        """W3 (#1471): the profile decides the fail-closed posture.

        Production columns (corp, community) refuse credential writes when
        the vault has no master key — TokenVault's plaintext passthrough
        is right for local, catastrophic for tenant tokens at rest. The
        local/test columns keep the permissive default; corp_test runs the
        Mist-backed vault anyway and benefits from the same guard.
        """
        from agentclaw.community.di.profile import DeployProfile

        fail_closed = DeployProfile.detect() in (
            DeployProfile.CORP,
            DeployProfile.COMMUNITY,
        )
        return SourceCredentialService(repository, vault, fail_closed=fail_closed)
