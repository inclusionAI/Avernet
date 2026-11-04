"""
Bot service for managing bot lifecycle.

This service handles bot creation, retrieval, updates, and deletion.
It integrates with the device service to allocate devices for bots.

Mechanical split note: the service body is composed from the mixins in
``bot_service_parts/`` — members were moved there verbatim, and this module
remains the single public import path and composition point. The
module-level exceptions, constants and helpers now live in
``bot_service_parts.common`` and are re-exported below so every historical
``from ...bot_service import X`` and mock-patch target keeps working.
"""
import threading  # patched as `...bot_service.threading.Thread` in tests; keep.

from typing import Callable, Optional, TYPE_CHECKING

from agentclaw.community.core.common_config.bot_config_protocol import BotStoragePolicyProtocol
from agentclaw.community.core.bot_management.bot_service_protocol import BotServiceProtocol
from agentclaw.community.core.bot_management.services.template_service import TemplateService
from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_service import WorkspaceHostingService
from agentclaw.community.core.bot_management.services.bot_service_parts.common import (
    BotInstanceCreationError,
    BotInvalidLifecycleStateError,
    BotLimitExceededError,
    BotNameExistsError,
    BotNameInvalidError,
    BotNotFoundError,
    BotOperationNotAllowedError,
    BotPermissionError,
    BotServiceError,
    DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE,
    DefaultBotTeclawNotAllowedError,
    DeviceAllocationError,
    DeviceLimitError,
    PROVISIONING_CLAIM_STALE_SECONDS,
    RESTART_LOCK_TTL_SECONDS,
    _BOT_NAME_ALLOWED_RE,
    _BOT_NAME_MAX_LEN,
    _CALLER_REFRESH_MAX_WORKERS,
    _compose_operator_context,
    _copy_tree_fast,
    generate_bot_id,
    logger,
    validate_bot_name,
)
from agentclaw.community.core.bot_management.services.bot_service_parts.runtime_setup import BotRuntimeSetupMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.quota import BotQuotaMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.creation import BotCreationMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.provisioning import BotProvisioningMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.queries import BotQueriesMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.metadata import BotMetadataMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.collaboration import BotCollaborationMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.deletion import BotDeletionMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.lifecycle import BotLifecycleMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.restart import BotRestartMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.publication import BotPublicationMixin
from agentclaw.community.core.bot_management.services.bot_service_parts.credentials import BotCredentialsMixin

from agentclaw.community.core.desktop_bot.device_status_client import DeviceStatusClient
from agentclaw.community.core.devices.services.device_service import (  # patched by guard tests
    DeviceService,  # and referenced by the quoted provider annotations below
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot import BotRestartLockRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from agentclaw.community.core.repository.protocols.devices import OssToNasRecordRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.repository.protocols.identity import CallerIdentityRepositoryProtocol
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolLayoutRepositoryProtocol
from agentclaw.community.core.skills_pool.native_creation import SkillsPoolNativeCreationPolicy
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.plugin_api.drm import DRMReaderPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
    from agentclaw.community.core.bot_management.services.bcn_service import BcnService
    from agentclaw.community.core.bot_management.services.cleanup_service import BotCleanupService
    from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
    from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
    from agentclaw.community.core.service_bot.services.baas_service import BaasService
    from agentclaw.community.core.bot_management.services.teclaw_provision_service import (
        TeclawProvisionService,
    )
    from agentclaw.community.core.devices.services.baas_template_resolver import (
        BaasTemplateResolverProtocol,
    )
    from agentclaw.community.core.cron.services.aicoding.cron_auto_setup import CronAutoSetupService
    from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
    from agentclaw.community.core.common_config.service import CommonConfigService
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        BotRuntimeProjectorProtocol as CoreBotRuntimeProjectorProtocol,
    )
    from agentclaw.community.core.bot_app_grant.protocols import (
        BotAppGrantSweepProtocol,
    )
    # Type-only: importing ``agentclaw.community.di`` at runtime would form a cycle
    # (di/__init__ -> container -> aicoding_module -> workspace_service ->
    # bot_service). ``BotService`` is provider-constructed, so this
    # annotation is never resolved at runtime — same pattern as
    # ``SkillSetServiceFactory`` below.
    from agentclaw.community.di import config as cfg
    from agentclaw.community.core.access.policy_service_protocol import PolicyServiceProtocol
    from agentclaw.community.core.bot_management.bot_quota_service_protocol import (
        BotQuotaServiceProtocol,
    )
    from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import (
        RestartBackupDependencies,
    )


