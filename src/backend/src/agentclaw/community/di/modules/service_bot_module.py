"""ServiceBotModule — production singletons for the service_bot module.

Bindings:

- ``BotPublishRepositoryProtocol`` — database-mode-keyed; SQLite vs
  ZDAS is decided by the injected ``DatabasePlugin`` (unified ORM,
  single source). Tests swap SQLite via ``TestingDatabaseModule``;
  this module needs no dedicated testing override.
- ``BaasService`` — constructed eagerly via ``@inject``. The provider
  unpacks ``BaasConfig`` once and forwards string fields to the
  constructor (whose existing signature predates the typed config).
- ``BotPublishService`` — plain class, providers wire it up. Tests
  construct it directly with mocks.
- ``BotBuildService`` — same. ``DeviceService`` is still pulled
  inline through the legacy ``get_device_service_instance()``
  factory; that goes away when ``devices/`` migrates onto DI in
  Task 15.
- ``PublishFlowService`` — provider also fixes up the
  ``BotPublishService._publish_flow_service`` back-reference, since
  the two services have a circular relationship that is broken by
  setter injection on ``BotPublishService``. ``BotService`` is
  injected the same way for the ``offline_publish`` code path.

Tests construct services directly with mocks (no fake services); the
bindings exist so production routes can use ``Injected(...)``.
"""
from __future__ import annotations

from typing import Annotated, Callable

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.baas_service import BaasServiceProtocol
from agentclaw.community.api.bot_build_service import BotBuildServiceProtocol
from agentclaw.community.api.bot_publish_service import BotPublishServiceProtocol
from agentclaw.community.api.channel_service import ChannelServiceProtocol
from agentclaw.community.api.publish_flow_service import PublishFlowServiceProtocol
from agentclaw.community.api.publish_approval import PublishApprovalServiceProtocol
from agentclaw.community.api.quality_service import QualityTaskServiceProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.channel.services.engine_overrides_reader import (
    ChannelEngineOverridesReader,
)
from agentclaw.community.core.common_config import CommonConfigService, CommonWhiteListService
from agentclaw.community.core.config_compose.services.collector import (
    ConfigComposerInputCollector,
)
from agentclaw.community.core.config_compose.services.config_composer import ConfigComposer
from agentclaw.community.core.config_compose.services.mcporter_composer import McporterComposer
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.devices.services.baas_template_resolver import (
    SystemConfigBaasTemplateResolver,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.service_bot.services.deploy.teclaw_file_promotion import (
    TeclawFilePromotion,
)
from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.publish_operation_repository import (
    PublishOperationRepository,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.services.bot_build_service import BotBuildService
from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import (
    ArcaSnapshotProducer,
)
from agentclaw.community.core.service_bot.services.deploy.external_compose_producer import (
    ExternalComposeProducer,
)
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifactProducerRouter,
)
from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowService
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    PublishTaskLifecycle,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.service_bot.services.publish_approval_service import PublishApprovalService
from agentclaw.community.core.system_config import SystemConfigService
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.core.workspace.engines import create_engine_sandbox_registry
from agentclaw.community.core.workspace.path_factory import (
    WorkspacePathFactory,
)
from agentclaw.community.core.storage.path import (
    get_skills_repo_path,
    get_teclaw_bolt_data_prefix,
)
from agentclaw.community.di import config as cfg
from agentclaw.community.kernel.bot_config import StoreRef
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_BAAS, QUALIFIER_GENERAL
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugin_api.outbound_rules import OutboundRuleProvider
from agentclaw.community.plugin_api.approval_workflow import ApprovalWorkflowPlugin
from agentclaw.community.core.service_bot.repository.config_artifact_offload import (
    ConfigArtifactOffloader,
)
from agentclaw.community.plugins.bot_publish_repository import (
    BotPublishRepository as UnifiedBotPublishRepository,
)
from agentclaw.community.plugins.publish_operation_repository import (
    OrmPublishOperationRepository,
)
from agentclaw.community.utils import env_utils

logger = get_logger()


class ServiceBotModule(Module):
    """Production bindings for service_bot."""

    def configure(self, binder: Binder) -> None:
        binder.bind(WorkspacePathFactory, to=WorkspacePathFactory, scope=singleton)
        # Offloads an oversized config_artifact out of the ac_bot_publish.ext
        # TEXT column into object storage; injected into the repo below.
        binder.bind(
            ConfigArtifactOffloader,
            to=ConfigArtifactOffloader,
            scope=singleton,
        )
        # Single unified ORM impl — runs on prod OceanBase and SQLite
        # via the injected DatabasePlugin (@inject ctor).
        binder.bind(
            BotPublishRepositoryProtocol,
            to=UnifiedBotPublishRepository,
            scope=singleton,
        )
        # Publish operation ledger repository — same unified-ORM pattern; the
        # crash-safe operation ledger (ac_publish_operation).
        binder.bind(
            PublishOperationRepository,
            to=OrmPublishOperationRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def engine_sandbox_registry(
        self, workspace: cfg.WorkspaceConfig,
    ) -> EngineSandboxRegistry:
        """Construct shared EngineSandboxRegistry.

        Providers are mode-blind; the per-engine root paths flow in via
        ``WorkspaceConfig`` (from application.yaml). See Rule 14 — no
        downstream component branches on runtime mode.
        """
        return create_engine_sandbox_registry(workspace=workspace)

    @singleton
    @provider
    @inject
    def baas_service(
        self,
        baas: cfg.BaasConfig,
        bot_repo: BotRepository,
        bot_publish_repo: BotPublishRepositoryProtocol,
        system_config_service: SystemConfigService,
        device_binding_repo: DeviceBindingRepository,
        sandbox_registry: EngineSandboxRegistry,
        baas_http: Annotated[HttpClient, QUALIFIER_BAAS],
        general_http: Annotated[HttpClient, QUALIFIER_GENERAL],
        secret_resolver: SecretResolver,
        common_whitelist_service: CommonWhiteListService,
        outbound_rule_provider: OutboundRuleProvider,
    ) -> BaasService:
        """Construct ``BaasService`` from typed bindings.

        ``BaasConfig.api_base_url_pre`` is selected when the runtime
        env is ``pre``; otherwise ``api_base_url`` wins.
        ``storage_path`` is still imported as a module — it is a
        plain Python module, not a service.
        """
        from agentclaw.community.core.storage import path as storage_path

        api_base = (
            baas.api_base_url_pre
            if env_utils.get_current_env() == "pre"
            else baas.api_base_url
        )

        service = BaasService(
            baas_api_base=api_base,
            tenant=baas.tenant,
            template_uuid=baas.template_uuid,
            bot_repo=bot_repo,
            bot_publish_repo=bot_publish_repo,
            system_config_service=system_config_service,
            storage_path=storage_path,
            device_binding_repo=device_binding_repo,
            default_ttl_minutes=baas.default_ttl_minutes,
            sandbox_registry=sandbox_registry,
            http_client=baas_http,
            general_http_client=general_http,
            secret_resolver=secret_resolver,
            personal_bot_template_uuid=baas.personal_bot_template_uuid,
            common_whitelist_service=common_whitelist_service,
            outbound_rule_provider=outbound_rule_provider,
        )
        logger.info(
            "[NEW-ARCH] BaasService initialized: api_base=%s, tenant=%s, template_uuid=%s, "
            "personal_bot_template_uuid=%s",
            api_base,
            baas.tenant,
            baas.template_uuid,
            baas.personal_bot_template_uuid,
        )
        return service

    @singleton
    @provider
    @inject
    def bot_build_service(
        self,
        device_service: DeviceService,
        baas_service: BaasService,
        path_factory: WorkspacePathFactory,
        passport_plugin: PassportPlugin,
        device_binding_repo: DeviceBindingRepository,
        sandbox_registry: EngineSandboxRegistry,
        bot_repo: BotRepository,
        channel_service: ChannelServiceProtocol,
        baas: cfg.BaasConfig,
        common_whitelist_service: CommonWhiteListService,
        system_config_service: SystemConfigService,
    ) -> BotBuildService:
        """Construct ``BotBuildService`` with sandbox registry support."""
        return BotBuildService(
            device_service=device_service,
            baas_service=baas_service,
            path_factory=path_factory,
            passport_plugin=passport_plugin,
            device_binding_repo=device_binding_repo,
            sandbox_registry=sandbox_registry,
            channel_service=channel_service,
            bot_repository=bot_repo,
            teclaw_template_uuid=baas.teclaw_template_uuid,
            common_whitelist_service=common_whitelist_service,
            baas_template_resolver=SystemConfigBaasTemplateResolver(system_config_service),
        )

    @singleton
    @provider
    @inject
    def bot_publish_service(
        self,
        injector: Injector,
        bot_publish_repo: BotPublishRepositoryProtocol,
        bot_repo: BotRepository,
        bot_service: BotService,
        device_binding_repo: DeviceBindingRepository,
        bcn_service: BcnService,
        quality_task_service: QualityTaskServiceProtocol,
    ) -> BotPublishService:
        """Construct ``BotPublishService``.

        ``BotPublishService`` and ``PublishFlowService`` reference each
        other. The cycle is broken with a lazy ``Callable[[], PublishFlowService]``
        provider — ``BotPublishService`` only resolves it on first use,
        by which time the injector has cached the ``PublishFlowService``
        singleton.
        """
        return BotPublishService(
            bot_publish_repo=bot_publish_repo,
            bot_repo=bot_repo,
            bot_service=bot_service,
            device_binding_repo=device_binding_repo,
            bcn_service=bcn_service,
            quality_task_service=quality_task_service,
            publish_flow_service_provider=lambda: injector.get(PublishFlowService),
        )

    @provider
    @singleton
    def arca_snapshot_producer(
        self, bot_build_service: BotBuildService
    ) -> ArcaSnapshotProducer:
        """ARCA build-snapshot producer — wraps the existing ``build()``."""
        return ArcaSnapshotProducer(bot_build_service)

    @singleton
    @provider
    def mcporter_composer(self) -> McporterComposer:
        return McporterComposer()

    @singleton
    @provider
    @inject
    def config_composer_input_collector(
        self, injector: Injector
    ) -> ConfigComposerInputCollector:
        """Concrete collector (Task 15a) — constructed explicitly so the
        identity↔harness import cycle stays deferred (collector types
        ``IdentityService`` under ``TYPE_CHECKING``)."""
        from agentclaw.community.core.services.identity import IdentityService
        from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
        from agentclaw.community.core.mcp.services.config_service import MCPConfigService
        from agentclaw.community.core.resources.repository.protocol import (
            ResourceRepositoryProtocol,
        )

        return ConfigComposerInputCollector(
            skill_set_service_factory=injector.get(SkillSetServiceFactory),
            mcp_config_service=injector.get(MCPConfigService),
            resource_repository=injector.get(ResourceRepositoryProtocol),
            bot_repo=injector.get(BotRepository),
            path_factory=injector.get(WorkspacePathFactory),
            identity_service=injector.get(IdentityService),
            overrides_reader=injector.get(ChannelEngineOverridesReader),
        )

    @singleton
    @provider
    @inject
    def config_composer(
        self,
        mcporter_composer: McporterComposer,
        collector: ConfigComposerInputCollector,
        bot_oss: cfg.ObjectStorageConfig,
    ) -> ConfigComposer:
        """Single backend config composer (Task 8 + collector DI Task 15a).

        ``stores`` carries the physical coordinates (location only, no
        credentials) for each ``store_id`` a source may reference. The composer
        embeds only the stores a bot actually references. The artifact is teclaw's
        (ARCA uses the rsync snapshot, never composes), and teclaw fetches from
        **OSS**, so both stores are ``type="oss"`` in the OSS bucket
        (``ObjectStorageConfig.bucket_name``):

        - ``skill-repo`` — the global shared skills market
          (``get_skills_repo_path()`` = ``aidesktop/{env_dir}/bolt_shared/skills-repo``,
          the canonical OSS key, same one ARCA mounts); shared across bots, engine
          pulls the whole package from OSS.
        - ``bot-data`` — all per-bot files (resources, identity files, AND user
          skills-local skills), under the teclaw namespace
          ``get_teclaw_bolt_data_prefix()`` = ``teclaw/{env}/bolt_data``. The per-bot
          ``{entity}/{bot}/...`` lives in the ref path, so one base serves every bot.
          The teclaw fs plugin materializes bytes to ``{base}/{path}`` (via
          ``ConfigComposer.oss_location``), so write-key == this artifact ref."""
        return ConfigComposer(
            mcporter_composer=mcporter_composer,
            collector=collector,
            stores={
                "skill-repo": StoreRef(
                    type="oss",
                    bucket=bot_oss.bucket_name,
                    base=get_skills_repo_path(),
                ),
                "bot-data": StoreRef(
                    type="oss",
                    bucket=bot_oss.bucket_name,
                    base=get_teclaw_bolt_data_prefix(),
                ),
            },
        )

    @singleton
    @provider
    @inject
    def external_compose_producer(
        self, composer: ConfigComposer
    ) -> ExternalComposeProducer:
        """External (compose-from-DB+NAS) build producer. Base impl —
        ``engine_ext`` defaults to ``{}``; the teclaw subclass that fetches a
        real ``engine_ext`` is 🔒 Task 15 (engine handshake). The composed
        artifact is refs-only and pinned straight onto ``ext.config_artifact``
        (no separate materialization store)."""
        return ExternalComposeProducer(composer)

    @singleton
    @provider
    @inject
    def deploy_artifact_producer_router(
        self,
        arca_producer: ArcaSnapshotProducer,
        external_producer: ExternalComposeProducer,
    ) -> DeployArtifactProducerRouter:
        """Provider-keyed build-artifact producer selector (Task 12 + 15a).

        arca/baas → existing ``build()`` (behavior-unchanged); teclaw →
        the compose-from-DB+NAS producer (now that the ``ConfigComposer``
        collector DI has landed). No teclaw bots exist yet, so this changes
        nothing for current bots; the prod teclaw ``engine_ext`` fetch is Task 15.
        """
        return DeployArtifactProducerRouter(
            providers={
                "arca": arca_producer,
                "baas": arca_producer,
                "teclaw": external_producer,
            },
            default_provider_key="baas",
        )

    @singleton
    @provider
    @inject
    def publish_flow_service(
        self,
        injector: Injector,
        bot_publish_service: BotPublishService,
        bot_build_service: BotBuildService,
        baas_service: BaasService,
        bot_service: BotService,
        producer_router: DeployArtifactProducerRouter,
        common_config_service: CommonConfigService,
        device_binding_repo: DeviceBindingRepository,
        resolver: DeviceContextResolver,
        device_fs_dispatcher: DeviceFilesystemDispatcher,
        oss_storage: ObjectStoragePlugin,
        channel_overrides_reader: ChannelEngineOverridesReader,
        task_queue_service: TaskQueueService,
        publish_operation_repo: PublishOperationRepository,
    ) -> PublishFlowService:
        """Construct ``PublishFlowService``.

        The cycle with ``BotPublishService`` is broken on the other
        side via a lazy ``Callable[[], PublishFlowService]`` provider —
        no back-reference setup needed here. The build-artifact producer
        router is injected (see ``deploy_artifact_producer_router``). The
        resolver + device-fs dispatcher + a ``TeclawFilePromotion`` (over the
        OSS client) power the teclaw build-time file snapshot.
        """
        return PublishFlowService(
            bot_publish_service=bot_publish_service,
            bot_build_service=bot_build_service,
            baas_service=baas_service,
            bot_service=bot_service,
            producer_router=producer_router,
            common_config_service=common_config_service,
            device_binding_repo=device_binding_repo,
            resolver=resolver,
            device_fs_dispatcher=device_fs_dispatcher,
            teclaw_file_promotion=TeclawFilePromotion(oss_storage=oss_storage),
            channel_overrides_reader=channel_overrides_reader,
            task_queue_service=task_queue_service,
            publish_operation_repo=publish_operation_repo,
        )

    @singleton
    @provider
    @inject
    def publish_task_lifecycle(
        self,
        injector: Injector,
        registry: HandlerRegistry,
        flow: PublishFlowService,
        task_queue_service: TaskQueueService,
    ) -> PublishTaskLifecycle:
        """Register the durable publish task handlers into the shared
        ``HandlerRegistry``. A singleton ``Lifecycle`` so discovery runs its
        ``bootstrap()`` before ``TaskWorker.startup()`` claims (mirrors
        ``baas_publish_task_lifecycle`` in ``DevicesModule``).

        The approval trigger handler resolves ``PublishApprovalService`` lazily to
        break its DI cycle with ``PublishFlowService``.
        """
        return PublishTaskLifecycle(
            registry=registry,
            flow=flow,
            task_queue_service=task_queue_service,
            approval_service_provider=lambda: injector.get(PublishApprovalService),
        )

    @singleton
    @provider
    @inject
    def baas_service_factory(
        self, injector: Injector
    ) -> Callable[[], BaasService]:
        """Lazy provider so callers can break injector cycles by deferring
        ``BaasService`` resolution to call time (mirrors
        ``bot_service_factory`` in ``bot_management_module``).
        """
        return lambda: injector.get(BaasService)

    @singleton
    @provider
    @inject
    def _baas_service_protocol(self, svc: BaasService) -> BaasServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _bot_build_service_protocol(self, svc: BotBuildService) -> BotBuildServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _bot_publish_service_protocol(self, svc: BotPublishService) -> BotPublishServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _publish_flow_service_protocol(self, svc: PublishFlowService) -> PublishFlowServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def publish_approval_service(
        self,
        injector: Injector,
        publish_service: BotPublishService,
        process_service: ApprovalWorkflowPlugin,
        bot_service: BotService,
        task_queue_service: TaskQueueService,
    ) -> PublishApprovalService:
        """Construct ``PublishApprovalService`` with lazy publish flow service provider.

        The cycle with ``PublishFlowService`` is broken via a lazy ``Callable[[], PublishFlowService]``
        provider — the approval service only resolves it on first use (in the callback handler),
        by which time the injector has cached the singleton.
        """
        return PublishApprovalService(
            publish_service=publish_service,
            publish_flow_service_provider=lambda: injector.get(PublishFlowService),
            process_service=process_service,
            bot_service=bot_service,
            task_queue_service=task_queue_service,
        )

    @singleton
    @provider
    @inject
    def _publish_approval_service_protocol(
        self, svc: PublishApprovalService
    ) -> PublishApprovalServiceProtocol:
        return svc