class BotService(
    BotRuntimeSetupMixin,
    BotQuotaMixin,
    BotCreationMixin,
    BotProvisioningMixin,
    BotQueriesMixin,
    BotMetadataMixin,
    BotCollaborationMixin,
    BotDeletionMixin,
    BotLifecycleMixin,
    BotRestartMixin,
    BotPublicationMixin,
    BotCredentialsMixin,
    BotServiceProtocol,
):
    """Bot service for managing bot lifecycle."""

    def __init__(
        self,
        repository: BotRepository,
        allocation_config: "cfg.DeviceAllocationConfig",
        device_binding_repo: DeviceBindingRepository,
        skill_set_factory: "SkillSetServiceFactory",
        cleanup_service: "BotCleanupService",
        bcn_service: "BcnService",
        bot_publish_repo: "BotPublishRepositoryProtocol",
        passport_plugin: PassportPlugin,
        oss_record_repo: "OssToNasRecordRepository",
        bot_publish_service_provider: "Callable[[], BotPublishService]",
        device_service_provider: "Callable[[], DeviceService]",
        bot_app_grant_service_provider: "Callable[[], BotAppGrantSweepProtocol]",
        path_factory: WorkspacePathFactory,
        template_service: TemplateService,
        # Optional: DIMA (applicationCoding) hosting is corp-only. Community does
        # not install WorkspaceHostingService, so this is None there (B8); the two
        # applicationCoding call sites guard via ``_require_workspace_hosting``.
        workspace_hosting_service: "Optional[WorkspaceHostingService]",
        collaborator_repo: CollaboratorRepositoryProtocol,
        restart_lock_repo: BotRestartLockRepositoryProtocol,
        teclaw_provision_service_provider: "Callable[[], TeclawProvisionService]",
        device_status_client: "DeviceStatusClient",
        cron_auto_setup_service_provider: "Callable[[], CronAutoSetupService]",
        drm_reader: DRMReaderPlugin,
        caller_identity_repo: "CallerIdentityRepositoryProtocol",
        skills_pool_native_creation_policy: SkillsPoolNativeCreationPolicy,
        skill_layout_repository: SkillsPoolLayoutRepositoryProtocol,
        workspace_hosting_config: "cfg.WorkspaceHostingConfig | None" = None,
        policy_service: "PolicyServiceProtocol | None" = None,
        baas_template_resolver: "BaasTemplateResolverProtocol | None" = None,
        baas_service_provider: "Callable[[], BaasService] | None" = None,
        task_queue_service: "TaskQueueService | None" = None,
        common_config_service: "CommonConfigService | None" = None,
        runtime_reconciler: "CoreBotRuntimeProjectorProtocol | None" = None,
        runtime_reconciler_provider: "Callable[[], CoreBotRuntimeProjectorProtocol] | None" = None,
        bot_quota_service: "BotQuotaServiceProtocol | None" = None,
        bot_storage_policy: BotStoragePolicyProtocol | None = None,
        restart_backup_dependencies: "RestartBackupDependencies | None" = None,
    ) -> None:
        self._repository = repository
        self._skills_pool_native_creation_policy = skills_pool_native_creation_policy
        self._skill_layout_repository = skill_layout_repository
        self._bot_storage_policy = bot_storage_policy
        self._allocation_config = allocation_config
        if workspace_hosting_config is None:
            from agentclaw.community.di.config import WorkspaceHostingConfig

            workspace_hosting_config = WorkspaceHostingConfig()
        self._workspace_hosting_config = workspace_hosting_config
        self._device_binding_repo = device_binding_repo
        self._skill_set_factory = skill_set_factory
        self._cleanup_service = cleanup_service
        self._bcn_service = bcn_service
        self._bot_publish_repo = bot_publish_repo
        self._passport_plugin = passport_plugin
        self._caller_identity_repo = caller_identity_repo
        self._oss_record_repo = oss_record_repo
        self._drm_reader = drm_reader
        # Cycle-breakers: BotPublishService.__init__ depends on BotService,
        # and DeviceService is constructed with BotService as ``bot_sync``.
        # Hold lazy ``Callable[[], T]`` factories and resolve on demand so
        # the cycle never closes during graph build.
        self._bot_publish_provider = bot_publish_service_provider
        self._device_service_provider = device_service_provider
        self._restart_backup_dependencies = restart_backup_dependencies
        # Typed against a **core-level protocol**, not the concrete service:
        # selecting an implementation is the composition root's job. Not the
        # Service API Protocol either — core may not import ``api/``, which the
        # architecture suite enforces — hence
        # ``core/bot_app_grant/protocols.py``, the same shape
        # ``core/bot_collaborator`` uses for the same pair of rules.
        #
        # Required, not optional: deleting a bot has to withdraw the
        # authorizations standing against it, and a BotService that could not
        # would delete bots while leaving applications able to reach them.
        # There is no "grants not configured" state worth modelling — the
        # alternative to a provider here is a silent security hole.
        self._bot_app_grant_provider = bot_app_grant_service_provider
        self._path_factory = path_factory
        self._template_service = template_service
        self._workspace_hosting_service = workspace_hosting_service

        # DIMA (applicationCoding) hosting is corp-only; community leaves this
        # unbound. Both use sites go through ``_require_workspace_hosting`` (B8).
        self._collaborator_repo = collaborator_repo
        self._restart_lock_repo = restart_lock_repo
        # Lazy (cycle-safe): teclaw provisioning pulls in BaasService +
        # ConfigComposer, whose graphs transitively reach BotService.
        self._teclaw_provision_provider = teclaw_provision_service_provider
        # Lazy (cycle-safe): BaaS 原地重启入口。baas bot restart 走 BaaSService.restart_bot
        # 不 destroy+recreate，device_uuid 不变 → session NAS 复用。arca 无此 provider 走旧路。
        self._baas_service_provider = baas_service_provider
        # Leaf BaaS device-status reader. The by-owner list shows desktop bots'
        # LIVE status (the DB status lags), so it reads BaaS directly via this
        # leaf. A bare httpx client (no service deps) keeps the list off the
        # DesktopBotService → DeviceService → BotService edge — no DI cycle.
        self._device_status_client = device_status_client
        # Lazy (cycle-safe): CronAutoSetupService transitively depends on
        # services that reach BotService, so we hold a Callable factory.
        self._cron_auto_setup_provider = cron_auto_setup_service_provider
        self._policy_service = policy_service
        self._baas_template_resolver = baas_template_resolver
        self._task_queue_service = task_queue_service
        self._common_config_service = common_config_service
        self._runtime_reconciler = runtime_reconciler
        self._runtime_reconciler_provider = runtime_reconciler_provider
        self._bot_quota_service = bot_quota_service

    def _require_workspace_hosting(self) -> "WorkspaceHostingService":
        """Return the workspace-hosting service, or raise if not configured.

        Workspace hosting (applicationCoding) is corp-only; community leaves
        ``WorkspaceHostingService`` unbound (B8). The applicationCoding paths call
        this so the failure is an explicit, clear error rather than an
        ``AttributeError`` on ``None``.
        """
        if self._workspace_hosting_service is None:
            raise BotServiceError(
                "Workspace-hosting service is not configured in this deployment; "
                "applicationCoding bots require it."
            )
        return self._workspace_hosting_service

    def is_workspace_hosting_available(self) -> bool:
        """Whether this deployment has a bound Workspace Hosting implementation.

        The route preflight calls this so applicationCoding can be refused (503)
        before a bot id is allocated or a Passport is applied, rather than
        tripping ``_require_workspace_hosting`` mid-create after side effects.
        """
        return self._workspace_hosting_service is not None
