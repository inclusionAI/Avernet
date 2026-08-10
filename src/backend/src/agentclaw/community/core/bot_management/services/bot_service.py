"""
Bot service for managing bot lifecycle.

This service handles bot creation, retrieval, updates, and deletion.
It integrates with the device service to allocate devices for bots.
"""
import json
import random
import re
import shutil
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List, Tuple, TYPE_CHECKING

from agentclaw.community.core.bot_management.capabilities import (
    can_join_bcn_as_provider,
    has_declared_capabilities,
    is_template_factory_config,
)
from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AICODING_ENGINE_TYPE,
)
from agentclaw.community.core.bot_management.engines.registry import (
    normalize_engine_type,
    resolve_baas_engine_bucket,
)
from agentclaw.community.core.bot_management.services.template_service import TemplateService
from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_service import WorkspaceHostingService
from agentclaw.community.core.desktop_bot.device_status_client import DeviceStatusClient
from agentclaw.community.core.desktop_bot.status_mapping import map_baas_to_display

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
    from agentclaw.community.core.bot_app_grant.protocols import (
        BotAppGrantSweepProtocol,
    )
    # Type-only: importing ``agentclaw.community.di`` at runtime would form a cycle
    # (di/__init__ -> container -> aicoding_module -> workspace_service ->
    # bot_service). ``BotService`` is provider-constructed, so this
    # annotation is never resolved at runtime — same pattern as
    # ``SkillSetServiceFactory`` below.
    from agentclaw.community.di import config as cfg
    from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.core.bot_management.repository.models import BotRestartLockRecord
from agentclaw.community.core.repository.protocols.bot import BotRestartLockRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.services.default_image_policy_listener import (
    DEFAULT_IMAGE_POLICY_VALUE,
    IMAGE_POLICY_ON_ACTIVE_KEY,
)
from agentclaw.community.core.bot_collaborator.models import CollaboratorRole
from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE, _get_engine_types
from agentclaw.community.core.workspace.path_factory import (
    WorkspacePathFactory,
    get_bot_engine_dir,
    get_bot_engine_config_dir,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.core.service_bot.services.arca_image_pin import (
    apply_default_image_to_ext,
    clear_image_policy_from_ext,
    overlay_image_pin_on_template_config,
    persist_default_image_policy,
    resolve_current_arca_image,
)
from agentclaw.community.utils.avernet_tenant import (
    bind_current_avernet_tenant,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.devices.errors import (
    DeviceNotFoundError,
    InvalidDeviceStatusError,
    DeviceLimitExceededError,
    ResourceInsufficientError,
    DeviceAllocateError,
)
from agentclaw.community.core.devices.models import AllocatedDevice, DeviceAllocationMode, DeviceBindingStatus, OperatorContext, SynlinkMappingInfo
from agentclaw.community.core.repository.protocols.devices import OssToNasRecordRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_service import (
    ARCA_DEVICE_PROVIDER,
    BAAS_DEVICE_PROVIDER,
    DeviceService,
)
from agentclaw.community.plugin_api.drm import DRMReaderPlugin
from agentclaw.community.plugin_api.passport import PassportError
from agentclaw.community.plugin_api.passport import PassportPlugin


from agentclaw.community.log import get_logger

logger = get_logger()

# Restart idempotency guard TTL. The full device-allocation path has a p99 of
# ~2 min. A guard row older than this is considered abandoned (holder crashed)
# and is reaped on the next restart attempt so a bot is never permanently
# blocked from restarting.
RESTART_LOCK_TTL_SECONDS = 120

# Passport refresh is callback-driven and retryable. Keep caller-instance
# fan-out bounded while still attempting every instance before reporting an
# aggregate failure.
_CALLER_REFRESH_MAX_WORKERS = 5


def _compose_operator_context(user_id: str, nick_name: str) -> OperatorContext:
    """构建操作人上下文。"""
    return OperatorContext(
        staff_id=user_id,
        staff=user_id,
        nick_name=nick_name,
        operator_name=nick_name,
        tenant_id="default",
    )


class BotServiceError(Exception):
    """Base exception for bot service errors."""
    pass


class BotInvalidLifecycleStateError(BotServiceError):
    """The requested operation is not allowed in the bot's current state."""

    def __init__(self, *, bot_id: str, current_status: str) -> None:
        self.bot_id = bot_id
        self.current_status = current_status
        super().__init__(
            f"Bot {bot_id} cannot be restarted while status is {current_status}"
        )


class BotOperationNotAllowedError(BotServiceError):
    """The operation is not supported for this bot and never will be.

    Distinct from a transient failure: retrying cannot help, so delivery
    surfaces should report it as a client error rather than a server fault.
    """
    pass


class BotNotFoundError(BotServiceError):
    """Bot not found error."""
    pass


class DeviceAllocationError(BotServiceError):
    """Device allocation error."""
    pass


class BotNameExistsError(BotServiceError):
    """Bot name already exists for owner."""
    pass


class DeviceLimitError(BotServiceError):
    """Device limit exceeded error."""
    pass


class BotInstanceCreationError(BotServiceError):
    """Bot instance creation error."""
    pass


class BotPermissionError(BotServiceError):
    """Bot permission error - user is not the owner."""
    pass


class BotNameInvalidError(BotServiceError):
    """Bot name fails validation (empty / illegal chars / too long)."""
    pass


class BotLimitExceededError(BotServiceError):
    """Owner has reached the maximum number of bots allowed."""
    pass


DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE = (
    "Teclaw Cloud Bot 不能作为 Default Bot，请先创建其他类型的 Bot。"
)


class DefaultBotTeclawNotAllowedError(BotServiceError):
    """Default Bot cannot use a Teclaw Cloud engine."""

    def __init__(self) -> None:
        super().__init__(DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE)


# 仅允许中英文、数字、下划线、中划线、空格；禁止 @ # / 等特殊字符。
_BOT_NAME_MAX_LEN = 32
_BOT_NAME_ALLOWED_RE = re.compile(r"^[\w一-鿿 \-]+$", re.UNICODE)


def validate_bot_name(bot_name: Optional[str]) -> str:
    """Validate bot_name; return the trimmed value or raise BotNameInvalidError.

    Rules:
    - must be non-empty after strip
    - length <= 32 characters
    - only Chinese/letters/digits/underscore/hyphen/space allowed
    """
    if bot_name is None:
        raise BotNameInvalidError("Bot 名称不能为空")
    trimmed = bot_name.strip()
    if not trimmed:
        raise BotNameInvalidError("Bot 名称不能为空")
    if len(trimmed) > _BOT_NAME_MAX_LEN:
        raise BotNameInvalidError(
            f"Bot 名称长度不能超过 {_BOT_NAME_MAX_LEN} 字符（当前 {len(trimmed)}）"
        )
    if not _BOT_NAME_ALLOWED_RE.match(trimmed):
        raise BotNameInvalidError(
            "Bot 名称只能包含中英文、数字、下划线、中划线和空格，不允许 @、# 等特殊字符"
        )
    return trimmed


def _copy_tree_fast(src: Path, dst: Path, symlinks: bool = True) -> None:
    """
    使用 rsync 快速复制目录，优化 NAS 场景下的性能。

    rsync 采用增量同步算法，对于已有文件会跳过，大幅提升 NAS 复制速度。
    如果 rsync 不可用，回退到 shutil.copytree。

    Args:
        src: 源目录路径
        dst: 目标目录路径
        symlinks: 是否保留符号链接（默认 True）
    """
    import subprocess

    if not src.exists():
        raise FileNotFoundError(f"Source directory does not exist: {src}")

    # 首先尝试使用 rsync
    try:
        cmd = ["rsync", "-a", "--delete"]
        if symlinks:
            cmd.append("--links")  # 保留符号链接
        # 源目录以 / 结尾表示复制目录内容而不是目录本身
        cmd.extend([str(src) + "/", str(dst) + "/"])

        logger.info(f"[_copy_tree_fast] Using rsync: {src} -> {dst}")
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"[_copy_tree_fast] Rsync completed: {src} -> {dst}")
        return

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"[_copy_tree_fast] Rsync failed ({e}), falling back to shutil.copytree")

    # Fallback: 使用 shutil.copytree
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=symlinks)


def generate_bot_id(owner_id: str, bot_repository: BotRepository) -> str:
    """Return a globally-unique bot_id.

    Never returns 'default' — that convention is retired. ``bot_repository`` is
    retained in the signature for call-site compatibility; id allocation no
    longer depends on owner history.
    """
    # owner_id retained as the semantic subject (whose bot); not used in id allocation.
    del bot_repository  # unused; kept for call-site compatibility
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{date_part}_{random_part}"


class BotService:
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
        workspace_hosting_config: "cfg.WorkspaceHostingConfig | None" = None,
        policy_service: "PolicyServiceProtocol | None" = None,
        baas_template_resolver: "BaasTemplateResolverProtocol | None" = None,
        baas_service_provider: "Callable[[], BaasService] | None" = None,
        task_queue_service: "TaskQueueService | None" = None,
        common_config_service: "CommonConfigService | None" = None,
    ) -> None:
        self._repository = repository
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
        self._oss_record_repo = oss_record_repo
        self._drm_reader = drm_reader
        # Cycle-breakers: BotPublishService.__init__ depends on BotService,
        # and DeviceService is constructed with BotService as ``bot_sync``.
        # Hold lazy ``Callable[[], T]`` factories and resolve on demand so
        # the cycle never closes during graph build.
        self._bot_publish_provider = bot_publish_service_provider
        self._device_service_provider = device_service_provider
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

    def _service_bot_image_policy_enabled(self) -> bool:
        """Whether draft create/restart should opt into image policy."""
        return (
            resolve_current_arca_image(
                getattr(self, "_common_config_service", None),
                env=get_current_env(),
            )
            is not None
        )

    def _mark_service_bot_default_image(
        self,
        bot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply the draft ARCA image policy only when fully configured."""
        if bot.get("bot_type") != "service" or self.is_teclaw_bot(
            bot.get("active_engine")
        ):
            return bot
        updated_bot = dict(bot)
        if self._service_bot_image_policy_enabled():
            updated_bot["ext"] = apply_default_image_to_ext(bot.get("ext"))
        else:
            updated_bot["ext"] = clear_image_policy_from_ext(bot.get("ext"))
        return updated_bot

    def _persist_service_bot_default_image(
        self,
        bot: Dict[str, Any],
        *,
        user_id: str,
    ) -> None:
        """Persist an accepted draft restart's default policy to Bot and Draft."""
        if (
            bot.get("bot_type") != "service"
            or self.is_teclaw_bot(bot.get("active_engine"))
            or not self._service_bot_image_policy_enabled()
        ):
            return

        persist_default_image_policy(
            bot_repository=self._repository,
            publish_repository=self._bot_publish_repo,
            bot_id=str(bot["bot_id"]),
            owner_id=user_id,
            env=get_current_env(),
            common_config_service=self._common_config_service,
        )

    def _build_engine_extra_envs(
        self,
        *,
        bot_id: str,
        owner_id: str,
        active_engine: "str | None",
        bot_type: str,
        template_type: "str | None",
        template_config: "Optional[Dict[str, Any]]",
        log_context: str,
    ) -> "Optional[Dict[str, Any]]":
        """Build engine-specific extra_envs via the provisioning strategy.

        Centralizes the create / restart / start provisioning so each call site
        only supplies the metadata it actually has.  Fails soft: any strategy
        error is logged and treated as "no extra envs" so device allocation is
        never blocked by the engine layer.  Logging is engine-agnostic (logs the
        whole ``extra_envs`` dict) so new engine strategies are picked up without
        touching bot_service.
        """
        try:
            from agentclaw.community.core.bot_management.engines import (
                resolve_provisioning,
            )

            ctx, strategy = resolve_provisioning(
                bot_id=bot_id,
                owner_id=owner_id,
                active_engine=active_engine,
                bot_type=bot_type,
                template_type=template_type,
                template_config=template_config,
            )
            extra_envs = strategy.build_extra_envs(ctx)
            if extra_envs:
                logger.info(
                    "[%s] Setting engine extra_envs for bot %s: %s",
                    log_context,
                    bot_id,
                    extra_envs,
                )
            return extra_envs
        except Exception as e:
            logger.warning(
                "[%s] Failed to build engine extra_envs for bot %s: %s",
                log_context,
                bot_id,
                e,
            )
            return None

    def _extract_engine_runtime_token(
        self,
        *,
        bot_id: str,
        owner_id: str,
        active_engine: "str | None",
        bot_type: str,
        template_type: "str | None",
        template_config: "Optional[Dict[str, Any]]",
        log_context: str,
    ) -> "Optional[str]":
        """Resolve the engine runtime token (symmetric to ``_build_engine_extra_envs``).

        Used by the update_bot token-refresh path so it shares the same
        ``resolve_provisioning`` entry point instead of rebuilding context +
        strategy inline.  Fails soft (returns None).
        """
        try:
            from agentclaw.community.core.bot_management.engines import (
                resolve_provisioning,
            )

            ctx, strategy = resolve_provisioning(
                bot_id=bot_id,
                owner_id=owner_id,
                active_engine=active_engine,
                bot_type=bot_type,
                template_type=template_type,
                template_config=template_config,
            )
            token = strategy.extract_runtime_token(ctx)
            if token:
                logger.info(
                    "[%s] Resolved engine runtime token for bot %s",
                    log_context,
                    bot_id,
                )
            return token
        except Exception as e:
            logger.warning(
                "[%s] Failed to extract engine runtime token for bot %s: %s",
                log_context,
                bot_id,
                e,
            )
            return None

    @staticmethod
    def _should_trigger_memory_initialization(
        *,
        active_engine: "str | None",
        template_type: "str | None",
        template_config: "Optional[Dict[str, Any]]",
        old_template_config: "Optional[Dict[str, Any]]" = None,
        on_create: bool = False,
    ) -> bool:
        """Whether to reuse the AppCoding memory/Wiki initialization path.

        Template-factory bots consume business Wiki / RepoWiki through the same
        AppCoding runtime pipeline, but the source of truth is AC resolved
        ``template_config``.  Keep applicationCoding legacy behavior on create,
        and let template-factory bots trigger only when their template snapshot
        actually declares repo/wiki sources.
        """
        if active_engine != "claude_code" or not isinstance(template_config, dict):
            return False

        # Legacy applicationCoding keeps the original always-init-on-create
        # behavior.  Template-factory bots (normalCC / architect / user-created)
        # are detected from the resolved template snapshot instead of a backend
        # template_type whitelist, and only initialize memory when repo/wiki
        # sources are declared.
        is_legacy_application_coding = template_type == "applicationCoding"
        is_template_factory_bot = is_template_factory_config(template_config)
        if not is_legacy_application_coding and not is_template_factory_bot:
            return False

        try:
            from agentclaw.community.core.bot_management.utils import (
                memory_sources_changed,
            )

            empty_config: Dict[str, Any] = {}
            has_sources = memory_sources_changed(empty_config, template_config)
            if on_create:
                return is_legacy_application_coding or has_sources
            return memory_sources_changed(old_template_config or empty_config, template_config)
        except Exception as e:
            logger.warning(
                "[bot_service.memory_init] source detection failed: "
                "template_type=%s active_engine=%s error=%s",
                template_type,
                active_engine,
                e,
            )
            return template_type == "applicationCoding" and on_create

    def _attach_template_uid_context(
        self,
        *,
        bot_id: str,
        user_id: str,
        bot_type: str,
        engine_type: str | None,
        template_type: str | None,
        template_config: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """为设备分配补上 template_uid 上下文。

        这里还没确定最终 provider，因此只记录解析结果，不直接阻断流程。
        如果后面路由到 ARCA，这些信息不会影响分配；如果路由到 BaaS，
        BaasDeviceService 会再校验 template_uid，并基于这里记录的错误 fail-fast。
        """
        template_resolver = getattr(self, "_baas_template_resolver", None)
        if template_resolver is None:
            return template_config

        resolved_template_config = dict(template_config or {})
        try:
            template_uid = template_resolver.resolve_template_uid(
                bot_id=bot_id,
                user_id=user_id,
                env=get_current_env(),
                bot_type=bot_type,
                engine_type=engine_type,
                template_type=template_type,
                template_config=template_config,
            )
            resolved_template_config["template_uid"] = template_uid
            resolved_template_config.pop("_baas_template_uid_resolution_error", None)
            logger.info(
                "[bot_service.template_uid] attached: bot_id=%s user_id=%s "
                "bot_type=%s engine_type=%s template_type=%s template_uid=%s",
                bot_id,
                user_id,
                bot_type,
                engine_type,
                template_type,
                template_uid,
            )
        except Exception as e:
            resolved_template_config["template_uid"] = None
            resolved_template_config["_baas_template_uid_resolution_error"] = str(e)
            logger.warning(
                "[bot_service.template_uid] resolution failed: bot_id=%s user_id=%s "
                "bot_type=%s engine_type=%s template_type=%s error=%s",
                bot_id,
                user_id,
                bot_type,
                engine_type,
                template_type,
                e,
            )
        return resolved_template_config

    def _resolve_baas_restart_template_uuid(
        self,
        *,
        bot_id: str,
        user_id: str,
        bot: dict[str, Any],
        template_config: dict[str, Any] | None,
    ) -> str | None:
        """BaaS 原地重启前，按当前 bot 上下文解析 template_uuid。

        BaaS update 仍然会重建 create payload；这里显式传入 template_uuid，
        避免 BaasService 使用默认模板覆盖当前引擎应使用的模板。
        """
        template_resolver = getattr(self, "_baas_template_resolver", None)
        if template_resolver is None:
            return None

        env = get_current_env()
        if not isinstance(template_config, dict):
            template_config = None

        template = template_resolver.resolve_template(
            bot_id=bot_id,
            user_id=user_id,
            env=env,
            bot_type=str(bot.get("bot_type") or ""),
            engine_type=bot.get("active_engine"),
            template_type=bot.get("template_type"),
            template_config=template_config,
        )
        logger.info(
            "[bot_service.restart_template] resolved: bot_id=%s user_id=%s "
            "engine_type=%s template_type=%s template_uid=%s template_uuid=%s",
            bot_id,
            user_id,
            bot.get("active_engine"),
            bot.get("template_type"),
            template.template_uid,
            template.template_uuid,
        )
        return template.template_uuid

    def _resolve_restart_target_provider(
        self,
        *,
        bot_id: str,
        user_id: str,
        bot: dict[str, Any],
        source_provider: str | None,
    ) -> str | None:
        """Resolve an opt-in provider migration for an existing ARCA bot.

        The database-backed template override is a positive-match switch. Any
        absent, invalid, unmatched, or unreadable configuration preserves the
        source provider and therefore the pre-existing restart behavior.
        """
        if source_provider != ARCA_DEVICE_PROVIDER:
            return source_provider

        active_engine = normalize_engine_type(bot.get("active_engine"), default="")
        template_type = bot.get("template_type")
        engine_bucket = resolve_baas_engine_bucket(
            engine_type=active_engine,
            template_type=template_type,
        )
        if active_engine not in {"openclaw", "hermes", "claude_code"}:
            return source_provider
        if engine_bucket == AICODING_ENGINE_TYPE:
            return source_provider

        template_resolver = getattr(self, "_baas_template_resolver", None)
        if template_resolver is None:
            return source_provider

        owner_id = str(bot.get("owner_id") or user_id)
        try:
            override_uuid = template_resolver.resolve_template_override(
                env=get_current_env(),
                user_id=owner_id,
                bot_type=str(bot.get("bot_type") or "personal"),
            )
        except Exception as e:
            logger.warning(
                "[bot_service.restart_provider] template override lookup failed; "
                "preserving source provider: bot_id=%s owner_id=%s "
                "source_provider=%s error=%s",
                bot_id,
                owner_id,
                source_provider,
                e,
            )
            return source_provider

        if override_uuid is None:
            logger.info(
                "[bot_service.restart_provider] no template override hit; "
                "preserving source provider: bot_id=%s owner_id=%s "
                "source_provider=%s",
                bot_id,
                owner_id,
                source_provider,
            )
            return source_provider

        logger.info(
            "[bot_service.restart_provider] template override hit; migrating provider: "
            "bot_id=%s owner_id=%s source_provider=%s target_provider=%s "
            "template_uuid=%s active_engine=%s template_type=%s",
            bot_id,
            owner_id,
            source_provider,
            BAAS_DEVICE_PROVIDER,
            override_uuid,
            active_engine,
            template_type,
        )
        return BAAS_DEVICE_PROVIDER

    def _get_device_binding_repo(self):
        """Get the DeviceBindingRepository (injected via __init__)."""
        return self._device_binding_repo

    def _query_admin_worknos(self, bot_id: str, owner_id: str) -> list[str] | None:
        """Query admin collaborator worknos for a bot.

        Returns ``[]`` when the bot has no admins, and ``None`` only when the
        query itself fails. Callers downstream (passport updatePassport) treat
        ``[]`` as "clear admins" and ``None`` as "do not update", so the two
        cases must stay distinct.
        """
        try:
            env = get_current_env()
            collaborators = self._collaborator_repo.list_by_bot(
                bot_id=bot_id, owner_id=owner_id, env=env, role=CollaboratorRole.ADMIN,
            )
            return [c.user_id for c in collaborators]
        except Exception as e:
            logger.warning(
                "[bot_service._query_admin_worknos] Failed: bot_id=%s, owner_id=%s, error=%s",
                bot_id, owner_id, e,
            )
            return None

    def _list_bot_members(
        self, bot_id: str, owner_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        """List a bot's members (collaborators) for the appcoding-bots response.

        Reads the ``ac_bot_collaborator`` table (added admin/member
        collaborators) scoped to the current env. The bot owner is intentionally
        not synthesized as a member here: owner identity lives on the bot record
        itself (``owner_id`` / ``owner_name``); callers wanting the full roster
        should combine that with this list.

        Returns an empty list when there are no collaborators, when ``owner_id``
        is missing, or when the lookup itself fails — member enrichment must
        never break the coding-bots listing.

        Args:
            bot_id: Bot ID of the coding bot.
            owner_id: Bot owner's work number (filters the collaborator index).

        Returns:
            A list of member dicts with ``user_id`` and ``user_name`` only
            (operator_id / role / timestamps are not exposed on this public
            surface).
        """
        if not owner_id:
            return []
        try:
            env = get_current_env()
            collaborators = self._collaborator_repo.list_by_bot(
                bot_id=bot_id, owner_id=owner_id, env=env,
            )
            return [
                {"user_id": c.user_id, "user_name": c.user_name}
                for c in collaborators
            ]
        except Exception as e:
            logger.warning(
                "[bot_service._list_bot_members] Failed: bot_id=%s, owner_id=%s, error=%s",
                bot_id, owner_id, e,
            )
            return []

    def _try_acquire_restart_lock(
        self, env: str, entity_id: str, bot_id: str, holder_user_id: str
    ) -> Optional[BotRestartLockRecord]:
        """Try to acquire the per-bot restart idempotency lock.

        The lock is a row in ``ac_bot_restart_lock`` guarded by a UNIQUE
        constraint on ``(env, entity_id, bot_id)``:

        1. Attempt ``INSERT`` — success means we hold the lock.
        2. On conflict, if the existing row is stale (older than
           ``RESTART_LOCK_TTL_SECONDS``, judged on the DB clock) the holder is
           presumed crashed: delete the stale row and re-``INSERT``.
        3. Otherwise a restart is genuinely in progress → return ``None`` so
           the caller suppresses this duplicate.

        Returns the lock record on success, or ``None`` if a restart is already
        in progress for this bot.
        """
        rec = self._restart_lock_repo.acquire(env, entity_id, bot_id, holder_user_id)
        if rec is not None:
            return rec

        # A row existed at INSERT time. Since acquire/check/acquire is not
        # transactional, the row may since have (a) stayed held & fresh, (b)
        # become stale, or (c) been released by its holder. If it's stale, reap
        # it; then make a SECOND, authoritative acquire attempt that decides the
        # outcome:
        #   - reaped (b) or released (c)  -> key is now free -> acquire succeeds.
        #   - still held & fresh (a), or re-taken by another worker -> acquire
        #     fails -> None -> suppress this duplicate.
        # Crucially this also covers case (c): if the previous holder released
        # between our first acquire and the staleness check, get_if_stale returns
        # None (no row) yet the second acquire still picks the lock up — so a
        # legitimately-new restart is not dropped as a phantom duplicate.
        stale = self._restart_lock_repo.get_if_stale(
            env, entity_id, bot_id, RESTART_LOCK_TTL_SECONDS
        )
        if stale is not None:
            logger.warning(
                "[bot_service._try_acquire_restart_lock] Reaping stale restart lock: "
                "env=%s, entity_id=%s, bot_id=%s, created=%s",
                env, entity_id, bot_id, stale.gmt_create,
            )
            # Compare-and-delete on the stale row's token: if another worker
            # already reaped+reacquired, the token won't match and this is a
            # no-op — the acquire below then fails and we suppress, instead of
            # stealing their fresh lock.
            self._restart_lock_repo.release(env, entity_id, bot_id, stale.lock_token)

        return self._restart_lock_repo.acquire(env, entity_id, bot_id, holder_user_id)

    async def get_bot_by_ip_and_user(self, ip: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 IP 地址和 User ID 查询对应的 Bot 记录。

        查询逻辑：
        1. 查询 ac_entity_device_binding 表，entity_id=user_id, status=ACTIVE
        2. 匹配 device_props.device_locator 中的 IP 地址（如 "11.68.29.96:22"）
        3. 根据 binding_id 查询对应的 bot 记录

        Args:
            ip: IP 地址（如 "11.68.29.96"）
            user_id: 用户 ID

        Returns:
            Bot 记录字典，未找到返回 None
        """
        try:
            # Step 1: Query ACTIVE device bindings for the user (paginated)
            device_binding_repo = self._get_device_binding_repo()
            page = 1
            page_size = 100
            matching_binding = None

            while True:
                total, bindings = device_binding_repo.list_bindings(
                    entity_id=user_id,
                    entity_type="staff",
                    status="ACTIVE",
                    env=get_current_env(),
                    page=page,
                    page_size=page_size
                )

                if not bindings:
                    break

                # Step 2: Search for binding record matching the IP address in current page
                for binding in bindings:
                    device_props = binding.device_props or {}
                    device_locator = device_props.get("device_locator", "")

                    # device_locator format: "11.68.29.96:22" or just "11.68.29.96"
                    if device_locator:
                        # Extract IP part (remove port if present)
                        locator_ip = device_locator.split(":")[0] if ":" in device_locator else device_locator
                        if locator_ip == ip:
                            matching_binding = binding
                            logger.info(f"[get_bot_by_ip_and_user] Found matching binding: id={binding.id}, device_locator={device_locator}")
                            break

                if matching_binding:
                    break

                # Check if there are more pages
                if len(bindings) < page_size:
                    break
                page += 1

            if not matching_binding:
                logger.info(f"[get_bot_by_ip_and_user] No ACTIVE device bindings found for user {user_id} with IP {ip}")
                return None

            # Step 3: Query bot by binding_id
            bot = self._repository.get_by_binding_id(matching_binding.id)

            if bot:
                logger.info(f"[get_bot_by_ip_and_user] Found bot: bot_id={bot.get('bot_id')}, binding_id={matching_binding.id}")
            else:
                logger.info(f"[get_bot_by_ip_and_user] No bot found for binding_id={matching_binding.id}")

            return bot

        except Exception as e:
            logger.error(f"[get_bot_by_ip_and_user] Error querying bot by IP {ip} and user {user_id}: {e}")
            return None

    def is_first_bot(self, user_id: str) -> bool:
        """First bot iff the owner has zero bots (current env; tenant enforced by guard)."""
        return self._repository.count_by_owner(user_id) == 0

    def is_first_personal_bot(self, user_id: str) -> bool:
        """Return whether the owner has no live personal Bot in the current scope."""
        return not self._repository.exists_by_owner_and_bot_type(
            user_id, "personal"
        )

    def _check_bot_count_limit(self, owner_id: str) -> None:
        """Enforce the per-owner bot count limit.

        上限优先级：``ac_access_control_policy.policy.bots_ceiling`` > 0
        → ``device_allocation.max_devices_per_entity`` (default 5)。

        桌面 Bot（bot_type="desktop"）运行在用户本地 VM，不占用云端容器资源，
        因此不计入数量限制。

        Args:
            owner_id: 当前操作用户

        Raises:
            BotLimitExceededError: 已达到该用户允许的最大 Bot 数量
        """
        # 1. 尝试从 policy 表读取用户专属上限
        max_bots = self.get_bots_ceiling_for_owner(owner_id)

        if max_bots <= 0:
            return
        try:
            current_raw = self._repository.count_by_owner(
                owner_id, exclude_bot_type="desktop"
            )
            current = int(current_raw)
        except (TypeError, ValueError):
            return
        except Exception as e:
            logger.warning(
                f"[bot_service._check_bot_count_limit] count_by_owner failed for {owner_id}: {e}"
            )
            return
        logger.info(
            f"[bot_service._check_bot_count_limit] owner={owner_id} "
            f"cloud_bot_count={current} max={max_bots}"
        )
        if current >= max_bots:
            raise BotLimitExceededError(
                f"已达到 Bot 数量上限 ({max_bots})，当前 {current} 个。请删除部分 Bot 后再创建新的。"
            )

    def get_bots_ceiling_for_owner(self, owner_id: str) -> int:
        """获取用户的 BOT 数量上限，优先从 policy 表读取。

        Public because it is the single definition of the ceiling: creation
        enforces it here, and a surface that *reports* the quota must resolve it
        the same way. Reading ``PolicyService.get_bots_ceiling`` directly instead
        picks up that method's own hardcoded default (5) rather than the
        configured allocation limit, so the advertised and enforced ceilings can
        disagree whenever ``max_devices_per_entity`` is not 5.

        Priority:
        1. PolicyService.get_bots_ceiling (per-user, from DB)
        2. DeviceAllocationConfig.max_devices_per_entity (global config)
        3. 0 (if config is invalid — disables the check)
        """
        config_default = 0
        try:
            config_default = int(self._allocation_config.max_devices_per_entity)
        except Exception:
            pass

        if self._policy_service is None:
            return config_default

        try:
            return self._policy_service.get_bots_ceiling(
                entity_id=owner_id, default=config_default,
            )
        except Exception as e:
            logger.warning(
                "[bot_service.get_bots_ceiling_for_owner] "
                "policy_service.get_bots_ceiling failed for %s: %s",
                owner_id, e,
            )
            return config_default

    def check_create_bot_preflight(
        self,
        user_id: str,
        bot_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        bot_name: Optional[str] = None,
    ) -> None:
        """Validate whether a bot creation request can start external auth.

        The create flow may be two-phase: Passport auth is requested first, and
        the bot is persisted later from /auth-status. Quota checks that can be
        evaluated before Passport should run here so users are not sent through
        authorization only to fail during the second phase.

        ``bot_name`` (when known) is checked for uniqueness here for the same
        reason: ``create_bot`` rejects a duplicate, but only *after* the Passport
        application has already happened, leaving an identity behind with no bot
        and repeating that external side effect on every retry. The service-level
        check stays where it is — this one narrows the window, it does not close
        it, since another create can take the name in between.
        """
        self._check_bot_count_limit(user_id)
        if bot_name and bot_name.strip():
            if self._repository.get_by_bot_name(bot_name.strip()):
                raise BotNameExistsError(f"Bot name '{bot_name}' already exists")
        if bot_id is not None and engine_type is not None:
            self._validate_default_bot_engine(bot_id, engine_type)

    def _validate_default_bot_engine(self, bot_id: str, engine_type: str) -> None:
        """Reject Teclaw Cloud as the engine of the reserved Default Bot."""
        if bot_id == "default" and self.is_teclaw_bot(engine_type):
            raise DefaultBotTeclawNotAllowedError()

    def _check_device_limit(self, entity_id: str, entity_type: str, owner_id: str) -> None:
        """
        Check if device limit is reached for the entity based on user's bots.

        Counts devices that are actively bound to user's bots (ACTIVE/PENDING/FAILED),
        regardless of whether the bot is ACTIVE, PENDING, or FAILED.

        上限来源为 per-user ceiling（``get_bots_ceiling_for_owner``，优先读
        ``ac_access_control_policy.policy.bots_ceiling``，fallback 到
        ``device_allocation.max_devices_per_entity``），与 ``_check_bot_count_limit``
        同源，确保动态上限对绑定了 device 的 bot 同样生效。

        Args:
            entity_id: Entity ID
            entity_type: Entity type
            owner_id: Owner user ID (to find user's bots)

        Raises:
            DeviceLimitError: If device limit is reached
        """
        try:
            mode_str = self._allocation_config.mode
            max_devices = self.get_bots_ceiling_for_owner(owner_id)

            # ceiling 无效（<=0）时放行，与 _check_bot_count_limit 语义一致，
            # 避免 active_device_count >= 0 恒真导致第一个 bot 就被拦。
            if max_devices <= 0:
                return

            try:
                mode = DeviceAllocationMode(mode_str)
            except ValueError:
                mode = DeviceAllocationMode.MULTI

            # Only check limit for multi-device mode
            if mode != DeviceAllocationMode.MULTI:
                return

            # Get all bots for this owner
            total_bots, bots = self._repository.list_by_owner(
                owner_id=owner_id,
                page=1,
                page_size=1000,  # Get all bots
            )

            # Collect binding_ids from bots that have devices
            # 桌面 Bot 运行在用户本地 VM，不占用云端容器资源，因此不计入 device 限制。
            binding_ids = []
            bot_binding_map = {}  # binding_id -> bot info for logging
            skipped_desktop = 0
            for bot in bots:
                if bot.get("bot_type") == "desktop":
                    skipped_desktop += 1
                    continue
                binding_id = bot.get("binding_id")
                if binding_id:
                    binding_ids.append(binding_id)
                    bot_binding_map[binding_id] = {
                        "bot_id": bot.get("bot_id"),
                        "bot_status": bot.get("status"),
                    }
            if skipped_desktop > 0:
                logger.info(
                    f"[bot_service._check_device_limit] owner={owner_id} "
                    f"skipped {skipped_desktop} desktop bot(s) from device limit check"
                )

            if not binding_ids:
                logger.info(f"[bot_service._check_device_limit] No bound devices for owner {owner_id}, count=0/{max_devices}")
                return

            # Query device binding status for these binding_ids
            # Use device service to get binding details
            active_device_count = 0
            service = self._device_service_provider()

            for binding_id in binding_ids:
                try:
                    binding = service.get_device(binding_id=binding_id)
                    if binding and binding.status in [
                        DeviceBindingStatus.ACTIVE.value,
                        DeviceBindingStatus.PENDING.value,
                        DeviceBindingStatus.FAILED.value,
                        DeviceBindingStatus.STOPPED.value,
                    ]:
                        active_device_count += 1
                        bot_info = bot_binding_map.get(binding_id, {})
                        logger.debug(
                            f"[bot_service._check_device_limit] Counting device: "
                            f"binding_id={binding_id}, device_status={binding.status}, "
                            f"bot_id={bot_info.get('bot_id')}, bot_status={bot_info.get('bot_status')}"
                        )
                except Exception as e:
                    # If can't get device info, assume it counts (conservative)
                    logger.warning(f"[bot_service._check_device_limit] Failed to get device {binding_id}: {e}")
                    active_device_count += 1

            if active_device_count >= max_devices:
                raise DeviceLimitError(
                    f"已达到设备数量上限 ({max_devices})，当前关联设备数: {active_device_count}。"
                    f"请释放部分Bot后再创建新的Bot。"
                )

            logger.info(
                f"[bot_service._check_device_limit] Device count {active_device_count}/{max_devices} "
                f"for owner {owner_id} (entity {entity_type}/{entity_id})"
            )

        except DeviceLimitError:
            raise
        except Exception as e:
            logger.warning(f"[bot_service._check_device_limit] Failed to check device limit: {e}")
            # Don't block bot creation if check fails

    def _resolve_bot_name(
        self,
        bot_name: Optional[str],
        bot_id: str,
        user_id: str,
        nick_name: str
    ) -> str:
        """
        Resolve bot name according to the naming rules:
        - First bot (user has no existing bots): use nick_name (花名)
        - Non-first bot: if bot_name not provided, use bot_id

        Args:
            bot_name: User-provided bot name (optional)
            bot_id: Generated bot ID
            user_id: User ID
            nick_name: User nickname (花名)

        Returns:
            Resolved bot name
        """
        # If bot_name is explicitly provided, use it
        if bot_name is not None and bot_name.strip():
            return bot_name.strip()

        # Check if this is the first bot for the user
        is_first = self.is_first_bot(user_id)

        if is_first:
            # First bot: use nick_name (花名) as default
            resolved_name = nick_name.strip() if nick_name and nick_name.strip() else str(bot_id)
            logger.info(f"[bot_service._resolve_bot_name] First bot for user {user_id}, using nick_name: {resolved_name}")
            return resolved_name
        else:
            # Non-first bot: use bot_id as default
            logger.info(f"[bot_service._resolve_bot_name] Non-first bot for user {user_id}, using bot_id: {bot_id}")
            return str(bot_id)

    def create_bot(
        self,
        user_id: str,
        nick_name: str,
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        share_policy: Optional[Dict[str, Any]] = None,
        engine_type: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None,
        bot_id: Optional[str] = None,
        bot_type: Optional[str] = None,
        template_type: Optional[str] = None,
        template_config: Optional[Dict[str, Any]] = None,
        cookie: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new bot with async device allocation.

        Args:
            user_id: User ID (used as creator and for default entity)
            nick_name: User nickname for device allocation and first bot naming
            bot_name: Bot name (optional, defaults based on rules)
            bot_desc: Bot description (optional)
            entity_id: Entity ID (defaults to staff_{user_id})
            entity_type: Entity type (defaults to 'staff')
            share_policy: Sharing configuration (optional)
            engine_type: Active engine type (default: uses config default)
            cookie: User's session cookie for memoryos API authentication (optional)
            ext: Extension field dictionary (optional), e.g., {"avatar_url": "https://..."}
            bot_id: Bot ID (optional, used for Passport authorization continuation)
            bot_type: Bot type (default: "personal", can be "personal" or "service")
            template_type: Template type (optional, e.g., "applicationCoding")
            template_config: Template configuration dictionary (optional)

        Returns:
            Created bot record with PENDING status and binding info

        Raises:
            BotServiceError: If bot creation fails

        Note:
            Naming rules:
            - First bot (user has no bots): uses nick_name (花名) as default
            - Non-first bot: uses bot_id as default if bot_name not provided
        """
        # ===== 入参校验：bot_name 合法性（空/字符/长度） =====
        # 兼容老调用：bot_name 允许 None — None 时跳过显式校验，由 _resolve_bot_name
        # 走默认命名规则；只有传了非 None 才严格校验。
        if bot_name is not None:
            bot_name = validate_bot_name(bot_name)

        # ===== 幂等性检查：如果传入 bot_id，检查是否已存在 =====
        if bot_id is not None:
            existing_bot = self._repository.get_by_id_and_owner(bot_id, user_id)
            if existing_bot:
                # Bot 已存在，直接返回（幂等）
                logger.info(f"[bot_service.create_bot] Bot {bot_id} already exists for user {user_id}, returning existing bot")
                return existing_bot
            # 不存在，继续创建流程，使用传入的 bot_id
            logger.info(f"[bot_service.create_bot] Using provided bot_id={bot_id} for user {user_id}")
        else:
            # 未传入 bot_id，生成新的
            bot_id = generate_bot_id(user_id, self._repository)

        # Resolve active engine before creation preflight validation.
        resolved_active_engine = engine_type or DEFAULT_ENGINE_TYPE

        # ===== 创建前置校验：数量上限 + Default Bot 引擎约束 =====
        self.check_create_bot_preflight(
            user_id=user_id,
            bot_id=bot_id,
            engine_type=resolved_active_engine,
        )

        # Resolve entity info
        resolved_entity_id = entity_id or f"staff_{user_id}"
        resolved_entity_type = entity_type or "staff"

        # Always use backend configured engine types, ignore frontend input.
        # _get_engine_types() (ENGINE_TYPES env, falling back to the static list)
        # is what validation and switch_engine both use; persisting the static
        # list instead meant a bot created on a deployment-enabled engine (e.g.
        # teclaw) stored an enabled-engine list that omitted its own active
        # engine — so switch_engine would refuse to switch back to it.
        resolved_engine_types = _get_engine_types()

        # A bot's active engine must be a member of its own enabled-engine list —
        # switch_engine checks that list, so a row violating this can never
        # return to the engine it was created on. The invariant held by accident
        # while the static list was persisted (it contains DEFAULT_ENGINE_TYPE);
        # persisting the configured registry broke it wherever the two differ.
        #
        # Guaranteed by construction rather than by rejecting: teclaw is a
        # supported engine that is absent from the default registry, so
        # rejecting would break teclaw creation on any deployment that does not
        # set ENGINE_TYPES. Whether an engine outside the configured registry
        # should be creatable at all is a separate question, decided by the
        # callers that validate it — not something to enforce here by writing a
        # row that contradicts itself.
        if resolved_active_engine not in resolved_engine_types:
            resolved_engine_types = [*resolved_engine_types, resolved_active_engine]
        resolved_bot_type = bot_type or "personal"

        if resolved_bot_type == "service" and not self.is_teclaw_bot(
            resolved_active_engine
        ):
            # The CommonConfig record is the master switch. Missing, disabled,
            # or lacking a valid image preserves the pre-feature behavior and
            # removes any caller-supplied image-policy fields.
            if self._service_bot_image_policy_enabled():
                ext = apply_default_image_to_ext(ext)
            else:
                ext = clear_image_policy_from_ext(ext)

        # Resolve bot name according to naming rules
        resolved_bot_name = self._resolve_bot_name(bot_name, bot_id, user_id, nick_name)

        # Check if bot_name already exists globally
        if bot_name and bot_name.strip():
            if self._repository.exists_by_bot_name(bot_name.strip()):
                raise BotNameExistsError(f"Bot name '{bot_name}' already exists")

        # Check device limit before creating bot (based on user's existing bots)
        self._check_device_limit(resolved_entity_id, resolved_entity_type, user_id)

        logger.info(f"[bot_service.create_bot] Creating bot {bot_id} for user {user_id}, "
                   f"entity={resolved_entity_type}/{resolved_entity_id}, engines={resolved_engine_types}, "
                   f"bot_name={resolved_bot_name}")

        try:
            # Step 1: Create bot record with PENDING status (binding_id is None initially)
            bot_data = {
                "bot_id": bot_id,
                "bot_name": resolved_bot_name,
                "bot_desc": bot_desc,
                "entity_id": resolved_entity_id,
                "entity_type": resolved_entity_type,
                "creator_id": user_id,
                "owner_id": user_id,
                "owner_name": nick_name,  # Set owner_name to creator's nick_name
                "modifier_id": user_id,
                "engine_types": resolved_engine_types,
                "active_engine": resolved_active_engine,
                "status": "PENDING",  # Initial status
                "binding_id": None,
                "device_id": None,
                "share_policy": share_policy,
                "is_delete": 0,
                "ext": ext,
                "bot_type": resolved_bot_type,
                "template_type": template_type,  # Template type (e.g., "applicationCoding")
            }

            bot_record = self._repository.insert(bot_data)
            logger.info(f"[bot_service.create_bot] Bot {bot_id} created with PENDING status")

            # Step 1.5: Create template record if template_config is provided
            if template_config and template_type:
                try:
                    logger.info(
                        "[bot_service.create_bot] Creating template for bot %s, template_type=%s",
                        bot_id, template_type,
                    )

                    # For applicationCoding template, ensure DIMA workspace exists
                    if template_type == "applicationCoding":
                        # Create DIMA workspace and update template_config with dima_space_id
                        workspace_id = self._require_workspace_hosting().create_workspace_for_bot(
                            staff_id=user_id,
                            bot_id=bot_id,
                            bot_name=resolved_bot_name,
                            template_config=template_config,
                        )

                        if workspace_id:
                            logger.info(f"[bot_service.create_bot] Created DIMA workspace {workspace_id} for bot {bot_id}")
                        else:
                            logger.warning(f"[bot_service.create_bot] Failed to create DIMA workspace for bot {bot_id}, continuing without it")

                    self._template_service.create_template(
                        bot_id=bot_id,
                        template_config=template_config,
                        template_type=template_type,
                        active_engine=resolved_active_engine,
                    )
                    logger.info(f"[bot_service.create_bot] Template created for bot {bot_id}")
                except Exception as e:
                    logger.error(f"[bot_service.create_bot] Failed to create template for bot {bot_id}: {e}", exc_info=True)
                    # Don't fail bot creation if template creation fails
                    # Just log the error

            # 桌面 bot 的设备分配走 BaaS 流程（DesktopBotService._execute_creation），
            # 不应走 DeviceService.apply_device()（会生成 staff_xxx 格式 device_id）。
            if resolved_bot_type == "desktop":
                logger.info(
                    f"[bot_service.create_bot] Bot {bot_id} is desktop type, "
                    f"skipping DeviceService.apply_device (uses BaaS allocation)"
                )
                return bot_record

            # Step 2: 设备分配（错误立即透出给前端）。teclaw bot 走 BaaS 即时备容器
            # (create + approve)，不经 DeviceService.apply_device()；其余引擎走 apply_device。
            # 两条路径汇合到下方共享尾部（更新 bot_record + 注册 BCN + service 发布单创建）。
            # 注：teclaw service bot 的 source/草稿 容器在此即时备好；其 verify/online
            # 容器仍由发布流程（按 device_provider）单独备好。
            # teclaw 即时备容器时投递的初始 artifact —— 用于在下方创建草稿发布单后
            # 回填其 ext.config_artifact（仅 teclaw service bot）。
            teclaw_config_artifact: dict | None = None
            try:
                teclaw_provision = self._teclaw_provision_provider()
                if teclaw_provision.is_teclaw(resolved_active_engine):
                    logger.info(
                        f"[bot_service.create_bot] Bot {bot_id} is teclaw, provisioning "
                        f"container via BaaS (skipping DeviceService.apply_device)"
                    )
                    # The passport token is fetched and pushed by the create
                    # publish poll task once BaaS reports the container started —
                    # the PaaS device it is written onto does not exist yet here.
                    provision = teclaw_provision.provision(
                        bot=bot_record,
                        owner_id=user_id,
                    )
                    binding_id = provision.binding_id
                    device_id = provision.device_id
                    final_status = provision.status
                    teclaw_config_artifact = provision.config_artifact
                else:
                    service = self._device_service_provider()
                    operator = _compose_operator_context(user_id, nick_name)

                    # DRM: 判断新建 bot 是否走 NAS
                    force_nas = self._is_new_bot_use_nas()

                    # 生成软链配置
                    symlink_mappings: List[SynlinkMappingInfo] = []
                    try:
                        skill_set_service = self._skill_set_factory.create(
                            entity_id=resolved_entity_id,
                            entity_type=resolved_entity_type,
                            bot_id=str(bot_id),
                            engine_type=resolved_active_engine,
                        )
                        symlink_mappings = skill_set_service.get_symlink_mappings(
                            user_id=resolved_entity_id,
                            bolt_id=str(bot_id)
                        )
                        logger.info(f"[bot_service.create_bot] Generated symlink_mappings: {len(symlink_mappings)}")
                    except Exception as e:
                        logger.warning(f"[bot_service.create_bot] Failed to get symlink_mappings: {e}")

                    # Engine strategy 场景：按具体引擎策略传入额外环境变量。
                    extra_envs = None
                    # 路由到具体 provider 前，先把 template_uid 上下文带给 device 层。
                    # 解析失败先记下来；只有后续真正走 BaaS 时才需要 fail-fast。
                    device_template_config = self._attach_template_uid_context(
                        bot_id=str(bot_id),
                        user_id=user_id,
                        bot_type=resolved_bot_type,
                        engine_type=resolved_active_engine,
                        template_type=template_type,
                        template_config=template_config,
                    )
                    device_template_config = overlay_image_pin_on_template_config(
                        device_template_config,
                        bot_record.get("ext"),
                    )
                    extra_envs = self._build_engine_extra_envs(
                        bot_id=str(bot_id),
                        owner_id=user_id,
                        active_engine=resolved_active_engine,
                        bot_type=resolved_bot_type,
                        template_type=template_type,
                        template_config=template_config,
                        log_context="bot_service.create_bot",
                    )

                    device_result = service.apply_device(
                        apply_reason=f"Create bot: {resolved_bot_name or bot_id}",
                        entity_id=resolved_entity_id,
                        entity_type=resolved_entity_type,
                        operator=operator,
                        bot_id=str(bot_id),
                        engine=resolved_active_engine,
                        bot_type=resolved_bot_type,
                        owner_id=user_id,  # 创建时，owner_id 是当前登录用户
                        symbol=symlink_mappings,
                        force_nas=force_nas,
                        extra_envs=extra_envs,
                        admins=None,  # 新建 bot 还未添加协作者
                        template_type=template_type,
                        template_config=device_template_config,
                    )

                    if not device_result:
                        raise DeviceAllocationError("设备申请返回空结果")

                    binding_id = device_result.id
                    device_id = device_result.device_id
                    device_status = device_result.status

                    # 更新 bot 记录（绑定设备）
                    if device_status == DeviceBindingStatus.ACTIVE.value:
                        final_status = "ACTIVE"
                    else:
                        final_status = "PENDING"  # DaaS 设备等待回调

                self._repository.update_by_owner(bot_id, user_id, {
                    "binding_id": binding_id,
                    "device_id": device_id,
                })
                logger.info(f"[bot_service.create_bot] Bot {bot_id} device allocated: "
                           f"binding_id={binding_id}, status={final_status}")

                bot_record["binding_id"] = binding_id
                bot_record["device_id"] = device_id
                bot_record["status"] = final_status
                bot_record["engine_types"] = resolved_engine_types

                # 创建时注册 BCN Provider（与 start_bot 条件一致）
                # 触发条件:
                #   - active_engine == "claude_code" 且 template_type == "normalCC"
                #   - active_engine == "claude_code" 且 template_type == "personalCoding"
                #   - active_engine == "aicoding" 且 template_type == "personalCoding"
                #   - active_engine == "teclaw" (所有 bot_type)
                #   - active_engine == "openclaw" 且 bot_type == "service"
                # 排查日志关键字: [bot_service.create_bot] register bot to BCN as provider
                should_register_bcn = self._should_register_bcn_provider(
                    active_engine=resolved_active_engine,
                    bot_type=resolved_bot_type,
                    template_type=template_type,
                    template_config=template_config,
                )
                if should_register_bcn:
                    logger.info(
                        f"[bot_service.create_bot] register bot to BCN as provider: "
                        f"bot_id={bot_id} active_engine={resolved_active_engine} "
                        f"bot_type={resolved_bot_type} template_type={template_type}"
                    )
                    try:
                        self._register_bot_to_bcn_as_provider(
                            bot_id=bot_id,
                            user_id=user_id,
                            owner_workno=user_id,
                            bot_name=resolved_bot_name or bot_id,
                            bot_summary=bot_desc or "",
                        )
                    except Exception as e:
                        logger.warning(
                            f"[bot_service.create_bot] BCN provider registration failed for bot {bot_id}, "
                            f"will retry on next start: {e}"
                        )
                else:
                    logger.info(
                        f"[bot_service.create_bot] skip BCN provider registration: "
                        f"bot_id={bot_id} active_engine={resolved_active_engine} "
                        f"bot_type={resolved_bot_type} template_type={template_type}"
                    )

                # 如果是服务型 bot，创建发布单
                if resolved_bot_type == "service":
                    try:
                        publish_service = self._bot_publish_provider()

                        publish_record = publish_service.create_publish(
                            source_bot_pk=bot_record["id"],
                            source_bot_id=bot_id,
                            publish_bot_id=bot_id,
                            name=resolved_bot_name,
                            owner_id=user_id,
                            permission_owner="owner",
                            description=bot_desc,
                            owner_name=nick_name,
                        )
                        # 将发布单信息保存到 bot_record 中
                        bot_record["publish"] = publish_record.to_dict()
                        logger.info(f"[bot_service.create_bot] Created publish record for service bot {bot_id}")

                    except Exception as e:
                        logger.error(f"[bot_service.create_bot] Failed to create publish record for service bot {bot_id}: {e}")

                    # teclaw service bot：把即时备容器时投递的初始 artifact 回填到
                    # 刚创建的草稿发布单 ext.config_artifact，使草稿期也有当前配置的
                    # 持久记录。独立 try（不与建单混淆日志），best-effort 不影响建 bot。
                    # 草稿期 publish_bot_id == bot_id，故 record_draft_artifact 按
                    # publish_bot_id 即可定位该草稿行。
                    if teclaw_config_artifact is not None:
                        try:
                            publish_service.record_draft_artifact(
                                bot_id=bot_id,
                                artifact=teclaw_config_artifact,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[bot_service.create_bot] draft artifact record "
                                f"failed for bot {bot_id}: {e}"
                            )

                # Attach template_config to bot_record if template was created
                if template_config and template_type:
                    bot_record["template_config"] = template_config

                # applicationCoding 保留原 memory 初始化；新通用 CC / 架构师 Bot
                # 的业务知识库、Wiki、RepoWiki 复用同一链路，但从
                # template_config resolved 快照检测配置来源。
                if self._should_trigger_memory_initialization(
                    active_engine=resolved_active_engine,
                    template_type=template_type,
                    template_config=template_config,
                    on_create=True,
                ):
                    logger.info(
                        f"[bot_service.create_bot] Triggering memory initialization for "
                        f"template-backed bot {bot_id}, template_type={template_type}"
                    )
                    from agentclaw.community.core.bot_management.utils import trigger_memory_initialization

                    trigger_memory_initialization(
                        bot_id=bot_id,
                        bot_name=resolved_bot_name,
                        user_id=user_id,
                        template_config=template_config,
                        cookie=cookie or "",
                        aixcore_base_url=self._workspace_hosting_config.aixcore_base_url,
                        aixcore_base_url_pre=self._workspace_hosting_config.aixcore_base_url_pre,
                    )
                    logger.info(
                        f"[bot_service.create_bot] Memory initialization completed for bot {bot_id}"
                    )

                return bot_record
            except (ResourceInsufficientError, DeviceAllocateError, DeviceLimitExceededError) as e:
                # 设备申请失败，删除 bot 记录并立即抛出错误（default bot 除外）
                logger.error(f"[bot_service.create_bot] Device allocation failed for bot {bot_id}: {e}")
                if bot_id != "default":
                    self._repository.soft_delete_by_owner(bot_id, user_id)
                raise BotServiceError(f"设备申请失败: {e}")
            except Exception as e:
                # 其他异常（default bot 除外，不删除）
                logger.exception(f"[bot_service.create_bot] Unexpected error during device allocation for bot {bot_id}: {e}")
                if bot_id != "default":
                    self._repository.soft_delete_by_owner(bot_id, user_id)
                raise BotServiceError(f"设备申请失败: {e}")

        except BotServiceError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.create_bot] Failed to create bot record: {e}")
            raise BotServiceError(f"Failed to create bot record: {e}")

    def _allocate_device_async(
        self,
        bot_id: str,
        user_id: str,
        nick_name: str,
        entity_id: str,
        entity_type: str,
        engine_types: list[str],
        bot_name: Optional[str] = None,
        active_engine: str = DEFAULT_ENGINE_TYPE,
        owner_id: Optional[str] = None,
        force_nas: bool = False,
        device_provider: Optional[str] = None,
        restart_lock_key: Optional[Tuple[str, str, str, str]] = None,
        bot_ext_override: Optional[Dict[str, Any]] = None,
    ):
        """
        Allocate device asynchronously in background thread.

        This runs the device allocation in a separate thread to avoid blocking
        the HTTP response. Updates bot status and binding_id based on result.
        Device binding is stored in ac_entity_device_binding table (reused from device service).

        Args:
            restart_lock_key: When set (restart flow), a ``(env, entity_id,
                bot_id, lock_token)`` tuple identifying the restart idempotency
                lock. The background thread owns releasing it: the row is
                compare-and-deleted (by token) in the ``finally`` once
                allocation finishes (success or failure), so a row another
                holder acquired after a reap is never deleted. When ``None``
                (create/start flows) no lock is touched.
            device_provider: Explicit device_provider fact to preserve for
                existing bots during restart. ``None`` means this is a new
                create/start allocation and the router may use creation rollout.
        """
        def do_allocate():
            try:
                logger.info(f"[bot_service._allocate_device_async] Starting device allocation for bot {bot_id}")

                # 桌面 bot 的设备分配走 BaaS 流程（device_id = BOT-xxx），
                # 不应走 DeviceService.apply_device()（会生成 staff_xxx 格式 device_id）。
                bot_record = self._repository.get_by_id_and_owner(bot_id, user_id)
                if bot_record and bot_record.get("bot_type") == "desktop":
                    logger.warning(
                        f"[bot_service._allocate_device_async] Skipping bot {bot_id}: "
                        f"desktop bots use BaaS device allocation, not DeviceService"
                    )
                    return

                # Get device service instance
                service = self._device_service_provider()

                # Build operator context
                operator = _compose_operator_context(user_id, nick_name)

                # 判断重启 bot 是否走 NAS：
                # 1. 上层显式指定 force_nas=True（如迁移服务）→ 直接走 NAS
                # 2. 迁移名单中 storage_status == "nas" → 走 NAS
                # 3. DRM 开关 is_new_bot_use_nas() == True → 走 NAS
                resolved_force_nas = force_nas
                if not resolved_force_nas:
                    try:
                        record = self._oss_record_repo.get_record(entity_id, str(bot_id))
                        if record and record.get("storage_status") == "nas":
                            resolved_force_nas = True
                            logger.info(f"[bot_service._allocate_device_async] Bot {bot_id} in NAS migration list, force_nas=True")
                    except Exception as e:
                        logger.info(f"[bot_service._allocate_device_async] Migration record check skipped: {e}")

                if not resolved_force_nas:
                    try:
                        resolved_force_nas = self._is_new_bot_use_nas()
                        logger.info(f"[bot_service._allocate_device_async] DRM is_new_bot_use_nas={resolved_force_nas}")
                    except Exception as e:
                        logger.warning(f"[bot_service._allocate_device_async] DRM check failed, fallback force_nas=False: {e}")

                # 生成软链配置
                symlink_mappings: List[SynlinkMappingInfo] = []
                try:
                    skill_set_service = self._skill_set_factory.create(
                        entity_id=entity_id,
                        entity_type=entity_type,
                        bot_id=str(bot_id),
                        engine_type=active_engine if active_engine == "claude_code" else None,
                    )
                    symlink_mappings = skill_set_service.get_symlink_mappings(
                        user_id=entity_id,
                        bolt_id=str(bot_id)
                    )
                    logger.info(f"[bot_service._allocate_device_async] Generated symlink_mappings: {len(symlink_mappings)}")
                except Exception as e:
                    logger.warning(f"[bot_service._allocate_device_async] Failed to get symlink_mappings: {e}")

                # Read template_config from ac_templates (not ac_bots).
                # bot_record comes from ac_bots which has no template_config column;
                # the value lives in ac_templates.ext.  Fetching it here ensures
                # restart / start-bot paths use the saved overrides (image,
                # command, envs, resource_spec) instead of losing them.
                bot_template_type = bot_record.get("template_type") if bot_record else None
                # Get bot_type from bot_record before building provisioning context.
                resolved_bot_type = bot_record.get("bot_type", "") if bot_record else ""
                resolved_template_config = None
                try:
                    resolved_template_config = self._template_service.get_template_config(bot_id)
                except Exception as e:
                    logger.warning(
                        "[bot_service._allocate_device_async] Failed to get template for bot %s: %s",
                        bot_id, e,
                    )

                # Engine strategy 重启场景：传入额外的环境变量。
                extra_envs = self._build_engine_extra_envs(
                    bot_id=str(bot_id),
                    owner_id=owner_id or user_id,
                    active_engine=active_engine,
                    bot_type=resolved_bot_type,
                    template_type=bot_template_type,
                    template_config=resolved_template_config,
                    log_context="bot_service._allocate_device_async",
                )

                # Call device service to allocate device
                # This creates a record in ac_entity_device_binding table
                resolved_owner_id = owner_id or user_id
                admins = self._query_admin_worknos(bot_id=str(bot_id), owner_id=resolved_owner_id)
                # 路由到具体 provider 前，先把 template_uid 上下文带给 device 层。
                # 解析失败先记下来；只有后续真正走 BaaS 时才需要 fail-fast。
                device_template_config = self._attach_template_uid_context(
                    bot_id=str(bot_id),
                    user_id=user_id,
                    bot_type=resolved_bot_type,
                    engine_type=active_engine,
                    template_type=bot_template_type,
                    template_config=resolved_template_config,
                )
                effective_bot_ext = (
                    bot_ext_override
                    if bot_ext_override is not None
                    else (bot_record.get("ext") if bot_record else None)
                )
                device_template_config = overlay_image_pin_on_template_config(
                    device_template_config,
                    effective_bot_ext,
                )
                logger.info(
                    f"[bot_service._allocate_device_async] allocation requested: "
                    f"bot_id={bot_id}, user_id={user_id}, entity_id={entity_id}, "
                    f"active_engine={active_engine}, bot_type={resolved_bot_type}, "
                    f"explicit_device_provider={device_provider or '<empty>'}, "
                    f"force_nas={resolved_force_nas}"
                )
                apply_kwargs = {
                    "apply_reason": f"Create bot: {bot_name or bot_id}",
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                    "operator": operator,
                    "bot_id": str(bot_id),
                    "engine": active_engine,
                    "bot_type": resolved_bot_type,
                    "owner_id": resolved_owner_id,  # 重启时 owner_id 从原 bot 表获取
                    "symbol": symlink_mappings,
                    "force_nas": resolved_force_nas,
                    "extra_envs": extra_envs,
                    "admins": admins,
                    "template_type": bot_template_type,
                    "template_config": device_template_config,
                }
                # Restart passes the provider that created the previous
                # binding. New create/start calls leave it empty so the router
                # can still apply the create-time rollout policy.
                if device_provider is not None:
                    apply_kwargs["device_provider"] = device_provider
                if bot_ext_override is not None:
                    apply_kwargs["device_props_extra"] = {
                        IMAGE_POLICY_ON_ACTIVE_KEY: DEFAULT_IMAGE_POLICY_VALUE
                    }

                device_result = service.apply_device(**apply_kwargs)

                if not device_result:
                    raise DeviceAllocationError("Device allocation returned empty result")

                binding_id = device_result.id
                device_id = device_result.device_id
                allocated_device_provider = device_result.device_provider
                device_status = device_result.status

                if not binding_id:
                    raise DeviceAllocationError("Device allocation did not return binding_id")

                logger.info(f"[bot_service._allocate_device_async] Device allocated for bot {bot_id}: "
                           f"binding_id={binding_id}, device_id={device_id}, provider={allocated_device_provider}, status={device_status}")

                # Map device status to bot status
                # Device status can be: PENDING, ACTIVE, RELEASED
                if device_status == DeviceBindingStatus.ACTIVE.value:
                    final_status = "ACTIVE"
                elif device_status == DeviceBindingStatus.PENDING.value:
                    final_status = "PENDING"
                else:
                    final_status = "FAILED"

                # Update bot with binding_id, device_id and final status
                # Use update_by_owner to ensure we only update the owner's bot
                bot_update = {
                    "binding_id": binding_id,
                    "device_id": device_id,
                    "status": final_status,
                }
                updated = self._repository.update_by_owner(bot_id, user_id, bot_update)
                if not updated:
                    logger.error(f"[bot_service._allocate_device_async] Failed to update bot {bot_id} for user {user_id}: bot not found or not owner")
                    return
                if (
                    bot_ext_override is not None
                    and device_status == DeviceBindingStatus.ACTIVE.value
                ):
                    self._persist_service_bot_default_image(
                        {**(bot_record or {}), "bot_id": bot_id, "ext": bot_ext_override},
                        user_id=user_id,
                    )

                logger.info(f"[bot_service._allocate_device_async] Bot {bot_id} updated with binding_id={binding_id}, status={final_status}")

            except (DeviceNotFoundError, InvalidDeviceStatusError, DeviceLimitExceededError) as e:
                logger.error(f"[bot_service._allocate_device_async] Device allocation failed for bot {bot_id}: {e}")
                # Update bot status to FAILED (use update_by_owner to ensure correct ownership)
                self._repository.update_by_owner(bot_id, user_id, {"status": "FAILED"})

            except Exception as e:
                logger.error(f"[bot_service._allocate_device_async] Unexpected error for bot {bot_id}: {e}")
                # Update bot status to FAILED (use update_by_owner to ensure correct ownership)
                self._repository.update_by_owner(bot_id, user_id, {"status": "FAILED"})

            finally:
                # Release the restart idempotency lock once allocation finishes,
                # on every path (success, desktop early-return, error). This is
                # the authoritative release point — the lock is held across the
                # whole ~2-min allocation so concurrent restart clicks are
                # suppressed until it completes. No-op for non-restart flows.
                if restart_lock_key is not None:
                    try:
                        self._restart_lock_repo.release(*restart_lock_key)
                        logger.info(
                            "[bot_service._allocate_device_async] Released restart lock for bot %s: %s",
                            bot_id, restart_lock_key,
                        )
                    except Exception as release_err:
                        # A failed release is non-fatal: the TTL reaper will
                        # reclaim the row on the next restart attempt.
                        logger.error(
                            "[bot_service._allocate_device_async] Failed to release restart lock for bot %s (%s): %s",
                            bot_id, restart_lock_key, release_err,
                        )

        # Start device allocation in background thread
        thread = threading.Thread(
            target=bind_current_avernet_tenant(do_allocate), daemon=True
        )
        thread.start()
        logger.info(f"[bot_service._allocate_device_async] Started background thread for bot {bot_id} device allocation")

    def get_bot_by_id(self, bot_id: str) -> Dict[str, Any]:
        """Resolve a bot by id alone. **Decides nothing about who may reach it.**

        The counterpart to :meth:`get_bot` for the one case that genuinely
        cannot use it: a caller who may reach a bot *without owning it*, and so
        cannot supply the owner the owner-scoped read needs. It exists to answer
        "which bot, and whose" — the authority question is the caller's to ask
        afterwards, against the resolved owner and primary key this returns.

        **A caller that stops here has performed no check at all.** Every caller
        must follow it with an adjudication (``core/engine_runtime/gate.py``'s
        ``require_bot_operator``) and must raise the same
        :class:`BotNotFoundError` on refusal, so a caller who may not reach the
        bot cannot tell it apart from one that does not exist.

        Ambiguity fails closed rather than picking a row: ``bot_id`` is not
        unique across owners for legacy ``default`` bots, so
        ``get_unique_by_id`` raises rather than resolving one caller's bot for
        another's request.

        Raises:
            BotNotFoundError: no live bot has this id in this env and tenant.
        """
        bot = self._repository.get_unique_by_id(bot_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        return bot

    def list_bots_reachable_by_id(
        self, bot_id: str, caller_id: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Live bots with this id the caller owns or collaborates on.

        The tie-breaker for :meth:`get_bot_by_id`'s fail-closed ambiguity. That
        method has to refuse a duplicated ``bot_id`` because it has no way to
        know which one is meant; asking the question inside the caller's own
        reach usually answers it, because the other owners' same-named bots
        were never candidates for this caller in the first place.

        **Decides nothing**, exactly like its sibling. Reachability here is
        collaboration at any level, which is below the operator bar, so a caller
        must still adjudicate every candidate this returns. It narrows the
        field; it does not confer anything.

        ``limit`` is required rather than defaulted: the rows are unordered and
        the adjudication happens after, so a caller that truncates and then
        answers can drop the one bot it was looking for. Making the bound an
        explicit decision at the call site is what keeps that from being an
        accident.
        """
        return self._repository.list_reachable_by_bot_id(bot_id, caller_id, limit)

    def get_bot(self, bot_id: str, user_id: str) -> Dict[str, Any]:
        """
        Get bot by ID.

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check (must be the owner)

        Returns:
            Bot record

        Raises:
            BotNotFoundError: If bot not found
        """
        # Use get_by_id_and_owner to ensure we only get the owner's bot
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        # Also fetch binding info from ac_entity_device_binding if exists
        binding_id = bot.get("binding_id")
        if binding_id:
            try:
                service = self._device_service_provider()
                binding = service.get_device(binding_id=binding_id)
                if binding:
                    bot["device_binding"] = binding.to_dict()
            except Exception as e:
                logger.warning(f"[bot_service.get_bot] Failed to get device binding {binding_id}: {e}")

            # NOTE: teclaw bots no longer read status through to baas here — the
            # TeclawPublishTaskHandler persists the resolved status onto the stored
            # column post-provision, so the bot row is authoritative for all
            # engines (see the durable Teclaw publish task lifecycle).

        # Fetch template info if exists
        try:
            template = self._template_service.get_template(bot_id)
            if template:
                bot["template_config"] = template.get("ext")
        except Exception as e:
            logger.warning(f"[bot_service.get_bot] Failed to get template for bot {bot_id}: {e}", exc_info=True)

        return bot

    def list_coding_bots_by_architect(self, architect_bot_id: str) -> List[Dict[str, Any]]:
        """List application coding bots associated with a domain architect bot.

        A domain architect bot is identified by ac_bots.ext.is_domain_bot == true.
        Application coding bots are linked to the architect bot via
        ac_templates.ext.architect_bot_id.

        Args:
            architect_bot_id: The architect bot's bot_id

        Returns:
            List of bot records that are application coding bots
            associated with the given architect bot
        """
        templates = self._template_service.list_templates_by_architect_bot_id(architect_bot_id)
        if not templates:
            return []

        # Collect bot_ids from templates and fetch bot records
        # Build a mapping from bot_id to template ext (template_config)
        template_ext_map = {}
        for template in templates:
            bot_id = template.get("bot_id")
            if bot_id:
                template_ext_map[bot_id] = template.get("ext")

        coding_bots = []
        for template in templates:
            bot_id = template.get("bot_id")
            if not bot_id:
                continue
            try:
                # list_by_conditions with bot_id returns (total, [items])
                total, items = self._repository.list_by_conditions(bot_id=bot_id, page=1, page_size=1)
                if items:
                    bot = items[0]
                    # Attach template_config (ext field from ac_templates) to bot record
                    ext = template_ext_map.get(bot_id)
                    if ext is not None:
                        bot["template_config"] = ext
                    # Attach bot member (collaborator) information. The owner
                    # is intentionally NOT included here: it lives on the bot
                    # record itself (owner_id / owner_name); only added admin/
                    # member collaborators are returned. Failure is non-fatal:
                    # an empty list is attached so enrichment never breaks the
                    # coding-bots list.
                    bot["members"] = self._list_bot_members(
                        bot_id=bot_id,
                        owner_id=bot.get("owner_id"),
                    )
                    coding_bots.append(bot)
            except Exception as e:
                logger.warning(
                    "[bot_service.list_coding_bots_by_architect] Failed to get bot %s: %s",
                    bot_id, e,
                )
                continue

        return coding_bots

    def _attach_template_configs_to_bots(self, items: List[Dict[str, Any]]) -> None:
        """Attach ac_templates.ext as template_config for bot list items in batch.

        create/get/update paths store template details in ac_templates.ext while
        list repository methods read only ac_bots fields.  Keep list APIs
        consistent with get_bot() by enriching template-backed bots here.
        Best-effort: template lookup failure must not break bot lists.
        """
        if not items:
            return

        bot_ids = list(dict.fromkeys(
            str(bot.get("bot_id"))
            for bot in items
            if bot.get("bot_id") and bot.get("template_type")
        ))
        if not bot_ids:
            return

        try:
            templates = self._template_service.list_templates_by_bot_ids(bot_ids)
            ext_by_bot_id = {
                str(template.get("bot_id")): template.get("ext")
                for template in templates
                if template.get("bot_id") and template.get("ext") is not None
            }
            if not ext_by_bot_id:
                return
            for bot in items:
                bot_id = bot.get("bot_id")
                if bot_id is None:
                    continue
                ext = ext_by_bot_id.get(str(bot_id))
                if ext is not None:
                    bot["template_config"] = ext
        except Exception as e:
            logger.warning(
                "[bot_service._attach_template_configs_to_bots] Failed to attach template configs: %s",
                e, exc_info=True,
            )

    def list_bots(
        self,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        List bots with pagination.

        Args:
            entity_id: Filter by entity ID (optional)
            entity_type: Filter by entity type (optional)
            page: Page number (1-based)
            page_size: Items per page

        Returns:
            Dictionary with 'total' and 'items' keys
        """
        total, items = self._repository.list_by_entity(
            entity_id=entity_id,
            entity_type=entity_type,
            page=page,
            page_size=page_size,
        )

        # Note: Device binding info is stored in ac_entity_device_binding table
        # To avoid N+1 queries, we don't fetch binding info for list operations
        # Use get_bot() to get detailed info including device_binding
        self._attach_template_configs_to_bots(items)

        return {
            "total": total,
            "items": items,
        }

    def list_bots_by_conditions(
        self,
        public: Optional[str] = None,
        bot_name: Optional[str] = None,
        owner_name: Optional[str] = None,
        bot_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        engine: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        bot_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        List bots by conditions with pagination.

        Args:
            public: Filter by public status ("0" or "1")
            bot_name: Filter by bot name (fuzzy search)
            owner_name: Filter by owner name
            bot_id: Filter by bot ID (exact match)
            owner_id: Filter by owner id (exact match) — scopes to one owner
            engine: Filter by active engine (exact match)
            status: Filter by lifecycle status (exact match)
            page: Page number (1-based)
            page_size: Items per page
            bot_ids: Restrict to this explicit set. ``None`` means unrestricted;
                an empty list means none. The distinction is load-bearing —
                treating empty as unrestricted would show a caller entitled to
                nothing everything.

        Returns:
            Dictionary with 'total' and 'items' keys
        """
        total, items = self._repository.list_by_conditions(
            public=public,
            bot_name=bot_name,
            owner_name=owner_name,
            bot_id=bot_id,
            owner_id=owner_id,
            engine=engine,
            status=status,
            page=page,
            page_size=page_size,
            bot_ids=bot_ids,
        )
        self._attach_template_configs_to_bots(items)
        return {
            "total": total,
            "items": items,
        }

    def list_bots_by_search(
        self,
        public: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        List bots with search and pagination.

        Args:
            public: Filter by public status ("0" or "1")
            search: Fuzzy search by owner_name or bot_name
            page: Page number (1-based)
            page_size: Items per page

        Returns:
            Dictionary with 'total' and 'items' keys
        """
        total, items = self._repository.list_by_search(public=public, search=search, page=page, page_size=page_size)
        self._attach_template_configs_to_bots(items)
        return {"total": total, "items": items}

    def list_domain_bots(
        self,
        page: int | None = None,
        page_size: int | None = None,
        keyword: str | None = None,
    ) -> Dict[str, Any]:
        """
        List domain bots (bots with ext.is_domain_bot=true).

        Args:
            page: Page number (1-based), omit for all results
            page_size: Items per page, omit for all results
            keyword: Keyword to fuzzy-match on bot_name

        Returns:
            Dictionary with 'total' and 'items' keys
        """
        total, items = self._repository.list_domain_bots(
            page=page, page_size=page_size, keyword=keyword,
        )
        return {"total": total, "items": items}

    def search_bots(
        self,
        key: Optional[str] = None,
        bot_status: Optional[str] = None,
        public: Optional[str] = None,
        owner_id: Optional[str] = None,
        service_status_list: Optional[List[str]] = None,
        bot_type: Optional[str] = None,
        active_engine: Optional[str] = None,
        collaborator_user_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        provider: Optional[str] = None,
        template_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        搜索 bots，关联发布记录。

        Args:
            key: 模糊搜索 bot_name 或 owner_name
            bot_status: ac_bots.status 过滤
            public: ac_bots.public 过滤
            owner_id: ac_bots.owner_id 过滤
            service_status_list: 服务状态列表过滤（不影响 bot 返回）
            bot_type: ac_bots.bot_type 过滤（如 "personal" 或 "service"）
            active_engine: ac_bots.active_engine 过滤（如 "openclaw"、"claude_code"、"aicoding"）
            collaborator_user_id: 协作者用户 ID，用于过滤该用户参与的 bot
            bot_id: ac_bots.bot_id 精确过滤
            provider: device_provider 过滤（如 "arca"、"daas"、"local"、"baas"）
            template_type: ac_bots.template_type 过滤
            page: 页码
            page_size: 每页数量

        Returns:
            Dictionary with 'total' and 'items' keys, items 中每条记录包含 bot 和 publish 字段，
            当 collaborator_user_id 存在时，每条记录还包含 user_role 字段
        """
        total, items = self._repository.search_bots(
            key=key,
            bot_status=bot_status,
            public=public,
            owner_id=owner_id,
            service_status_list=service_status_list,
            bot_type=bot_type,
            active_engine=active_engine,
            collaborator_user_id=collaborator_user_id,
            bot_id=bot_id,
            provider=provider,
            template_type=template_type,
            page=page,
            page_size=page_size,
        )

        # 为每个 bot 添加 can_delete_bot 和 can_upgrade_publish 字段
        try:
            publish_service = self._bot_publish_provider()

            for bot in items:
                publish = bot.get("publish")

                # 只有服务型 bot 才计算这些字段
                if bot.get("bot_type") == "service" and publish:
                    publish_id = publish.get("id")
                    if publish_id:
                        try:
                            bot["can_delete_bot"] = publish_service.can_delete_bot(publish_id)
                            bot["can_upgrade_publish"] = publish_service.can_upgrade_publish(publish_id)
                        except Exception as e:
                            logger.warning(f"[bot_service.search_bots] Failed to get can_delete/can_upgrade for publish {publish_id}: {e}")
                            bot["can_delete_bot"] = False
                            bot["can_upgrade_publish"] = False
                    else:
                        bot["can_delete_bot"] = False
                        bot["can_upgrade_publish"] = False
                else:
                    # 非服务型 bot 或无发布单，默认为 False
                    bot["can_delete_bot"] = False
                    bot["can_upgrade_publish"] = False
        except Exception as e:
            logger.warning(f"[bot_service.search_bots] Failed to add can_delete_bot/can_upgrade_publish: {e}")

        self._attach_template_configs_to_bots(items)

        return {
            "total": total,
            "items": items,
        }

    def list_bots_by_owner(
        self,
        owner_id: str,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """
        List bots by owner_id with pagination.

        Args:
            owner_id: Owner user ID
            page: Page number (1-based)
            page_size: Items per page

        Returns:
            Dictionary with 'total' and 'items' keys
        """
        total, items = self._repository.list_by_owner(
            owner_id=owner_id,
            page=page,
            page_size=page_size,
        )

        # Note: Device binding info is stored in ac_entity_device_binding table
        # To avoid N+1 queries, we don't fetch binding info for list operations
        # Use get_bot() to get detailed info including device_binding

        # 为服务型 bot 添加 can_edit_bot 字段
        try:
            publish_service = self._bot_publish_provider()

            for bot in items:
                # 只有服务型 bot 才计算 can_edit_bot
                if bot.get("bot_type") == "service":
                    bot_id = bot.get("bot_id")
                    if bot_id:
                        try:
                            bot["can_edit_bot"] = publish_service.can_edit_bot(bot_id, owner_id)
                        except Exception as e:
                            logger.warning(f"[bot_service.list_bots_by_owner] Failed to get can_edit_bot for bot {bot_id}: {e}")
                            bot["can_edit_bot"] = False
                    else:
                        bot["can_edit_bot"] = False
                else:
                    # 非服务型 bot，默认为 True
                    bot["can_edit_bot"] = True
        except Exception as e:
            logger.warning(f"[bot_service.list_bots_by_owner] Failed to add can_edit_bot: {e}")

        self._attach_template_configs_to_bots(items)

        return {
            "total": total,
            "items": items,
        }

    def list_bots_by_owner_or_collaborator(
        self,
        owner_id: str,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """List bots owned by the user or collaboratively managed by the user."""
        total, items = self._repository.list_by_owner_or_collaborator(
            owner_id=owner_id,
            page=page,
            page_size=page_size,
        )

        try:
            publish_service = self._bot_publish_provider()

            for bot in items:
                if bot.get("bot_type") == "service":
                    bot_id = bot.get("bot_id")
                    if bot_id:
                        try:
                            bot["can_edit_bot"] = publish_service.can_edit_bot(bot_id, bot["owner_id"])
                        except Exception as e:
                            logger.warning(f"[bot_service.list_bots_by_owner_or_collaborator] Failed to get can_edit_bot for bot {bot_id}: {e}")
                            bot["can_edit_bot"] = False
                    else:
                        bot["can_edit_bot"] = False
                else:
                    bot["can_edit_bot"] = True
        except Exception as e:
            logger.warning(f"[bot_service.list_bots_by_owner_or_collaborator] Failed to add can_edit_bot: {e}")

        # List reads use the persisted status. Live desktop status is reconciled
        # outside this request path so a slow BaaS cannot delay first paint.
        self._attach_template_configs_to_bots(items)

        return {
            "total": total,
            "items": items,
        }

    # Transient/process states where BaaS is NOT yet an authoritative source of
    # truth: while a desktop bot is creating (PENDING) or releasing (RELEASING),
    # the container/process may be mid-transition and BaaS reports ALL_OFFLINE
    # even though the bot is progressing normally. The backend's own status is
    # the trustworthy view during these phases; the periodic scan advances them
    # to a steady state. The list only consumes BaaS LIVE status once the bot is
    # already in a steady state (ACTIVE / OFFLINE).
    _MERGE_SKIP_LOCAL_STATUSES = frozenset({"PENDING", "RELEASING"})

    def resolve_desktop_live_status(self, bot: dict) -> str | None:
        """Resolve one desktop bot's BaaS live status; ``None`` to trust DB.

        Single source of truth for "is this a desktop bot that should consume
        BaaS right now", shared by the list-merge and the single-bot upload
        gate so the two never drift on what counts as desktop / steady-state.

        Returns the display status string when ALL hold:
        - ``bot_type == 'desktop'``
        - non-empty ``device_id``
        - local status is NOT a process state (PENDING / RELEASING) — BaaS is
          unreliable mid-transition, so the backend status is authoritative.

        Returns ``None`` otherwise (non-desktop, no device, process state, BaaS
        unmapped status, or any query failure) — the caller keeps the DB status.
        Best-effort and read-only: never writes the DB.
        """
        if bot.get("bot_type") != "desktop" or not bot.get("device_id"):
            return None
        if bot.get("status") in self._MERGE_SKIP_LOCAL_STATUSES:
            return None
        try:
            display = map_baas_to_display(
                self._device_status_client.query_device_status(bot["device_id"])
            )
            return display or None
        except Exception as e:
            logger.warning(
                "[resolve_desktop_live_status] bot=%s BaaS query failed: %s",
                bot.get("bot_id"), e,
            )
            return None

    def update_bot(
        self,
        bot_id: str,
        user_id: str,
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        share_policy: Optional[Dict[str, Any]] = None,
        ext: Optional[Dict[str, Any]] = None,
        template_config: Optional[Dict[str, Any]] = None,
        sync_to_bcn: bool = True,
        request_headers: Optional[Dict[str, str]] = None,
        cookie: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update bot information.

        Args:
            bot_id: Bot ID
            user_id: User ID (used as modifier and for permission check)
            bot_name: New bot name (optional)
            bot_desc: New bot description (optional)
            share_policy: New sharing configuration (optional)
            ext: Extension field dictionary (optional), e.g., {"avatar_url": "https://..."}
            template_config: Template configuration dictionary (optional)
            sync_to_bcn: Whether to sync to BCN (default: True)
            request_headers: Incoming HTTP request headers for downstream sync (optional)

        Returns:
            Updated bot record

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If update fails
        """
        # Check user_id is provided
        if not user_id:
            raise BotServiceError("User ID is required for updating bot")

        # Get bot by bot_id and owner_id (user_id)
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        update_data = {}

        if bot_name is not None:
            # Check if new bot_name already exists (and it's not the current bot)
            if bot_name.strip():
                existing_bot = self._repository.get_by_bot_name(bot_name.strip())
                # Identify the current record by owner AND bot_id. bot_id is only
                # unique per owner — every owner's first bot is "default" — so
                # comparing bot_id alone made another owner's "default" bot look
                # like this one, letting its name be taken even though create and
                # check-name enforce the name tenant-wide.
                if existing_bot and not (
                    existing_bot.get("bot_id") == bot_id
                    and existing_bot.get("owner_id") == bot.get("owner_id")
                ):
                    raise BotNameExistsError(f"Bot name '{bot_name}' already exists")
            update_data["bot_name"] = bot_name
        if bot_desc is not None:
            update_data["bot_desc"] = bot_desc
        if share_policy is not None:
            update_data["share_policy"] = share_policy
        if ext is not None:
            # Merge ext with existing ext (if any)
            existing_ext = bot.get("ext") or {}
            if isinstance(existing_ext, str):
                try:
                    existing_ext = json.loads(existing_ext)
                except json.JSONDecodeError:
                    existing_ext = {}
            merged_ext = {**existing_ext, **ext}
            update_data["ext"] = merged_ext

        # Update template configuration if provided
        if template_config is not None:
            # 在更新前抓取旧 template_config，用于对比 yuque_kb_repos 是否发生变化
            old_template_config: Dict[str, Any] = {}
            try:
                old_template_config = self._template_service.get_template_config(bot_id) or {}
            except Exception as e:
                logger.warning(
                    f"[bot_service.update_bot] Failed to fetch old template_config for bot {bot_id}: {e}"
                )

            try:
                # Check if template exists
                if self._template_service.exists_template(bot_id):
                    # Update existing template
                    self._template_service.update_template(
                        bot_id=bot_id,
                        template_config=template_config,
                        template_type=bot.get("template_type"),
                        active_engine=bot.get("active_engine"),
                    )
                else:
                    # Create new template (this should not normally happen in update)
                    # But we handle it gracefully
                    self._template_service.create_template(
                        bot_id=bot_id,
                        template_config=template_config,
                        template_type=bot.get("template_type"),
                        active_engine=bot.get("active_engine"),
                    )
                logger.info(f"[bot_service.update_bot] Template updated for bot {bot_id}")

                # Runtime token 变化时按引擎策略刷新运行中容器。仅当本次入参
                # 携带 token 字段且与旧值解密后不同才触发；异步执行，失败只告警
                # 不阻断主流程。
                runtime_token = self._extract_engine_runtime_token(
                    bot_id=bot_id,
                    owner_id=bot.get("owner_id") or user_id,
                    active_engine=bot.get("active_engine"),
                    bot_type=bot.get("bot_type") or "",
                    template_type=bot.get("template_type"),
                    template_config=template_config if isinstance(template_config, dict) else None,
                    log_context="bot_service.update_bot",
                )
                if (
                    isinstance(template_config, dict)
                    and "token" in template_config
                    and runtime_token is not None
                ):
                    self._maybe_refresh_codefuse_token_async(
                        bot_id=bot_id,
                        user_id=user_id,
                        old_template_config=old_template_config,
                        new_template_config=template_config,
                    )
            except Exception as e:
                logger.error(f"[bot_service.update_bot] Failed to update template for bot {bot_id}: {e}")
                # Don't fail bot update if template update fails
                # Just log the error

            # applicationCoding + claude_code 引擎的 Bot：若语雀知识库或代码仓库发生变化，重新初始化 memory
            try:
                bot_template_type = bot.get("template_type")
                bot_active_engine = bot.get("active_engine")
                from agentclaw.community.core.bot_management.utils import (
                    trigger_memory_initialization,
                )

                if self._should_trigger_memory_initialization(
                    active_engine=bot_active_engine,
                    template_type=bot_template_type,
                    template_config=template_config,
                    old_template_config=old_template_config,
                ):
                    logger.info(
                        f"[bot_service.update_bot] memory sources changed for bot {bot_id}, "
                        f"re-triggering memory initialization"
                    )
                    trigger_memory_initialization(
                        bot_id=bot_id,
                        bot_name=bot.get("bot_name") or "",
                        user_id=user_id,
                        template_config=template_config,
                        cookie=cookie or "",
                        aixcore_base_url=self._workspace_hosting_config.aixcore_base_url,
                        aixcore_base_url_pre=self._workspace_hosting_config.aixcore_base_url_pre,
                    )
            except Exception as e:
                logger.error(
                    f"[bot_service.update_bot] Failed to re-trigger memory init for bot {bot_id}: {e}"
                )

            # applicationCoding Bot：若 devflow_workflow 发生变化，更新 autoInitiate 定时任务的 workflow
            try:
                bot_template_type = bot.get("template_type")
                if bot_template_type == "applicationCoding":
                    from agentclaw.community.core.bot_management.utils import extract_workflow_name

                    old_wf = extract_workflow_name(old_template_config)
                    new_wf = extract_workflow_name(template_config)
                    if old_wf != new_wf:
                        import threading

                        def _update_cron_workflow(
                            _bot_id=str(bot_id), _user_id=user_id, _new_wf=new_wf,
                            _cron_provider=self._cron_auto_setup_provider,
                        ):
                            import asyncio

                            try:
                                cron_svc = _cron_provider()
                                asyncio.run(
                                    cron_svc.update_auto_initiate_workflow(
                                        bot_id=_bot_id,
                                        owner_id=_user_id,
                                        nick_name=_user_id,
                                        new_workflow_name=_new_wf,
                                    )
                                )
                            except Exception as exc:
                                logger.warning(
                                    f"[bot_service.update_bot] Failed to update cron workflow "
                                    f"for bot {_bot_id}: {exc}"
                                )

                        threading.Thread(
                            target=bind_current_avernet_tenant(_update_cron_workflow),
                            name=f"cron-workflow-update-{bot_id}",
                            daemon=True,
                        ).start()
                        logger.info(
                            f"[bot_service.update_bot] Workflow changed from '{old_wf}' to '{new_wf}' "
                            f"for bot {bot_id}, updating cron task in background"
                        )
            except Exception as e:
                logger.error(
                    f"[bot_service.update_bot] Failed to update cron workflow for bot {bot_id}: {e}"
                )

        if not update_data:
            # No fields to update, return current record with binding info
            return self.get_bot(bot_id, user_id)

        update_data["modifier_id"] = user_id

        try:
            # Use update_by_owner to ensure we only update the owner's bot
            bot = self._repository.update_by_owner(bot_id, user_id, update_data)
            if not bot:
                raise BotNotFoundError(f"Bot not found: {bot_id}")

            # Sync to BCN if bot_name or bot_desc was updated
            if sync_to_bcn and (bot_name is not None or bot_desc is not None):
                sync_kwargs = {
                    "bot_id": bot_id,
                    "owner_id": user_id,
                    "bot_name": bot_name,
                    "bot_desc": bot_desc,
                }
                if request_headers:
                    sync_kwargs["request_headers"] = request_headers
                self._sync_bot_to_bcn(**sync_kwargs)

            # Fetch binding info from ac_entity_device_binding if exists
            binding_id = bot.get("binding_id")
            if binding_id:
                try:
                    service = self._device_service_provider()
                    binding = service.get_device(binding_id=binding_id)
                    if binding:
                        bot["device_binding"] = binding.to_dict()
                except Exception as e:
                    logger.warning(f"[bot_service.update_bot] Failed to get device binding {binding_id}: {e}")

            # Fetch template info if exists
            try:
                template = self._template_service.get_template(bot_id)
                if template:
                    bot["template_config"] = template.get("ext")
            except Exception as e:
                logger.warning(f"[bot_service.update_bot] Failed to get template for bot {bot_id}: {e}", exc_info=True)

            return bot
        except BotNotFoundError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.update_bot] Failed to update bot {bot_id}: {e}")
            raise BotServiceError(f"Failed to update bot: {e}")

    def _maybe_refresh_codefuse_token_async(
        self,
        *,
        bot_id: str,
        user_id: str,
        old_template_config: Dict[str, Any],
        new_template_config: Dict[str, Any],
    ) -> None:
        """token 变化时异步把新 codefuse token 写入运行中容器。

        去重采用「密文比密文」同口径：``old_template_config`` 是落库前从 DB 抓回
        的旧密文（``enc:v1:`` 前缀）；新值在 ``update_template`` 落库后回读 DB
        拿到新密文。两者同为 DB 密文，比较才有意义。若直接拿「入参明文」与
        「DB 密文」比，恒不等，会导致每次带 token 的 PUT 都 exec 容器，违背去重
        初衷。只有真正变化才下发，下发在 daemon 线程里执行，失败只记 warning，
        不影响更新主流程（与现有 memory-init / cron 异步副作用语义一致）。
        """
        try:
            old_token_cipher = (old_template_config or {}).get("token")
            # 入参是否携带 token：未带则不下发（如仅改 yuque_kb_repos）。
            incoming_token = (new_template_config or {}).get("token")
            if not isinstance(incoming_token, str) or not incoming_token:
                return

            # 落库后回读 DB 拿新密文（update_template 已把入参明文加密落库），
            # 与旧 DB 密文同口径比较；回读失败则一律视为变化（安全侧触发刷新）。
            new_template_config_db = self._template_service.get_template_config(bot_id)
            new_token_cipher = (
                (new_template_config_db or {}).get("token")
                if isinstance(new_template_config_db, dict)
                else None
            )
            if (
                isinstance(old_token_cipher, str)
                and isinstance(new_token_cipher, str)
                and old_token_cipher == new_token_cipher
            ):
                return  # 密文一致 → 真未变化，跳过

            # 解密新 token 为明文 auth_code：用真实 vault，与 apply_device 启动
            # 解密、reconciler 重启解密同路径；空 master_key（singlebox）退化为
            # 透传，不另造空钥实例以免 enc:v1: 密文被静默写坏。
            plaintext = self._template_service.get_decrypted_codefuse_token(bot_id)
            if not plaintext:
                return
        except Exception as e:
            logger.warning(
                "[bot_service._maybe_refresh_codefuse_token_async] prepare failed for "
                "bot %s: %s", bot_id, e,
            )
            return

        thread = threading.Thread(
            target=bind_current_avernet_tenant(self._refresh_codefuse_token_on_device),
            kwargs={"bot_id": bot_id, "user_id": user_id, "plaintext_token": plaintext},
            daemon=True,
            name=f"refresh-codefuse-token-{bot_id}",
        )
        thread.start()

    def _refresh_codefuse_token_on_device(
        self, *, bot_id: str, user_id: str, plaintext_token: str
    ) -> None:
        """把明文 codefuse auth_code 写入 bot 当前绑定设备的 codefuse.json。

        baas 设备走 ``write_codefuse_token_baas``（exec_command_on_bot），
        arca / local 设备走 ``DeviceService.exec_shell``，与
        ``aicoding/router.save_codefuse_token`` 的双分支对称。设备未 ACTIVE /
        无 binding / exec 失败均只告警，不抛出（异步线程上下文，无法回传错误）。
        """
        try:
            bot = self._repository.get_by_id_and_owner(bot_id, user_id)
            if not bot:
                logger.warning(
                    "[bot_service._refresh_codefuse_token_on_device] bot not found: %s",
                    bot_id,
                )
                return
            binding_id = bot.get("binding_id")
            if not binding_id:
                logger.warning(
                    "[bot_service._refresh_codefuse_token_on_device] no binding for bot %s",
                    bot_id,
                )
                return

            device_service = self._device_service_provider()
            binding = self._device_binding_repo.get_by_id(binding_id)
            if not binding:
                logger.warning(
                    "[bot_service._refresh_codefuse_token_on_device] binding %s not found",
                    binding_id,
                )
                return

            from agentclaw.community.core.bot_management.codefuse_token import (
                build_codefuse_write_cmd_from_auth_code,
            )

            provider = getattr(binding, "device_provider", None) or ""
            if provider == "baas":
                bot_uuid = (getattr(binding, "device_props", None) or {}).get("bot_uuid")
                if not bot_uuid:
                    logger.warning(
                        "[bot_service._refresh_codefuse_token_on_device] no bot_uuid for "
                        "bot %s", bot_id,
                    )
                    return
                from agentclaw.community.core.devices.services.baas_codefuse_writer import (
                    write_codefuse_token_baas,
                )
                write_codefuse_token_baas(
                    self._baas_service_provider(), bot_uuid, plaintext_token
                )
            else:
                device_id = getattr(binding, "device_id", None)
                if not device_id:
                    logger.warning(
                        "[bot_service._refresh_codefuse_token_on_device] no device_id for "
                        "bot %s", bot_id,
                    )
                    return
                cmd = build_codefuse_write_cmd_from_auth_code(plaintext_token)
                device_service.exec_shell(device_id=device_id, shell_cmd=cmd)

            logger.info(
                "[bot_service._refresh_codefuse_token_on_device] codefuse.json refreshed: "
                "bot_id=%s provider=%s", bot_id, provider,
            )
        except Exception as e:
            logger.warning(
                "[bot_service._refresh_codefuse_token_on_device] failed for bot %s: %s",
                bot_id, e, exc_info=True,
            )

    def admin_update_bot(
        self,
        bot_id: str,
        owner_id: str,
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        template_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Admin update bot — operator can modify any bot's config.

        Unlike update_bot(), this method:
        - Does NOT require the caller to be the bot owner
        - Uses owner_id to locate the bot (not the caller's user_id)
        - Supports updating template_config (sandbox overrides)

        Args:
            bot_id: Bot ID
            owner_id: Bot owner's user ID (required for locating the bot)
            bot_name: New bot name (optional)
            bot_desc: New bot description (optional)
            template_config: Template configuration dict (optional, contains sandbox overrides)

        Returns:
            Dict with bot record, template_config, and optional warning

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If update fails
        """
        if not owner_id:
            raise BotServiceError("owner_id is required for admin update")

        # Locate the bot by bot_id + owner_id
        bot = self._repository.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id} (owner: {owner_id})")

        update_data = {}

        if bot_name is not None:
            if bot_name.strip():
                existing_bot = self._repository.get_by_bot_name(bot_name.strip())
                if existing_bot and existing_bot.get("bot_id") != bot_id:
                    raise BotNameExistsError(f"Bot name '{bot_name}' already exists")
            update_data["bot_name"] = bot_name

        if bot_desc is not None:
            update_data["bot_desc"] = bot_desc

        # Update ac_bots fields if any
        if update_data:
            update_data["modifier_id"] = "admin"
            bot = self._repository.update_by_owner(bot_id, owner_id, update_data)
            if not bot:
                raise BotNotFoundError(f"Bot not found after update: {bot_id}")

        # Update template_config if provided — merge with existing config
        if template_config is not None:
            # Validate sandbox fields if present (validate the merged result)
            from agentclaw.community.core.devices.services.sandbox_overrides import (
                SandboxOverrides,
                InvalidSandboxOverridesError,
            )

            # Merge: overlay the incoming fields on top of the existing config
            existing_config = self._template_service.get_template_config(bot_id) or {}
            merged_config = {**existing_config, **template_config}

            try:
                overrides = SandboxOverrides.from_template_config(merged_config)
                if not overrides.is_empty():
                    overrides.validate()
            except InvalidSandboxOverridesError as e:
                raise BotServiceError(f"沙箱配置校验失败: {e}") from e

            if self._template_service.exists_template(bot_id):
                self._template_service.update_template(
                    bot_id=bot_id,
                    template_config=merged_config,
                    template_type=bot.get("template_type"),
                    active_engine=bot.get("active_engine"),
                )
            else:
                self._template_service.create_template(
                    bot_id=bot_id,
                    template_config=merged_config,
                    template_type=bot.get("template_type"),
                    active_engine=bot.get("active_engine"),
                )
            logger.info("[bot_service.admin_update_bot] Template updated for bot %s by admin", bot_id)

        # Build response — return the full merged config
        result = self.get_bot(bot_id, owner_id)
        if template_config is not None:
            result["template_config"] = merged_config
        else:
            result["template_config"] = self._template_service.get_template_config(bot_id)

        # Add warning if sandbox config was changed
        if template_config is not None:
            result["warning"] = "沙箱配置变更将在下次重启后生效"

        return result

    def _sync_bot_to_bcn(
        self,
        bot_id: str,
        owner_id: str,
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        request_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Sync bot name and summary to BCN.

        BCN 的 bot_id 格式为 "{tc_bot_id}:{owner_workno}"，例如 "20260421_gfdsz5vi:85020"。

        Args:
            bot_id: TC 页面上的 bot_id
            owner_id: Bot 所有者的工号
            bot_name: 新的 Bot 名称（如果更新了）
            bot_desc: 新的 Bot 简介（如果更新了）
            request_headers: 原始 HTTP 请求头，BCN 层会筛选需要透传的认证头
        """
        try:
            from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError

            # BCN 的 bot_id 格式: {tc_bot_id}:{owner_workno}
            bcn_bot_id = f"{bot_id}:{owner_id}"

            # 获取当前 bot 信息，用于填充未更新的字段
            bot = self._repository.get_by_id_and_owner(bot_id, owner_id)
            if not bot:
                logger.warning(f"[bot_service._sync_bot_to_bcn] Bot not found: {bot_id}")
                return

            # 使用传入的新值或现有的值
            final_name = bot_name if bot_name is not None else bot.get("bot_name", bot_id)
            final_summary = bot_desc if bot_desc is not None else bot.get("bot_desc", "")

            onboard_kwargs = {
                "bot_id": bcn_bot_id,
                "name": final_name or bot_id,
                "summary": final_summary or "",
            }
            if request_headers:
                onboard_kwargs["request_headers"] = request_headers
            self._bcn_service.onboard_bot(**onboard_kwargs)

            logger.info(
                f"[bot_service._sync_bot_to_bcn] Synced to BCN: "
                f"bot_id={bcn_bot_id}, name={final_name}"
            )

        except BcnServiceError as e:
            # BCN 同步失败不影响主流程，只记录警告日志
            logger.warning(
                f"[bot_service._sync_bot_to_bcn] Failed to sync to BCN: "
                f"bot_id={bot_id}, owner_id={owner_id}, error={e}"
            )
        except Exception as e:
            logger.warning(
                f"[bot_service._sync_bot_to_bcn] Unexpected error syncing to BCN: "
                f"bot_id={bot_id}, owner_id={owner_id}, error={e}"
            )

    @staticmethod
    def _should_register_bcn_provider(
        active_engine: Optional[str],
        bot_type: Optional[str],
        template_type: Optional[str],
        template_config: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """统一 create / start 注册 BCN Provider 的触发条件.

        如果 AC 模板工厂快照显式声明 capabilities，则 capabilities 是唯一事实源；
        缺失能力节点按 False，不再混用 legacy template_type fallback。旧 Bot 没有
        capabilities 时继续保留历史逻辑。
        """
        if is_template_factory_config(template_config) and has_declared_capabilities(template_config):
            return can_join_bcn_as_provider(template_config)

        is_coding_personal = (
            active_engine in ("claude_code", "aicoding")
            and template_type == "personalCoding"
        )
        return (
            is_coding_personal
            or (
                active_engine == "claude_code"
                and template_type == "normalCC"
            )
            or active_engine == "teclaw"
            or (active_engine == "openclaw" and bot_type == "service")
        )

    def _register_bot_to_bcn_as_provider(
        self,
        bot_id: str,
        user_id: str,
        owner_workno: str,
        bot_name: str,
        bot_summary: str,
    ) -> None:
        """Bot 创建/启动时把自己注册到 BCN 为 Provider (下行链路).

        当前在以下场景调用 (由 create / start 判定):
          - active_engine == "claude_code" 且 template_type == "normalCC"
          - active_engine == "claude_code" 且 template_type == "personalCoding"
          - active_engine == "aicoding" 且 template_type == "personalCoding"
          - active_engine == "teclaw"
          - active_engine == "openclaw" 且 bot_type == "service"

        受 DRM 开关 ``ClaudeCodeBcnRegister.enabled`` 控制 (默认关),
        失败仅记 warning, 不阻塞 start 主流程 (与 _sync_bot_to_bcn 风格一致).
        注册成功时往 ext 落 ``bcn_registered=True`` 标志, 不写 token.

        Args:
            bot_id: TC bot uuid
            user_id: 当前调用者 (保留参数, 当前实现不写 token)
            owner_workno: bot 所有者工号
            bot_name: bot 显示名
            bot_summary: bot 简介
        """
        # DRM 开关: 默认关, 只有显式开启时才注册. 读取失败也按关处理.
        # 排查日志关键字: [bot_service._register_bot_to_bcn_as_provider]
        if not self._is_claude_code_bcn_register_enabled():
            logger.info(
                f"[bot_service._register_bot_to_bcn_as_provider] disabled by DRM, "
                f"skip bot_id={bot_id} owner={owner_workno}"
            )
            return

        try:
            from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError

            result = self._bcn_service.register_provider_bot(
                teamclaw_bot_uuid=bot_id,
                owner_workno=owner_workno,
                name=bot_name,
                summary=bot_summary,
            )

            if result.get("skipped"):
                logger.info(
                    f"[bot_service._register_bot_to_bcn_as_provider] env-skipped, "
                    f"bot_id={bot_id} owner={owner_workno}"
                )
                return

            # bot_runtime_token 暂不落库 (按需求方要求), 仅在 ext 标记一个布尔
            # 成功标志 ``bcn_registered=True`` 用于上层判断 / 排查; 包含幂等成功.
            # TODO: 后续如确认下行调用需要 token, 再补持久化方式 (ext / secret store).
            bot_uuid = result.get("bot_uuid") or ""
            idempotent = result.get("idempotent_replay", False)
            has_token = bool(result.get("bot_runtime_token"))
            logger.info(
                f"[bot_service._register_bot_to_bcn_as_provider] registered "
                f"bot_id={bot_id} owner={owner_workno} "
                f"bot_uuid={bot_uuid} idempotent={idempotent} "
                f"runtime_token_present={has_token}"
            )

            try:
                self.update_bot_ext(
                    bot_id=bot_id,
                    user_id=user_id,
                    ext_update={"bcn_registered": True},
                )
            except Exception as ext_err:
                logger.warning(
                    f"[bot_service._register_bot_to_bcn_as_provider] "
                    f"failed to mark bcn_registered in bot.ext: {ext_err}"
                )

        except BcnServiceError as e:
            logger.warning(
                f"[bot_service._register_bot_to_bcn_as_provider] BCN register failed: "
                f"bot_id={bot_id} owner={owner_workno} error={e}"
            )
        except Exception as e:
            logger.warning(
                f"[bot_service._register_bot_to_bcn_as_provider] Unexpected error: "
                f"bot_id={bot_id} owner={owner_workno} error={e}"
            )

    # DRM 控制开关: claude_code 启动时是否往 BCN 注册 Provider bot.
    # value 取 "true"/"on"/"1" 视为开启, 其它 (含空 / 异常 / 未配置) 视为关闭.
    # 排查日志关键字: [DRM] ClaudeCodeBcnRegister
    _CLAUDE_CODE_BCN_REGISTER_DRM_ID = (
        "Alipay.agentclaw:name=com.alipay.agentclaw.service.drm."
        "ClaudeCodeBcnRegister.enabled,version=3.0@DRM"
    )

    # DRM 控制开关: 新建 bot 是否使用 NAS 存储 (value=="nas" 才走 NAS, 否则 OSS).
    _NEW_BOT_NAS_DRM_ID = (
        "Alipay.agentclaw:name=com.alipay.agentclaw.service.drm."
        "NewBotUserNas.use_nas,version=3.0@DRM"
    )

    def _is_claude_code_bcn_register_enabled(self) -> bool:
        """读 DRM 判断 claude_code BCN 注册是否启用. 默认关 (失败也关).

        Returns:
            True: DRM 显式开启 (value in {"true", "on", "1"} 不区分大小写)
            False: DRM 关闭 / 取不到 / 异常
        """
        raw_value = self._drm_reader.read(self._CLAUDE_CODE_BCN_REGISTER_DRM_ID)
        value = str(raw_value).strip().lower() if raw_value else ""
        enabled = value in ("true", "on", "1")
        logger.info(
            f"[DRM] ClaudeCodeBcnRegister value={raw_value!r} enabled={enabled}"
        )
        return enabled

    def _is_new_bot_use_nas(self) -> bool:
        """读 DRM 判断新建 bot 是否使用 NAS 存储. 默认 OSS (空/失败也 OSS).

        Returns:
            True: DRM 值为 "nas"，新建 bot 走 NAS 分支
            False: 其他情况（空、"oss"、取不到等），走 OSS 分支
        """
        value = self._drm_reader.read(self._NEW_BOT_NAS_DRM_ID)
        logger.info(f"[DRM] NewBotUserNas value={value}")
        return value == "nas"

    def get_bot_config_path(
        self,
        bot_id: str,
        user_id: str,
        engine_type: str = DEFAULT_ENGINE_TYPE
    ) -> Path:
        """
        Get bot configuration directory path by bot_id.

        Path structure: {aidesktop_root}/aidesktop_{env}/bolt_data/{entity_id}/{bot_id}/{engine_type}_conf

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check (must be the owner)
            engine_type: Engine type (default: "moltis")

        Returns:
            Path: Bot configuration directory path

        Raises:
            BotNotFoundError: If bot not found
        """
        # Get bot by bot_id and owner_id (user_id)
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        entity_id = bot.get("entity_id")
        entity_type = bot.get("entity_type", "staff")
        if not entity_id:
            raise BotServiceError(f"Bot {bot_id} has no associated entity_id")

        # Get config path from config module
        config_path = get_bot_engine_config_dir(entity_id, str(bot_id), engine_type, entity_type)
        logger.info(f"[bot_service.get_bot_config_path] Bot {bot_id} config path: {config_path}")

        return config_path

    def get_bot_work_path(
        self,
        bot_id: str,
        user_id: str,
        engine_type: str = DEFAULT_ENGINE_TYPE
    ) -> Path:
        """
        Get bot working directory path by bot_id.

        Path structure: {aidesktop_root}/aidesktop_{env}/bolt_data/{entity_id}/{bot_id}/{engine_type}

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check (must be the owner)
            engine_type: Engine type (default: "moltis")

        Returns:
            Path: Bot working directory path

        Raises:
            BotNotFoundError: If bot not found
        """
        # Get bot by bot_id and owner_id (user_id)
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        entity_id = bot.get("entity_id")
        entity_type = bot.get("entity_type", "staff")
        if not entity_id:
            raise BotServiceError(f"Bot {bot_id} has no associated entity_id")

        # Get work path from config module
        work_path = get_bot_engine_dir(entity_id, str(bot_id), engine_type, entity_type)
        logger.info(f"[bot_service.get_bot_work_path] Bot {bot_id} work path: {work_path}")

        return work_path

    def get_engine_paths(
        self,
        entity_id: str,
        bot_id: str,
        engine_types: list[str],
        entity_type: str = "staff",
    ) -> dict[str, str]:
        """Get bot working directories for all engines.

        Handles sqlite/prod mode switching internally.

        Args:
            entity_id: Entity ID
            bot_id: Bot ID
            engine_types: List of engine types
            entity_type: Entity type (default: "staff")

        Returns:
            Dict mapping engine_type to path string
        """
        return {
            engine: str(
                self._path_factory.get_engine_workspace_data_dir(
                    entity_id, bot_id, engine, entity_type
                )
            )
            for engine in engine_types
        }

    def switch_engine(self, bot_id: str, user_id: str, engine_type: str) -> Dict[str, Any]:
        """
        Switch the active engine for a bot.

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check (must be the owner)
            engine_type: New engine type to switch to

        Returns:
            Updated bot record

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If engine type is invalid or update fails
        """
        # Check user_id is provided
        if not user_id:
            raise BotServiceError("User ID is required for switching engine")

        # Validate engine_type
        supported_engines = _get_engine_types()
        if engine_type not in supported_engines:
            raise BotServiceError(f"Invalid engine type: {engine_type}. Supported engines: {supported_engines}")

        # Get bot by bot_id and owner_id (user_id)
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        self._validate_default_bot_engine(bot_id, engine_type)

        # Check if the engine is in bot's engine_types
        bot_engine_types = bot.get("engine_types", [])
        if engine_type not in bot_engine_types:
            raise BotServiceError(f"Engine '{engine_type}' is not enabled for this bot. Enabled engines: {bot_engine_types}")

        try:
            # Update active_engine (use update_by_owner to ensure we only update the owner's bot)
            update_data = {
                "active_engine": engine_type,
                "modifier_id": user_id,
            }
            updated_bot = self._repository.update_by_owner(bot_id, user_id, update_data)
            if not updated_bot:
                raise BotNotFoundError(f"Bot not found: {bot_id}")

            logger.info(f"[bot_service.switch_engine] Bot {bot_id} active_engine switched to {engine_type} by user {user_id}")

            # Fetch binding info from ac_entity_device_binding if exists
            binding_id = updated_bot.get("binding_id")
            if binding_id:
                try:
                    service = self._device_service_provider()
                    binding = service.get_device(binding_id=binding_id)
                    if binding:
                        updated_bot["device_binding"] = binding.to_dict()
                except Exception as e:
                    logger.warning(f"[bot_service.switch_engine] Failed to get device binding {binding_id}: {e}")

            return updated_bot
        except BotNotFoundError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.switch_engine] Failed to switch engine for bot {bot_id}: {e}")
            raise BotServiceError(f"Failed to switch engine: {e}")

    def delete_bot(self, bot_id: str, user_id: str, nick_name: Optional[str] = None) -> bool:
        """
        Soft delete a bot.

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check and releasing device (required)
            nick_name: Nick name for releasing device (optional, defaults to bot owner)

        Returns:
            True if deleted, False if not found

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If deletion fails
        """
        # Check user_id is provided
        if not user_id:
            raise BotServiceError("User ID is required for deleting bot")

        try:
            # Get bot by bot_id and owner_id (user_id)
            bot = self._repository.get_by_id_and_owner(bot_id, user_id)
            if not bot:
                raise BotNotFoundError(f"Bot not found: {bot_id}")

            # 保护 owner 名下最早创建的 bot 不能删除（等价于旧 "default" bot 不可删语义）。
            # 含 owner 仅一只的情形（earliest 即该只 → 拒），自然保留 ≥1。
            # 必须在 release_device / destroy_passport 之前拦截，否则会误销毁
            # agent 许可证 (Passport) 并重置引擎配置 (openclaw.json)。
            # 用 BotOperationNotAllowedError（BotServiceError 子类）表达"这是客户端
            # 不支持的操作"，而不是服务端故障：重试永远不会成功。内部路由的 except 链没有
            # 这一分支，仍落到 `except BotServiceError` → 500，行为不变；公共 API 则按
            # 4xx 映射。
            _total_owner_bots, owner_bot_items = self._repository.list_by_owner(user_id, 1, 1000)
            if owner_bot_items:
                # gmt_create可能是 datetime 或 ISO 字符串;统一成字符串排序,避免类型混比。
                # 空值兜底为空串(ISO 字符串排序下排最前,等同"最早")。
                earliest = min(
                    owner_bot_items,
                    key=lambda b: str(b.get("gmt_create") or ""),
                )
                earliest_bot_id = earliest.get("bot_id")
                if earliest_bot_id and bot_id == earliest_bot_id:
                    raise BotOperationNotAllowedError("不能删除首个创建的 Bot，该 Bot 受保护")

            # Withdraw every application authorization standing against this
            # bot — whoever delegated it — before anything destructive happens.
            # This is the first of two sweeps; the second runs after the soft
            # delete, and the pair is what actually closes the gap. See there.
            #
            # Ordering is the whole point. Placed after the device release and
            # the passport destruction, a failure here would leave a bot that is
            # already unusable with live grants against it: applications still
            # authorized to reach something that no longer works, and no
            # deletion to trigger the sweep again. Placed here, a failure aborts
            # while the bot is still intact, and the worst outcome is grants
            # withdrawn from a bot that survived — recoverable by re-granting.
            #
            # Failures propagate deliberately. Swallowing this would reintroduce
            # exactly the gap it closes, and quietly.
            revoked = self._bot_app_grant_provider().revoke_all_for_bot(
                bot_id=bot_id, owner_id=user_id
            )
            if revoked:
                logger.info(
                    "[bot_service.delete_bot] withdrew %s app authorization(s) "
                    "on bot %s before deleting it",
                    revoked,
                    bot_id,
                )

            # Release device if binding exists (包括 ACTIVE 和 PENDING 状态)
            binding_id = bot.get("binding_id")
            if binding_id:
                service = self._device_service_provider()
                # Get device binding from ac_entity_device_binding table
                binding = service.get_device(binding_id=binding_id)
                if binding and binding.status in [
                    DeviceBindingStatus.ACTIVE.value,
                    DeviceBindingStatus.PENDING.value,
                    DeviceBindingStatus.FAILED.value,
                    DeviceBindingStatus.STOPPED.value,
                ]:
                    # Release the device
                    operator = _compose_operator_context(
                        user_id,
                        nick_name or user_id
                    )
                    try:
                        service.release_device(
                            binding_id=binding_id,
                            release_reason=f"Bot {bot_id} deleted",
                            reset=True,  # 删除设备室，强制重置引擎配置(删除openclaw.json)
                            operator=operator,
                        )
                        logger.info(f"[bot_service.delete_bot] Released device for binding {binding_id}")
                    except Exception as e:
                        # 释放失败，阻断删除，抛出异常
                        logger.error(f"[bot_service.delete_bot] Failed to release device for binding {binding_id}: {e}")
                        raise BotServiceError(f"设备释放失败，无法删除 Bot: {e}")

            # Step 2: 通知 Passport 服务销毁 Passport（阻塞流程）
            try:
                self._passport_plugin.destroy_passport(bot_id, user_id)
                logger.info(f"[bot_service.delete_bot] destroyPassport success: bot_id={bot_id}, owner_workno={user_id}")
            except PassportError as e:
                logger.error(f"[bot_service.delete_bot] destroyPassport failed: bot_id={bot_id}, owner_workno={user_id}, error={e}")
                raise BotServiceError(f"销毁 Passport 失败: {e}")

            # Soft delete the bot (use soft_delete_by_owner to ensure we only delete the owner's bot)
            result = self._repository.soft_delete_by_owner(bot_id, user_id)
            if not result:
                raise BotNotFoundError(f"Bot not found: {bot_id}")

            self._sweep_grants_that_raced_the_deletion(bot_id, user_id)

            self._sync_provider_bot_delete_to_bcn(bot_id, user_id)

            # 清理关联的脏数据（仅限非 default bot）
            # default bot 是用户的默认 Bot，删除它通常是"重启"逻辑，应保留技能和配置
            if bot_id != "default":
                try:
                    self._cleanup_bot_associated_data(bot_id, user_id)
                    logger.info(f"[bot_service.delete_bot] Cleaned up associated data for bot {bot_id}")
                except Exception as cleanup_error:
                    # 清理失败不影响删除结果，只记录日志
                    logger.warning(f"[bot_service.delete_bot] Failed to cleanup associated data for bot {bot_id}: {cleanup_error}")
            else:
                logger.info(f"[bot_service.delete_bot] Skipping cleanup for default bot {bot_id} (restart scenario)")

            logger.info(f"[bot_service.delete_bot] Bot {bot_id} deleted successfully")
            return True
        except BotNotFoundError:
            raise
        except BotOperationNotAllowedError:
            # 必须在 catch-all 之前放行：否则"不允许删除 default bot"这类客户端错误
            # 会被重新包成通用 BotServiceError，调用方看到 500 并可能无谓重试。
            raise
        except Exception as e:
            logger.error(f"[bot_service.delete_bot] Failed to delete bot {bot_id}: {e}")
            raise BotServiceError(f"Failed to delete bot: {e}")

    def _sweep_grants_that_raced_the_deletion(
        self, bot_id: str, user_id: str
    ) -> None:
        """Withdraw grants that landed while the deletion was in flight.

        The first sweep commits, and then the deletion spends time in the device
        release and the Passport destruction — both remote calls. A collaborator
        granting in that window inserts a row *after* the sweep read, and it
        would outlive the bot.

        **This narrows the window; it does not close it, and an earlier
        revision of this docstring wrongly claimed it did.** The claim was that
        once ``soft_delete_by_owner`` commits no further grant can be created,
        because every way the delegation gate resolves a bot filters
        ``is_delete == 0``. Those filters are real, but they guard *resolution*,
        which happens early in the request — the row is written later, in
        ``BotAppGrantService.grant``. A request that had already resolved the
        bot can still insert behind this sweep.

        What remains is bounded on the other side by
        :class:`~...bot_app_grant.errors.GrantBotNotLiveError`: the grant path
        rechecks liveness at the write, so the surviving gap is between that
        check and its insert rather than the whole request. Closing even that
        would mean locking the bot row across every grant write, to prevent a
        row that grants nothing — every read filters bots by liveness, and every
        request re-adjudicates against a bot that cannot be resolved. Hygiene,
        not the boundary. The boundary is that nothing is trusted from the row.

        **Failures propagate, like the first sweep.** An earlier revision made
        this best-effort, reasoning that the bot is already deleted so raising
        reports a failure for an operation that succeeded. That reasoning is
        real but it loses: `AGENTS.md` is explicit that persistence write
        failures are propagated and never swallowed into a success, and a sweep
        that could not commit is exactly a failed write. Reporting success
        while the authorization table still holds rows for this bot makes the
        two disagree with no signal that they do.

        The cost is worth naming rather than hiding: the deletion really has
        happened by then, so a caller who retries is answered "no such bot".
        That is a confusing sequence, but it is an honest one — where silent
        success is a wrong answer that no one can later discover.

        A non-zero count here means the race actually happened, which is worth
        seeing in the log rather than inferring later from an orphan row.
        """
        raced = self._bot_app_grant_provider().revoke_all_for_bot(
            bot_id=bot_id, owner_id=user_id
        )
        if raced:
            logger.warning(
                "[bot_service.delete_bot] withdrew %s app authorization(s) "
                "created on bot %s while it was being deleted",
                raced,
                bot_id,
            )

    def _sync_provider_bot_delete_to_bcn(self, bot_id: str, user_id: str) -> None:
        """Best-effort sync of local bot deletion to BCN provider bot deletion."""
        try:
            result = self._bcn_service.delete_provider_bot(
                teamclaw_bot_uuid=bot_id,
                owner_workno=user_id,
            )
            logger.info(
                f"[bot_service._sync_provider_bot_delete_to_bcn] "
                f"provider bot delete synced: bot_id={bot_id}, "
                f"owner_workno={user_id}, result={result}"
            )
        except Exception as e:
            logger.error(
                f"[bot_service._sync_provider_bot_delete_to_bcn] "
                f"Failed to sync provider bot delete, ignored: "
                f"bot_id={bot_id}, owner_workno={user_id}, error={e}",
                exc_info=True,
            )

    def _cleanup_bot_associated_data(self, bot_id: str, user_id: str) -> Dict[str, Any]:
        """
        清理 Bot 关联的脏数据（技能、技能集、资源等）

        注意：此方法仅应在确认 Bot 真正被删除时调用（非 default bot 的重启场景）

        Args:
            bot_id: Bot ID
            user_id: 用户ID

        Returns:
            清理结果统计
        """
        logger.info(f"[bot_service._cleanup_bot_associated_data] Cleaning up data for bot {bot_id}, user {user_id}")

        result = {
            "skills_deleted": 0,
            "skill_sets_deleted": 0,
            "resources_deleted": 0,
            "errors": []
        }

        try:
            # 使用 cleanup_service 清理单个 bot 的数据
            cleanup_result = self._cleanup_service.cleanup_single_bot_data(bot_id, user_id)

            result["skills_deleted"] = cleanup_result.get("skills_deleted", 0)
            result["skill_sets_deleted"] = cleanup_result.get("skill_sets_deleted", 0)
            result["resources_deleted"] = cleanup_result.get("resources_deleted", 0)

            logger.info(
                f"[bot_service._cleanup_bot_associated_data] Cleanup completed for bot {bot_id}: "
                f"skills={result['skills_deleted']}, skill_sets={result['skill_sets_deleted']}, "
                f"resources={result['resources_deleted']}"
            )

        except Exception as e:
            error_msg = f"Cleanup error for bot {bot_id}: {e}"
            logger.error(f"[bot_service._cleanup_bot_associated_data] {error_msg}")
            result["errors"].append(error_msg)

        return result

    def check_bot_name_exists(self, bot_name: str) -> bool:
        """
        Check if a bot with specific bot_name exists globally.

        Args:
            bot_name: Bot name to check

        Returns:
            True if exists, False otherwise
        """
        if not bot_name or not bot_name.strip():
            return False

        return self._repository.exists_by_bot_name(bot_name.strip())

    def stop_bot(
        self,
        bot_id: str,
        user_id: str,
        nick_name: Optional[str] = None,
        release_reason: Optional[str] = None,
    ) -> bool:
        """Stop a bot by releasing its current device and resetting status to PENDING.

        Args:
            bot_id: Bot ID
            user_id: User ID (must be the owner)
            nick_name: Nick name for operator context (optional, defaults to user_id)
            release_reason: Reason for releasing device (optional)

        Returns:
            True if stopped successfully (including if device was already released),
            False if device release failed but status was still reset.

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If stop fails critically
        """
        if not user_id:
            raise BotServiceError("User ID is required for stopping bot")

        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        # 桌面 bot 的设备管理走 BaaS 流程，不应通过 DeviceService 释放，
        # 也不应清空 binding_id/device_id（桌面 bot 的 delete/restart 由 DesktopBotService 处理）。
        if bot.get("bot_type") == "desktop":
            raise BotServiceError(
                f"Desktop bot {bot_id} cannot be stopped via BotService.stop_bot, "
                f"use DesktopBotService instead"
            )

        resolved_nick_name = nick_name or user_id
        reason = release_reason or f"Bot {bot_id} stopped"

        # Release current device if binding exists
        binding_id = bot.get("binding_id")
        release_failed = False
        if binding_id:
            try:
                service = self._device_service_provider()
                binding = service.get_device(binding_id=binding_id)
                if binding and binding.status in [
                    DeviceBindingStatus.ACTIVE.value,
                    DeviceBindingStatus.PENDING.value,
                    DeviceBindingStatus.FAILED.value,
                    DeviceBindingStatus.STOPPED.value,
                ]:
                    operator = _compose_operator_context(user_id, resolved_nick_name)
                    service.release_device(
                        binding_id=binding_id,
                        release_reason=reason,
                        operator=operator,
                    )
                    logger.info(f"[bot_service.stop_bot] Released device for binding {binding_id}")
            except BotNotFoundError:
                logger.info(f"[bot_service.stop_bot] Device binding {binding_id} not found, treating as already released")
            except InvalidDeviceStatusError as e:
                logger.info(f"[bot_service.stop_bot] Device binding {binding_id} cannot be released: {e}")
            except Exception as e:
                logger.error(f"[bot_service.stop_bot] Failed to release device for binding {binding_id}: {e}")
                release_failed = True

        # Reset bot status to PENDING and clear binding info
        self._repository.update_by_owner(bot_id, user_id, {
            "status": "PENDING",
            "binding_id": None,
            "device_id": None,
        })
        logger.info(f"[bot_service.stop_bot] Bot {bot_id} status reset to PENDING")

        return not release_failed

    def update_status(self, bot_id: str, user_id: str, status: str) -> None:
        """Update the status column for a bot owned by user_id.

        Used by the dormant-bot recycling flow to mark a bot as RECYCLED
        after stop_bot has released its device.

        Args:
            bot_id: The bot whose status to update.
            user_id: Owner of the bot (used for ownership guard).
            status: New status string, e.g. ``'RECYCLED'``.
        """
        self._repository.update_by_owner(bot_id, user_id, {"status": status})
        logger.info(
            "[bot_service.update_status] bot_id=%s user_id=%s status=%s",
            bot_id, user_id, status,
        )

    def _resolve_current_device_restart_context(
        self,
        *,
        bot_id: str,
        binding_id: int,
    ) -> tuple[str, str | None]:
        """Return binding state while preserving its original provider."""
        try:
            service = self._device_service_provider()
            binding = service.get_device(binding_id=binding_id)
        except Exception as e:
            raise BotServiceError(
                f"Bot {bot_id} binding {binding_id} cannot resolve restart context: {e}"
            ) from e

        if isinstance(binding, dict):
            provider = binding.get("device_provider")
            binding_status = binding.get("status")
        else:
            provider = getattr(binding, "device_provider", None)
            binding_status = getattr(binding, "status", None)

        if not provider:
            raise BotServiceError(
                f"Bot {bot_id} binding {binding_id} missing device_provider; "
                "restart aborted to avoid creation rollout migration"
            )

        return str(provider), str(binding_status) if binding_status else None

    def _resolve_historical_unbound_restart_provider(
        self,
        *,
        bot_id: str,
        entity_id: str,
        entity_type: str,
        env: str,
    ) -> str | None:
        """Return a safe provider for a failed bot whose current binding is lost."""
        page = 1
        page_size = 100
        while True:
            total, bindings = self._device_binding_repo.list_bindings(
                env=env,
                entity_id=entity_id,
                entity_type=entity_type,
                status=None,
                page=page,
                page_size=page_size,
            )
            for binding in bindings:
                if (binding.device_props or {}).get("bolt_id") != bot_id:
                    continue

                status = str(binding.status or "").upper()
                if status not in {
                    DeviceBindingStatus.RELEASED.value,
                    DeviceBindingStatus.FAILED.value,
                    DeviceBindingStatus.STOPPED.value,
                }:
                    logger.warning(
                        "[bot_service.restart_bot] reject unbound recovery with live historical binding: "
                        "bot_id=%s binding_id=%s binding_status=%s",
                        bot_id,
                        binding.id,
                        status or "UNKNOWN",
                    )
                    return None

                provider = str(binding.device_provider or "")
                if provider:
                    logger.info(
                        "[bot_service.restart_bot] recover failed unbound bot from historical provider: "
                        "bot_id=%s binding_id=%s provider=%s binding_status=%s",
                        bot_id,
                        binding.id,
                        provider,
                        status,
                    )
                    return provider
                return None

            if not bindings or page * page_size >= total:
                return None
            page += 1

    @staticmethod
    def _activation_in_progress_result(bot: Dict[str, Any]) -> Dict[str, Any]:
        """Return an idempotent response without mutating lifecycle state."""
        current = dict(bot)
        current["restart_in_progress"] = True
        current["message"] = "Bot activation is in progress"
        return current

    def is_teclaw_bot(self, active_engine: Optional[str]) -> bool:
        """Whether a bot with this engine runs in a teclaw container.

        Delegates to the canonical teclaw definition on
        :class:`TeclawProvisionService`, so callers don't scatter
        ``active_engine == "teclaw"`` string checks.
        """
        return self._teclaw_provision_provider().is_teclaw(active_engine)

    def start_bot(
        self,
        bot_id: str,
        user_id: str,
        nick_name: Optional[str] = None,
        force_nas: bool = False,
        device_provider: Optional[str] = None,
        restart_lock_key: Optional[Tuple[str, str, str, str]] = None,
        bot_ext_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Start a bot by triggering async device allocation.

        Args:
            bot_id: Bot ID
            user_id: User ID (must be the owner)
            nick_name: Nick name for device allocation (optional, defaults to user_id)
            force_nas: If True, force NAS storage path (used during OSS→NAS migration)
            device_provider: Explicit device_provider fact to preserve for an
                existing bot. Used by restart; normal create/start leaves it
                empty so creation rollout can decide.
            restart_lock_key: Restart idempotency lock identity to hand off to
                the async allocation thread (set only by the restart flow).

        Returns:
            Updated bot record with PENDING status (device allocation in progress)

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If start fails
        """
        if not user_id:
            raise BotServiceError("User ID is required for starting bot")

        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        entity_id = bot.get("entity_id")
        entity_type = bot.get("entity_type", "staff")
        engine_types = bot.get("engine_types", _get_engine_types())
        active_engine = bot.get("active_engine", DEFAULT_ENGINE_TYPE)
        bot_type = bot.get("bot_type") or "personal"
        template_type = bot.get("template_type")
        resolved_nick_name = nick_name or user_id
        bot_owner_id = bot.get("owner_id") or user_id

        try:
            resolved_template_config = self._template_service.get_template_config(bot_id)
        except Exception as e:
            logger.warning(
                f"[bot_service.start_bot] Failed to get template config for bot {bot_id}: {e}"
            )
            resolved_template_config = None

        # 启动前先在 BCN 注册为 Provider bot (下行链路).
        # 触发条件:
        #   - active_engine == "claude_code" 且 template_type == "normalCC"
        #   - active_engine == "claude_code" 且 template_type == "personalCoding"
        #   - active_engine == "aicoding" 且 template_type == "personalCoding"
        #   - active_engine == "teclaw" (所有 bot_type)
        #   - active_engine == "openclaw" 且 bot_type == "service"
        # 失败不阻塞主流程, 与 _sync_bot_to_bcn 一致.
        # 排查日志关键字: [bot_service._register_bot_to_bcn_as_provider]
        should_register_bcn = self._should_register_bcn_provider(
            active_engine=active_engine,
            bot_type=bot_type,
            template_type=template_type,
            template_config=resolved_template_config,
        )
        if should_register_bcn:
            logger.info(
                f"[bot_service.start_bot] register bot to BCN as provider: "
                f"bot_id={bot_id} active_engine={active_engine} "
                f"bot_type={bot_type} template_type={template_type}"
            )
            self._register_bot_to_bcn_as_provider(
                bot_id=bot_id,
                user_id=user_id,
                owner_workno=bot_owner_id,
                bot_name=bot.get("bot_name") or bot_id,
                bot_summary=bot.get("bot_desc") or "",
            )
        else:
            logger.info(
                f"[bot_service.start_bot] skip BCN provider registration: "
                f"bot_id={bot_id} active_engine={active_engine} "
                f"bot_type={bot_type} template_type={template_type}"
            )

        logger.info(
            f"[bot_service.start_bot] start requested: bot_id={bot_id}, "
            f"user_id={user_id}, active_engine={active_engine}, bot_type={bot_type}, "
            f"template_type={template_type}, "
            f"explicit_device_provider={device_provider or '<empty>'}, force_nas={force_nas}"
        )

        # Trigger async device allocation
        self._allocate_device_async(
            bot_id=bot_id,
            user_id=user_id,
            nick_name=resolved_nick_name,
            entity_id=entity_id,
            entity_type=entity_type,
            engine_types=engine_types,
            bot_name=bot.get("bot_name"),
            active_engine=active_engine,
            owner_id=bot_owner_id,
            force_nas=force_nas,
            device_provider=device_provider,
            restart_lock_key=restart_lock_key,
            bot_ext_override=bot_ext_override,
        )

        # The allocation thread is now spawned and (for the restart flow) owns
        # releasing the lock. Do NOT raise past this point: a post-spawn failure
        # would let the caller's guard release the lock while the thread is still
        # allocating, opening a window for a concurrent restart. Any failure of
        # the re-read (empty result OR exception) falls back to the bot we
        # already loaded — it clearly exists (we read it above and just
        # triggered allocation).
        try:
            updated_bot = self._repository.get_by_id_and_owner(bot_id, user_id)
            if not updated_bot:
                raise BotNotFoundError(f"Bot not found on re-read: {bot_id}")
        except Exception as reread_err:
            logger.warning(
                f"[bot_service.start_bot] Bot {bot_id} re-read failed after allocation "
                f"was triggered ({reread_err}); returning the pre-allocation snapshot."
            )
            updated_bot = dict(bot)
            updated_bot["status"] = "PENDING"
        updated_bot["engine_types"] = engine_types
        logger.info(f"[bot_service.start_bot] Bot {bot_id} start initiated, device allocation in progress")
        return updated_bot

    def restart_bot(
        self,
        bot_id: str,
        user_id: str,
        nick_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Restart a bot by releasing current device and allocating a new one.

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check (must be the owner)
            nick_name: Nick name for device allocation (optional, defaults to user_id)

        Returns:
            Updated bot record with PENDING status (device allocation in progress)

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If restart fails
        """
        if not user_id:
            raise BotServiceError("User ID is required for restarting bot")

        # Look up the bot first to derive the lock key and fail fast if missing.
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        if self.is_teclaw_bot(bot.get("active_engine")):
            logger.warning(
                "[bot_service.restart_bot] reject restart for teclaw bot: "
                "bot_id=%s user_id=%s",
                bot_id,
                user_id,
            )
            raise BotOperationNotAllowedError("teclaw 类型的 Bot 不支持重启")

        if bot.get("bot_type") == "desktop":
            raise BotServiceError(
                f"Desktop bot {bot_id} cannot be stopped via BotService.stop_bot, "
                "use DesktopBotService instead"
            )

        bot_status = str(bot.get("status") or "").upper()
        # REACTIVATING is an explicit lifecycle operation. PENDING is not:
        # failed startup reporting can strand a bot there indefinitely, so a
        # restart request must be allowed to reach the durable restart lock
        # below instead of being treated as proof that work is still running.
        if bot_status == "REACTIVATING":
            logger.info(
                "[bot_service.restart_bot] skip restart while activation is in progress: "
                "bot_id=%s user_id=%s bot_status=%s",
                bot_id,
                user_id,
                bot_status,
            )
            return self._activation_in_progress_result(bot)

        if bot_status not in {"ACTIVE", "FAILED", "PENDING"}:
            logger.warning(
                "[bot_service.restart_bot] reject restart for invalid lifecycle state: "
                "bot_id=%s user_id=%s bot_status=%s",
                bot_id,
                user_id,
                bot_status,
            )
            raise BotInvalidLifecycleStateError(
                bot_id=bot_id,
                current_status=bot_status or "UNKNOWN",
            )

        env = get_current_env()
        entity_id = bot.get("entity_id")
        if not entity_id:
            # entity_id is part of the lock key (and required for allocation);
            # a missing one would poison the key, so refuse rather than guard
            # against the wrong scope.
            raise BotServiceError(f"Bot {bot_id} has no entity_id; cannot restart")

        # Restart normally preserves its provider. Existing ARCA bots opt in to
        # BaaS only when the same database-backed template override used during
        # creation positively matches their owner; every other outcome keeps the
        # legacy provider behavior. An ACTIVE bot without a binding retains the
        # legacy fallback to normal allocation. FAILED bots require a trustworthy
        # historical provider because their current binding may be lost.
        current_device_provider = None
        binding_id = bot.get("binding_id")
        recovered_without_binding = False
        if binding_id:
            current_device_provider, binding_status = (
                self._resolve_current_device_restart_context(
                    bot_id=bot_id,
                    binding_id=binding_id,
                )
            )
            # An ACTIVE bot with a PENDING binding is still converging. When
            # both records are PENDING, however, allow the explicit restart to
            # recover the stranded lifecycle through the lock-protected path.
            if (
                binding_status == DeviceBindingStatus.PENDING.value
                and bot_status != "PENDING"
            ):
                logger.info(
                    "[bot_service.restart_bot] skip restart while binding is pending: "
                    "bot_id=%s user_id=%s binding_id=%s",
                    bot_id,
                    user_id,
                    binding_id,
                )
                current = self._activation_in_progress_result(bot)
                current["status"] = "PENDING"
                return current
            if binding_status and binding_status not in {
                DeviceBindingStatus.ACTIVE.value,
                DeviceBindingStatus.PENDING.value,
                DeviceBindingStatus.FAILED.value,
                DeviceBindingStatus.STOPPED.value,
            }:
                logger.warning(
                    "[bot_service.restart_bot] reject restart for invalid binding state: "
                    "bot_id=%s user_id=%s binding_id=%s binding_status=%s",
                    bot_id,
                    user_id,
                    binding_id,
                    binding_status,
                )
                raise BotInvalidLifecycleStateError(
                    bot_id=bot_id,
                    current_status=f"BINDING_{binding_status}",
                )
            logger.info(
                f"[bot_service.restart_bot] preserve device_provider before restart: "
                f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}, "
                f"device_provider={current_device_provider}"
            )
        elif bot_status == "FAILED":
            current_device_provider = self._resolve_historical_unbound_restart_provider(
                bot_id=bot_id,
                entity_id=str(entity_id),
                entity_type=bot.get("entity_type", "staff"),
                env=env,
            )
            if not current_device_provider:
                logger.warning(
                    "[bot_service.restart_bot] reject failed bot without recoverable binding: "
                    "bot_id=%s user_id=%s",
                    bot_id,
                    user_id,
                )
                raise BotInvalidLifecycleStateError(
                    bot_id=bot_id,
                    current_status="FAILED_WITHOUT_BINDING",
                )
            recovered_without_binding = True
        else:
            logger.info(
                f"[bot_service.restart_bot] no binding provider to preserve: "
                f"bot_id={bot_id}, user_id={user_id}"
            )

        restart_target_provider = self._resolve_restart_target_provider(
            bot_id=bot_id,
            user_id=user_id,
            bot=bot,
            source_provider=current_device_provider,
        )

        # Idempotency guard: acquire the per-bot restart lock. If a restart is
        # already in progress, suppress this duplicate and return the current
        # in-progress bot — the frontend is already polling on PENDING, so this
        # behaves identically to the first click (no duplicate sandbox/binding).
        lock = self._try_acquire_restart_lock(env, entity_id, bot_id, user_id)
        if lock is None:
            logger.info(
                "[bot_service.restart_bot] Restart already in progress for bot %s "
                "(env=%s, entity_id=%s); suppressing duplicate request.",
                bot_id, env, entity_id,
            )
            # We were suppressed because a restart is already in flight. Return
            # the in-progress shape the first request's stop_bot produces
            # (PENDING, binding cleared) so the client polls correctly.
            #
            # We apply this reset to the RETURNED record only — we do NOT
            # persist it. There is a window where the in-flight restart raced
            # ahead of stop_bot (so the DB still reads ACTIVE), and a window
            # where it already finished (ACTIVE + live binding) but hasn't
            # released the lock yet; persisting here would either be redundant
            # or clobber a live binding. Shaping only the response is safe.
            #
            # The "correct" fix is to make acquire-lock + set-PENDING atomic,
            # but this service has no multi-statement transaction support today
            # (prod runs at AUTOCOMMIT and each repo opens its own connection).
            # The service is broadly in a poor state re: transactional
            # consistency — revisit this once a transaction scope exists.
            current = dict(self._repository.get_by_id_and_owner(bot_id, user_id) or bot)
            current["status"] = "PENDING"
            current["binding_id"] = None
            current["device_id"] = None
            return current

        # Lock held. The async allocation thread is the authoritative releaser
        # (see _allocate_device_async). ``handed_off`` flips True once start_bot
        # has spawned that thread; until then this method's finally releases the
        # lock so a synchronous failure can't orphan it.
        lock_key = (env, entity_id, bot_id, lock.lock_token)
        handed_off = False
        try:
            if bot.get("bot_type") == "service" and not self.is_teclaw_bot(
                bot.get("active_engine")
            ):
                bot = self._mark_service_bot_default_image(bot)
            if (
                binding_id
                and current_device_provider == BAAS_DEVICE_PROVIDER
                and self._baas_service_provider is not None
            ):
                # BaaS 原地重启：不 destroy、不 release binding，bot_uuid/device_uuid 不变,
                # session NAS 目录复用，历史 session 留存。arca 永不进此支(provider 取自 binding 表)。
                # 同步完成、无 async 接管，handed_off 保持 False → finally 当场释放锁。
                updated_bot = self._restart_bot_baas(
                    bot_id=bot_id, user_id=user_id, binding_id=binding_id, bot=bot,
                )
                logger.info(f"[bot_service.restart_bot] Bot {bot_id} in-place restart via BaaS, binding preserved")
                return updated_bot
            release_ok = True
            if not recovered_without_binding:
                release_ok = self.stop_bot(
                    bot_id=bot_id,
                    user_id=user_id,
                    nick_name=nick_name,
                    release_reason=f"Bot {bot_id} restarted",
                )
            else:
                # There is no current binding to release, but the asynchronous
                # allocation can take time. Publish PENDING before spawning it
                # so status polling does not continue to report the old FAILED
                # state while recovery is underway.
                self._repository.update_by_owner(
                    bot_id,
                    user_id,
                    {
                        "status": "PENDING",
                        "binding_id": None,
                        "device_id": None,
                    },
                )
            updated_bot = self.start_bot(
                bot_id=bot_id,
                user_id=user_id,
                nick_name=nick_name,
                device_provider=restart_target_provider,
                restart_lock_key=lock_key,
                bot_ext_override=(
                    bot.get("ext")
                    if bot.get("bot_type") == "service"
                    and not self.is_teclaw_bot(bot.get("active_engine"))
                    and (bot.get("ext") or {}).get("sbot_use_default_image") is True
                    else None
                ),
            )
            handed_off = True

            if not release_ok:
                logger.warning(f"[bot_service.restart_bot] Bot {bot_id} restart initiated with warnings: old device may not be released properly")
                updated_bot["restart_warning"] = "旧设备释放可能失败，如后续出现设备限制问题，请联系管理员"
            else:
                logger.info(f"[bot_service.restart_bot] Bot {bot_id} restart initiated, device allocation in progress")
            return updated_bot

        except BotNotFoundError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.restart_bot] Failed to restart bot {bot_id}: {e}")
            raise BotServiceError(f"Failed to restart bot: {e}")
        finally:
            # Only release here if the async thread never took ownership (i.e. a
            # synchronous failure before allocation was spawned). Otherwise the
            # thread's finally is responsible for the release. The token ensures
            # we only delete the row we acquired.
            if not handed_off:
                self._restart_lock_repo.release(env, entity_id, bot_id, lock.lock_token)

    def _restart_bot_baas(self, *, bot_id: str, user_id: str, binding_id: int, bot: Dict[str, Any]) -> Dict[str, Any]:
        """BaaS 原地重启：调 BaasService.upgrade_bot（走 /update）不 destroy、不 release binding。

        bot_uuid/device_uuid 不变 → session NAS 目录复用 → 历史 session 留存。
        binding/device_id 保持不动，返回当前 bot 记录。
        普通 restart 不传 migration_path，cmd 不拼 --source_dir；发布态迁移源
        只在 service bot 发布流程里传入。
        """
        binding = self._device_service_provider().get_device(binding_id=binding_id)
        bot_uuid = (
            binding.get("device_id") if isinstance(binding, dict)
            else getattr(binding, "device_id", None)
        )
        if not bot_uuid:
            raise BotServiceError(f"Bot {bot_id} binding {binding_id} missing bot_uuid; cannot baas restart")

        from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
            RESTART_IMAGE_POLICY_ON_SUCCESS_KEY,
            RESTART_REQUEST_ID_KEY,
            RESTART_WORKFLOW_BASELINE_KEY,
        )

        raw_binding_props = (
            binding.get("device_props", {})
            if isinstance(binding, dict)
            else (getattr(binding, "device_props", None) or {})
        )
        binding_props = raw_binding_props if isinstance(raw_binding_props, dict) else {}
        restart_request_id = binding_props.get(RESTART_REQUEST_ID_KEY)
        restart_workflow_baseline = binding_props.get(RESTART_WORKFLOW_BASELINE_KEY)
        has_durable_recovery_intent = (
            isinstance(restart_request_id, str)
            and bool(restart_request_id)
            and isinstance(restart_workflow_baseline, int)
            and not isinstance(restart_workflow_baseline, bool)
            and restart_workflow_baseline >= 0
        )
        if has_durable_recovery_intent:
            logger.info(
                "[bot_service._restart_bot_baas] restart already has a durable "
                "recovery intent: bot_id=%s binding_id=%s request_id=%s baseline=%s",
                bot_id,
                binding_id,
                restart_request_id,
                restart_workflow_baseline,
            )
            return dict(self._repository.get_by_id_and_owner(bot_id, user_id) or bot)
        if restart_request_id:
            logger.warning(
                "[bot_service._restart_bot_baas] ignoring legacy restart intent "
                "without a valid workflow baseline: bot_id=%s binding_id=%s "
                "request_id=%s baseline=%r",
                bot_id,
                binding_id,
                restart_request_id,
                restart_workflow_baseline,
            )

        active_engine = (bot.get("active_engine") or "").strip()
        bot_type = bot.get("bot_type") or "personal"
        bot_template_type = (bot.get("template_type") or "").strip()

        # 先读取模板快照：BCN 能力门控与后续 BaaS restart 均使用同一份 resolved config。
        try:
            resolved_template_config = self._template_service.get_template_config(bot_id)
        except Exception as e:
            logger.warning(
                "[bot_service._restart_bot_baas] Failed to get template for bot %s: %s",
                bot_id, e,
            )
            resolved_template_config = None

        # BaaS 原地重启不会经过 start_bot，这里补齐启动链路的 BCN Provider 注册。
        # 注册接口幂等：已注册时直接返回，也能重试创建阶段失败的注册。
        if self._should_register_bcn_provider(
            active_engine=active_engine,
            bot_type=bot_type,
            template_type=bot_template_type,
            template_config=resolved_template_config,
        ):
            logger.info(
                "[bot_service._restart_bot_baas] register bot to BCN as provider: "
                "bot_id=%s active_engine=%s bot_type=%s template_type=%s",
                bot_id,
                active_engine,
                bot_type,
                bot_template_type,
            )
            self._register_bot_to_bcn_as_provider(
                bot_id=bot_id,
                user_id=user_id,
                owner_workno=bot.get("owner_id") or user_id,
                bot_name=bot.get("bot_name") or bot_id,
                bot_summary=bot.get("bot_desc") or "",
            )

        # 普通 restart 入口只重启当前 bot，不使用发布态 build 产物目录。
        # 发布态 verify/online 的重启由 PublishFlowService.restart_bot(publish_id) 处理。
        mig: Optional[str] = None
        restart_stage = (
            PublishStage.DRAFT.value
            if bot_type == "service"
            else None
        )

        # resolved_template_config 已在 BCN 能力门控前读取，后续 BaaS restart 复用同一快照。
        # 与 _allocate_device_async（create / arca-restart 路径）同口径构造
        # extra_envs，并独立透传 template_config。extra_envs 提供引擎策略
        # 变量（BOT_TYPE / RELAY_DEFAULT_* / AIX_DEVFLOW_INFO / GIT_ADDRESSES），
        # template_config 提供沙箱覆写（envs / image / resource_spec）。两者
        # 不能互相门控。
        extra_envs: Optional[Dict[str, Any]] = self._build_engine_extra_envs(
            bot_id=str(bot_id),
            owner_id=user_id,
            active_engine=active_engine,
            bot_type=bot.get("bot_type", ""),
            template_type=bot_template_type,
            template_config=resolved_template_config,
            log_context="bot_service._restart_bot_baas",
        )
        # 与 _allocate_device_async 对齐：BaaS 原地重启也必须透传模板快照。
        # template_config.envs / image / resource_spec 是独立的沙箱覆写能力，
        # 不能被 extra_envs（引擎策略环境变量）是否命中门控影响。否则非
        # coding 模板或仅配置 envs/image/spec 的模板在 restart -> /update 时会
        # 退化成默认 envs，丢失创建 Bot 时使用的沙箱覆写。
        try:
            device_template_config = self._attach_template_uid_context(
                bot_id=str(bot_id),
                user_id=user_id,
                bot_type=bot.get("bot_type", ""),
                engine_type=active_engine,
                template_type=bot_template_type,
                template_config=resolved_template_config,
            )
        except Exception as e:
            logger.warning(
                "[bot_service._restart_bot_baas] Failed to attach template uid context for bot %s: %s",
                bot_id, e,
            )
            device_template_config = resolved_template_config
        device_template_config = overlay_image_pin_on_template_config(
            device_template_config,
            bot.get("ext"),
        )

        import uuid as _uuid
        request_id = _uuid.uuid4().hex
        template_uuid = self._resolve_baas_restart_template_uuid(
            bot_id=bot_id,
            user_id=user_id,
            bot=bot,
            template_config=resolved_template_config,
        )
        upgrade_kwargs = {
            "bot_uuid": bot_uuid,
            "bot": bot,
            "owner_id": user_id,
            "request_id": request_id,
            "migration_path": mig,
            # 个人 Bot / 服务 Bot 草稿的普通重启不走发布产物迁移，但仍按 NAS home 目录运行。
            "mount_home_dir_storage": True,
            # extra_envs 可能因引擎策略门控为 None；template_config 仍需透传，
            # 以保留创建 Bot 时使用的 envs / image / resource_spec 沙箱覆写。
            "extra_envs": extra_envs,
            "template_config": device_template_config,
        }
        if restart_stage is not None:
            upgrade_kwargs["stage"] = restart_stage
        if template_uuid is not None:
            upgrade_kwargs["template_uuid"] = template_uuid
        image_policy_on_success = (
            DEFAULT_IMAGE_POLICY_VALUE
            if bot_type == "service"
            and not self.is_teclaw_bot(active_engine)
            and (bot.get("ext") or {}).get("sbot_use_default_image") is True
            else None
        )
        baas_service = self._baas_service_provider()
        try:
            workflows = baas_service.list_bot_publishes(bot_uuid)
            workflow_baseline = max(
                (
                    int(workflow["id"])
                    for workflow in (workflows or [])
                    if isinstance(workflow, dict)
                    and str(workflow.get("id", "")).isdigit()
                ),
                default=0,
            )
        except Exception as e:
            raise BotServiceError(
                f"Failed to snapshot BaaS restart workflow baseline: {e}"
            ) from e

        from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
            BAAS_RESTART_PUBLISH_POLL_TASK,
            build_restart_publish_poll_payload,
        )

        # The durable task is the operation intent and must exist before the
        # external BaaS mutation. If enqueue fails, no remote side effect has
        # happened. The handler can adopt the workflow by baseline if the
        # process exits after BaaS accepts but before publish_id is persisted.
        task_queue_service = self._task_queue_service
        if task_queue_service is None:
            raise BotServiceError("BaaS restart task queue service is unavailable")
        started_at_epoch_s = time.time()
        try:
            task_queue_service.enqueue(
                BAAS_RESTART_PUBLISH_POLL_TASK,
                build_restart_publish_poll_payload(
                    binding_id=binding_id,
                    bot_id=bot_id,
                    owner_id=user_id,
                    publish_id=None,
                    started_at_epoch_s=started_at_epoch_s,
                    bot_uuid=bot_uuid,
                    image_policy_on_success=image_policy_on_success,
                    request_id=request_id,
                    workflow_baseline=workflow_baseline,
                ),
                deadline_seconds=86400,
                delay_seconds=2,
            )
        except Exception as e:
            raise BotServiceError(
                "BaaS restart was not submitted because its durable task "
                f"could not be persisted: {e}"
            ) from e

        previous_binding_status = (
            binding.get("status")
            if isinstance(binding, dict)
            else getattr(binding, "status", DeviceBindingStatus.ACTIVE.value)
        )
        previous_bot_status = bot.get("status") or DeviceBindingStatus.ACTIVE.value
        try:
            self._device_binding_repo.update_device_props(
                binding_id=binding_id,
                props={
                    RESTART_REQUEST_ID_KEY: request_id,
                    RESTART_WORKFLOW_BASELINE_KEY: workflow_baseline,
                    "restart_publish_id": None,
                    RESTART_IMAGE_POLICY_ON_SUCCESS_KEY: image_policy_on_success,
                },
            )
            self._device_binding_repo.update_status(
                binding_id=binding_id, status=DeviceBindingStatus.PENDING
            )
            if self._repository.update_by_owner(
                bot_id, user_id, {"status": DeviceBindingStatus.PENDING.value}
            ) is None:
                raise BotServiceError(f"Bot not found while preparing restart: {bot_id}")
        except Exception as e:
            # No BaaS call has happened yet. Invalidate the queued task's request
            # identity and restore the previous visible lifecycle state.
            try:
                self._device_binding_repo.update_device_props(
                    binding_id=binding_id,
                    props={
                        RESTART_REQUEST_ID_KEY: None,
                        RESTART_WORKFLOW_BASELINE_KEY: None,
                        RESTART_IMAGE_POLICY_ON_SUCCESS_KEY: None,
                    },
                )
                self._device_binding_repo.update_status(
                    binding_id=binding_id,
                    status=previous_binding_status or DeviceBindingStatus.ACTIVE.value,
                )
                self._repository.update_by_owner(
                    bot_id, user_id, {"status": previous_bot_status}
                )
            except Exception:
                logger.exception(
                    "[bot_service._restart_bot_baas] failed to roll back restart preparation"
                )
            raise BotServiceError(f"Failed to prepare durable BaaS restart: {e}") from e

        # From this point on, every ambiguous failure is recoverable by the
        # pre-existing task. It either reads the stored publish id or adopts the
        # single workflow issued after workflow_baseline.
        result = baas_service.upgrade_bot(**upgrade_kwargs)
        publish_id = (result or {}).get("publish_id") if isinstance(result, dict) else None
        if publish_id is not None:
            restart_publish_id = str(publish_id)
            try:
                self._device_binding_repo.update_device_props(
                    binding_id=binding_id,
                    props={
                        "publish_id": restart_publish_id,
                        "restart_publish_id": restart_publish_id,
                    },
                )
            except Exception:
                logger.exception(
                    "[bot_service._restart_bot_baas] publish_id persistence failed; "
                    "durable task will adopt by baseline: bot_id=%s publish_id=%s",
                    bot_id,
                    publish_id,
                )

        return dict(self._repository.get_by_id_and_owner(bot_id, user_id) or {})

    def prepare_bot_backup(
        self,
        source_bot_id: str,
        bot_id_with_version: str,
        owner_id: str,
    ) -> Path:
        """
        准备bot备份：从source_bot_dir复制到version_bot_dir。

        该方法用于上层在发布前预先准备版本目录，复制源bot数据到版本目录。
        使用 rsync 优化 NAS 场景下的复制性能。

        Args:
            source_bot_id: 原bot ID（源bot）
            bot_id_with_version: 带版本号的bot ID（如 default_v001）
            owner_id: 原bot的owner_id

        Returns:
            Path: 版本目录路径 version_bot_dir

        Raises:
            BotNotFoundError: 如果原bot不存在
            BotInstanceCreationError: 如果复制失败
        """
        from agentclaw.community.core.workspace.path_factory import get_bot_dir

        logger.info(f"[bot_service.prepare_bot_backup] Starting: source_bot_id={source_bot_id}, "
                    f"bot_id_with_version={bot_id_with_version}, owner_id={owner_id}")

        try:
            # Step 1: 获取原bot信息，校验权限
            source_bot = self._repository.get_by_id_and_owner(source_bot_id, owner_id)
            if not source_bot:
                raise BotNotFoundError(f"Source bot not found: {source_bot_id}")

            entity_id = source_bot.get("entity_id")
            entity_type = source_bot.get("entity_type", "staff")

            if not entity_id:
                raise BotServiceError(f"Source bot {source_bot_id} has no associated entity_id")

            source_bot_dir = get_bot_dir(entity_id, source_bot_id, entity_type)
            version_bot_dir = get_bot_dir(entity_id, bot_id_with_version, entity_type)

            # Step 2: source -> version 复制
            if source_bot_dir.exists():
                logger.info(f"[bot_service.prepare_bot_backup] Syncing to version dir: {source_bot_dir} -> {version_bot_dir}")
                _copy_tree_fast(source_bot_dir, version_bot_dir, symlinks=True)
                logger.info(f"[bot_service.prepare_bot_backup] Version dir ready: {version_bot_dir}")
            else:
                logger.warning(f"[bot_service.prepare_bot_backup] Source directory does not exist: {source_bot_dir}")

            return version_bot_dir

        except BotNotFoundError:
            raise
        except BotServiceError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.prepare_bot_backup] Unexpected error: {e}")
            raise BotInstanceCreationError(f"准备bot备份失败: {e}")

    def create_bot_instances(
        self,
        source_bot_id: str,
        bot_id_with_version: str,
        pub_bot_id: str,
        owner_id: str,
        instance_count: int,
        operator: OperatorContext,
        copy_from_source: bool = True,
    ) -> List[Any]:
        """
        创建bot实例并申请设备。

        服务bot发布申请设备的接口。支持两种复制模式：
        1. copy_from_source=True：source_bot_dir -> version_bot_dir -> pub_bot_dir
        2. copy_from_source=False：version_bot_dir -> pub_bot_dir（跳过从source拷贝）

        注意：设备与发布单的绑定关系由调用方（如 BotPublishService）管理，
        该方法只负责申请设备，不感知发布单上下文。

        Args:
            source_bot_id: 原bot ID（源bot）
            bot_id_with_version: 带版本号的bot ID（如 default_v001）
            pub_bot_id: 发布用的bot ID（如 default_p001），设备申请时使用此ID
            owner_id: 原bot的owner_id
            instance_count: 实例数量（需要申请的设备数量）
            operator: 操作者上下文
            copy_from_source: 是否从source_bot_dir拷贝到version_bot_dir，默认为True

        Returns:
            List[DeviceBindingResponse]: 申请的设备binding对象列表

        Raises:
            BotNotFoundError: 如果原bot不存在
            BotInstanceCreationError: 如果创建实例失败
        """
        import concurrent.futures
        from agentclaw.community.core.workspace.path_factory import get_bot_dir
        logger.info(f"[bot_service.create_bot_instances] Starting: source_bot_id={source_bot_id}, "
                   f"bot_id_with_version={bot_id_with_version}, pub_bot_id={pub_bot_id}, "
                   f"copy_from_source={copy_from_source}, owner_id={owner_id}, instance_count={instance_count}")

        try:
            # Step 1: 获取原bot信息，校验权限
            source_bot = self._repository.get_by_id_and_owner(source_bot_id, owner_id)
            if not source_bot:
                raise BotNotFoundError(f"Source bot not found: {source_bot_id}")

            entity_id = source_bot.get("entity_id")
            entity_type = source_bot.get("entity_type", "staff")
            active_engine = source_bot.get("active_engine", DEFAULT_ENGINE_TYPE)
            source_template_type = source_bot.get("template_type")
            source_bot_type = source_bot.get("bot_type", "")

            if not entity_id:
                raise BotServiceError(f"Source bot {source_bot_id} has no associated entity_id")

            source_bot_dir = get_bot_dir(entity_id, source_bot_id, entity_type)
            version_bot_dir = get_bot_dir(entity_id, bot_id_with_version, entity_type)
            pub_bot_dir = get_bot_dir(entity_id, pub_bot_id, entity_type)

            # Step 2: 处理目录复制
            if copy_from_source:
                # copy_from_source=True: 直接从 source 复制到 pub
                if not source_bot_dir.exists():
                    raise BotInstanceCreationError(f"源bot目录不存在: {source_bot_dir}")
                logger.info(f"[bot_service.create_bot_instances] Syncing source to publish dir: {source_bot_dir} -> {pub_bot_dir}")
                _copy_tree_fast(source_bot_dir, pub_bot_dir, symlinks=True)
                logger.info(f"[bot_service.create_bot_instances] Publish dir ready: {pub_bot_dir}")
            else:
                # copy_from_source=False: 从 version 复制到 pub（需要预先调用prepare_bot_backup准备好version目录）
                if not version_bot_dir.exists():
                    raise BotInstanceCreationError(f"版本目录不存在: {version_bot_dir}，请先调用prepare_bot_backup")
                logger.info(f"[bot_service.create_bot_instances] Syncing version to publish dir: {version_bot_dir} -> {pub_bot_dir}")
                _copy_tree_fast(version_bot_dir, pub_bot_dir, symlinks=True)
                logger.info(f"[bot_service.create_bot_instances] Publish dir ready: {pub_bot_dir}")

            # Step 3: 异步申请n台设备（使用pub_bot_id）
            logger.info(f"[bot_service.create_bot_instances] Applying for {instance_count} devices for pub_bot_id={pub_bot_id}")

            # DRM: 判断新建 bot 是否走 NAS
            force_nas = self._is_new_bot_use_nas()

            # 生成软链配置
            symlink_mappings: List[SynlinkMappingInfo] = []
            try:
                skill_set_service = self._skill_set_factory.create(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    bot_id=pub_bot_id,
                    engine_type=active_engine if active_engine == "claude_code" else None,
                )
                symlink_mappings = skill_set_service.get_symlink_mappings(
                    user_id=entity_id,
                    bolt_id=pub_bot_id
                )
                logger.info(f"[bot_service.create_bot_instances] Generated symlink_mappings: {len(symlink_mappings)}")
            except Exception as e:
                logger.warning(f"[bot_service.create_bot_instances] Failed to get symlink_mappings: {e}")

            device_results: list = []
            errors: List[str] = []

            # 服务型 bot 实例化时，复用 source bot 的 admin 协作者作为沙箱启动 admins
            instance_admins = self._query_admin_worknos(bot_id=pub_bot_id, owner_id=owner_id)

            # Read template_config from ac_templates (not ac_bots).
            # source_bot comes from ac_bots which has no template_config column;
            # the value lives in ac_templates.ext.  Without this lookup,
            # create_bot_instances would always pass template_config=None,
            # losing sandbox overrides (image, command, envs, resource_spec).
            instance_template_config = None
            try:
                instance_template_config = self._template_service.get_template_config(source_bot_id)
            except Exception as e:
                logger.warning(
                    "[bot_service.create_bot_instances] Failed to get template for bot %s: %s",
                    source_bot_id, e,
                )
            # 路由到具体 provider 前，先把 template_uid 上下文带给 device 层。
            # 解析失败先记下来；只有后续真正走 BaaS 时才需要 fail-fast。
            device_template_config = self._attach_template_uid_context(
                bot_id=pub_bot_id,
                user_id=owner_id,
                bot_type=source_bot_type,
                engine_type=active_engine,
                template_type=source_template_type,
                template_config=instance_template_config,
            )

            def apply_single_device(index: int) -> tuple[int, Optional[Any], Optional[str]]:
                """申请单台设备，返回(index, result, error)"""
                try:
                    service = self._device_service_provider()
                    result = service.apply_device(
                        apply_reason=f"Create bot instance: {pub_bot_id} (#{index + 1}/{instance_count})",
                        entity_id=entity_id,
                        entity_type=entity_type,
                        operator=operator,
                        bot_id=pub_bot_id,
                        engine=active_engine,
                        bot_type=source_bot_type,
                        owner_id=owner_id,  # 使用传入的 owner_id（发布单中的 owner_id）
                        symbol=symlink_mappings,
                        force_nas=force_nas,
                        admins=instance_admins,
                        template_type=source_template_type,
                        template_config=device_template_config,
                    )
                    return index, result, None
                except Exception as e:
                    logger.error(f"[bot_service.create_bot_instances] Device allocation failed for instance #{index + 1}: {e}")
                    return index, None, str(e)

            # 使用线程池并行申请设备
            # ThreadPoolExecutor threads do NOT copy context vars, so bind the
            # request's tenant onto the submitted callable (captured now, in the
            # request thread) — each worker then runs under the right tenant.
            _apply_single_device = bind_current_avernet_tenant(apply_single_device)
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(instance_count, 10)) as executor:
                # 提交所有任务
                future_to_index = {
                    executor.submit(_apply_single_device, i): i for i in range(instance_count)
                }

                # 收集结果
                for future in concurrent.futures.as_completed(future_to_index):
                    index, result, error = future.result()
                    if error:
                        errors.append(f"Instance #{index + 1}: {error}")
                    elif result:
                        device_results.append(result)

            # 检查是否有申请失败的设备
            if len(device_results) == 0:
                # 全部失败，清理已创建的目录
                logger.error(f"[bot_service.create_bot_instances] All {instance_count} device allocations failed")
                try:
                    shutil.rmtree(pub_bot_dir)
                    logger.info(f"[bot_service.create_bot_instances] Cleaned up directory: {pub_bot_dir}")
                except Exception as e:
                    logger.warning(f"[bot_service.create_bot_instances] Failed to cleanup directory: {e}")
                raise BotInstanceCreationError(f"所有设备申请都失败: {'; '.join(errors)}")

            # 部分失败，记录警告
            if len(device_results) < instance_count:
                logger.warning(
                    f"[bot_service.create_bot_instances] Partial success: "
                    f"{len(device_results)}/{instance_count} devices allocated. "
                    f"Errors: {'; '.join(errors)}"
                )

            logger.info(f"[bot_service.create_bot_instances] Successfully created {len(device_results)} instances "
                       f"for bot {pub_bot_id}")

            return device_results

        except BotNotFoundError:
            raise
        except BotServiceError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.create_bot_instances] Unexpected error: {e}")
            raise BotInstanceCreationError(f"创建bot实例失败: {e}")

    def _archive_public_approval(self, ext: Dict[str, Any], bot_id: str) -> None:
        if "public_approval" not in ext:
            return
        from datetime import datetime
        old_approval = ext.pop("public_approval")
        old_approval["processed_at"] = datetime.now().isoformat()
        history = ext.get("public_approval_history", [])
        history.append(old_approval)
        ext["public_approval_history"] = history[-5:]
        logger.info(f"[bot_service.public_bot] Moved existing approval to history: bot_id={bot_id}, old_puid={old_approval.get('puid')}")

    def _build_public_approval_context(self, bot: Dict[str, Any], operator: Optional[OperatorContext] = None) -> Dict[str, str]:
        """构建公开审批上下文信息。

        Args:
            bot: Bot 对象字典
            operator: 操作者上下文，用于 owner_name 为空时获取操作人名称

        Returns:
            包含 publishHint、botSkills、botMcps 的字典
        """
        owner_name = bot.get("owner_name") or ""
        if not owner_name and operator:
            owner_name = operator.nick_name or operator.operator_name or operator.staff_id or ""
        bot_name = bot.get("bot_name") or bot.get("bot_id") or ""
        owner_id = bot.get("owner_id") or ""
        bot_id = bot.get("bot_id") or ""
        entity_id = bot.get("entity_id") or ""
        # owner_id 就是 user_id
        user_id = owner_id
        publish_hint = f"\"{owner_name}\"同学正在发布\"{bot_name}\"服务bot至AI工作台服务市场，审批通过后平台全体成员均可基于\"{owner_name}\"权限进行服务调用。在服务发布前，请您审核：该Agent是否涉及敏感数据处理或核心业务操作；如涉及，为避免造成由于权限扩散造成的不必要风险，建议您退回并重新选择为Caller 模式后发布。"
        # 获取 bot skills 并格式化为字符串
        bot_skills_str = ""
        try:
            skill_set_service = self._skill_set_factory.create(
                entity_id=entity_id, bot_id=bot_id
            )
            skill_sets = skill_set_service.get_all_skill_sets_with_skills(user_id=user_id, bolt_id=bot_id)
            skill_parts = []
            for skill_set in skill_sets:
                # 过滤掉默认技能集
                if skill_set.get("is_default"):
                    continue
                is_active = skill_set.get("is_active", False)
                set_name = skill_set.get("name", "")
                skills = skill_set.get("skills", [])
                if skills:
                    active_marker = "[激活]" if is_active else "[未激活]"
                    skill_parts.append(f"{set_name}{active_marker}:")
                    for s in skills:
                        name = s.get("name", "")
                        if not name:
                            continue
                        # 根据 is_public 决定拼接内容（兼容 bool 和 str）
                        is_public_val = s.get("is_public")
                        is_public = is_public_val in (True, "1", 1) if is_public_val is not None else False
                        if is_public:
                            # 公开：只显示 name
                            skill_parts.append(f"  - {name}")
                        else:
                            # 私有：显示 name 和 description
                            description = s.get("description", "")
                            skill_parts.append(f"  - {name}: {description}" if description else f"  - {name}")
            bot_skills_str = "\n".join(skill_parts) if skill_parts else "无"
        except Exception as e:
            logger.warning(f"[_build_public_approval_context] Failed to get skill sets for bot {bot_id}: {e}")
            bot_skills_str = "获取失败"
        # 获取 bot mcps 并格式化为字符串
        bot_mcps_str = ""
        try:
            skill_set_service = self._skill_set_factory.create(
                entity_id=entity_id, bot_id=bot_id
            )
            mcp_sets = skill_set_service.get_all_skill_sets_with_mcps(user_id=user_id, bolt_id=bot_id)
            mcp_parts = []
            for mcp_set in mcp_sets:
                is_active = mcp_set.get("is_active", False)
                set_name = mcp_set.get("name", "")
                mcps = mcp_set.get("mcps", [])
                mcp_names = [
                    m.get("name", "")
                    for m in mcps
                    if m.get("name")
                ]
                if mcp_names:
                    active_marker = "[激活]" if is_active else "[未激活]"
                    mcp_parts.append(f"{set_name}{active_marker}:")
                    for name in mcp_names:
                        mcp_parts.append(f"  - {name}")
            bot_mcps_str = "\n".join(mcp_parts) if mcp_parts else "无"
        except Exception as e:
            logger.warning(f"[_build_public_approval_context] Failed to get mcp sets for bot {bot_id}: {e}")
            bot_mcps_str = "获取失败"
        return {"publishHint": publish_hint, "botSkills": bot_skills_str, "botMcps": bot_mcps_str}

    def release_bot_for_others(
        self,
        target_user_id: str,
        target_bot_id: str,
        caller_user_id: str,
        caller_nick_name: str,
    ) -> Dict[str, Any]:
        """
        释放目标用户的指定 bot（管理员接口）。

        复用释放设备的逻辑，更新bot状态为FAILED。

        Args:
            target_user_id: 目标用户ID
            target_bot_id: 目标bot ID
            caller_user_id: 调用者用户ID（用于权限检查）
            caller_nick_name: 调用者昵称

        Returns:
            操作结果字典

        Raises:
            BotNotFoundError: 如果目标用户没有该 bot
            BotServiceError: 如果释放失败
        """
        logger.info(f"[bot_service.release_bot_for_others] Releasing bot {target_bot_id} for target {target_user_id}, caller {caller_user_id}")

        # 检查目标用户是否有该 bot
        if not self._repository.exists_by_owner_and_bot_id(target_user_id, target_bot_id):
            raise BotNotFoundError(f"目标用户 {target_user_id} 没有 bot '{target_bot_id}'")

        # 获取目标 bot
        total, items = self._repository.list_by_owner(target_user_id, page=1, page_size=100)
        target_bot = None
        for bot in items:
            if bot.get("bot_id") == target_bot_id:
                target_bot = bot
                break

        if not target_bot:
            raise BotNotFoundError(f"无法获取目标用户 {target_user_id} 的 bot '{target_bot_id}'")

        bot_id = target_bot.get("bot_id")
        binding_id = target_bot.get("binding_id")
        bot_status = target_bot.get("status", "UNKNOWN")

        logger.info(f"[bot_service.release_bot_for_others] Found bot {bot_id} with status {bot_status}, binding_id {binding_id}")

        # 【关键】无论bot状态如何，只要有binding且不是RELEASED，都应该尝试释放设备
        if binding_id:
            service = self._device_service_provider()
            binding = service.get_device(binding_id=binding_id)
            if binding and binding.status != DeviceBindingStatus.RELEASED.value:
                # ACTIVE、PENDING、FAILED 都需要释放，只有RELEASED跳过
                operator = _compose_operator_context(caller_user_id, caller_nick_name)
                try:
                    service.release_device(
                        binding_id=binding_id,
                        release_reason=f"Admin {caller_user_id} released bot {bot_id} for user {target_user_id}",
                        operator=operator,
                    )
                    logger.info(f"[bot_service.release_bot_for_others] Released device for binding {binding_id}")
                except Exception as e:
                    logger.error(f"[bot_service.release_bot_for_others] Failed to release device for binding {binding_id}: {e}")
                    raise BotServiceError(f"释放设备失败: {e}")
            else:
                logger.info(f"[bot_service.release_bot_for_others] Binding {binding_id} already RELEASED or not found, skipping device release")

        # 如果bot已经是FAILED状态，直接返回（设备已释放，不再重复更新状态）
        if bot_status == "FAILED":
            logger.info(f"[bot_service.release_bot_for_others] Bot {bot_id} is already FAILED, device released (if any), skipping status update")
            return {
                "action": "released",
                "bot_id": bot_id,
                "status": "FAILED",
                "target_user_id": target_user_id,
                "previous_status": bot_status,
                "message": "设备已释放，Bot已是FAILED状态",
            }

        # 更新bot状态为FAILED（调用者不是bot的owner，使用update_by_owner传入target_user_id）
        try:
            update_data = {
                "status": "FAILED",
                "binding_id": None,
                "device_id": None,
                "modifier_id": caller_user_id,
            }
            # 使用update_by_owner，传入target_user_id作为owner_id
            updated_bot = self._repository.update_by_owner(bot_id, target_user_id, update_data)
            if not updated_bot:
                raise BotNotFoundError(f"Bot not found or already released: {bot_id}")

            logger.info(f"[bot_service.release_bot_for_others] Successfully released bot {bot_id} for user {target_user_id}")

            return {
                "action": "released",
                "bot_id": bot_id,
                "status": "FAILED",
                "target_user_id": target_user_id,
                "previous_status": bot_status,
                "message": "Bot已成功释放",
            }

        except BotNotFoundError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.release_bot_for_others] Failed to update bot status: {e}")
            raise BotServiceError(f"更新Bot状态失败: {e}")

    def hot_update_passport_token_to_device(self, bot_id: str, user_id: str, token: str) -> dict:
        """刷新 Bot 的 Passport Token 并热更新到运行中的设备。

        流程：
        1. 获取 bot 并校验权限
        2. 校验 token 非空
        3. 根据 bot_type 路由到对应的热更新方法
           - service: 更新草稿态 + 已发布态（线上/预发）
           - personal: 更新单个 binding
        4. 返回更新结果

        Args:
            bot_id: Bot ID
            user_id: 用户 ID（权限校验）
            token: 调用方传入的最新 token

        Returns:
            包含新 token 摘要、设备更新结果的字典

        Raises:
            BotNotFoundError: Bot 不存在
            BotServiceError: Token 为空或设备热更新失败
        """
        logger.info(f"[bot_service.refresh_passport_token] Starting refresh for bot_id={bot_id}, user_id={user_id}")

        # 1. 获取 bot 并校验权限
        # 回调走 /api 前缀 → AvernetTenantMiddleware 套 DEFAULT 租户 teamclaw,
        # 但外部租户 bot 的 passport 刷新需跨租户直查。(bot_id, owner_workno)
        # 全局唯一,跨租户直查安全。
        bot = self._repository.get_by_id_and_owner(
            bot_id,
            user_id,
            execution_options={"skip_avernet_tenant_guard": True},
        )
        if not bot:
            logger.warning(f"[bot_service.refresh_passport_token] Bot not found: bot_id={bot_id}, user_id={user_id}")
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        bot_type = bot.get("bot_type", "personal")
        logger.info(f"[bot_service.refresh_passport_token] Bot type: {bot_type}, bot_id={bot_id}, user_id={user_id}")

        # 2. 使用调用方传入的 token
        if not token:
            logger.warning(f"[bot_service.refresh_passport_token] Token is empty for bot_id={bot_id}, user_id={user_id}, bot_type={bot_type}")
            raise BotServiceError("Passport Token 为空")

        new_token = token
        token_prefix = new_token[:20]
        logger.info(
            f"[bot_service.refresh_passport_token] Got new token for "
            f"bot_id={bot_id}, user_id={user_id}, bot_type={bot_type}, has_token=yes"
        )

        # 提取 agent_code
        from agentclaw.community.core.bot_management.utils import resolve_agent_code
        agent_code = resolve_agent_code(bot=bot, passport_plugin=self._passport_plugin)

        # 3. 热更新设备 header
        try:
            if bot_type == "service":
                logger.info(
                    f"[bot_service.refresh_passport_token] Routing to service bot: "
                    f"bot_id={bot_id}, user_id={user_id}"
                )
                bindings = self._hot_update_service_bot_passport_token(
                    bot_id=bot_id, user_id=user_id, token=new_token, bot=bot,
                    agent_code=agent_code,
                )
            elif bot_type == "personal":
                logger.info(
                    f"[bot_service.refresh_passport_token] Routing to personal bot: "
                    f"bot_id={bot_id}, user_id={user_id}"
                )
                personal_result = self._hot_update_by_device_binding(
                    bot_id=bot_id, user_id=user_id, token=new_token,
                    binding_id=bot.get("binding_id"), agent_code=agent_code,
                )
                devices = personal_result.get("devices", [])
                bindings = [{
                    "binding_id": personal_result.get("binding_id"),
                    "type": "personal",
                    "device_count": len(devices),
                    "devices": devices,
                }]
            else:
                logger.warning(
                    f"[bot_service.refresh_passport_token] Skip passport token hot-update for "
                    f"unsupported bot_type={bot_type}: bot_id={bot_id}, user_id={user_id}"
                )
                return {
                    "token_prefix": token_prefix,
                    "bindings": [],
                    "skipped": True,
                    "reason": f"unsupported bot_type: {bot_type}",
                }
            logger.info(
                f"[bot_service.refresh_passport_token] Device hot-update succeeded: "
                f"bot_id={bot_id}, user_id={user_id}, bot_type={bot_type}, bindings={bindings}"
            )
        except BotServiceError:
            logger.warning(
                f"[bot_service.refresh_passport_token] Device hot-update failed: "
                f"bot_id={bot_id}, user_id={user_id}, bot_type={bot_type}"
            )
            raise
        except Exception as e:
            logger.error(
                f"[bot_service.refresh_passport_token] Device hot-update failed unexpectedly: "
                f"bot_id={bot_id}, user_id={user_id}, bot_type={bot_type}, error={e}"
            )
            raise BotServiceError(f"设备热更新失败: {e}") from e

        return {
            "token_prefix": token_prefix,
            "bindings": bindings,
        }

    def _hot_update_by_device_binding(
        self, bot_id: str, user_id: str, token: str, binding_id: int | None, agent_code: str = ""
    ) -> dict:
        """通过 device binding 热更新设备 header。

        适用于 personal bot 和 service bot 草稿态，直接通过 device_service
        获取 binding 并更新 header。

        流程：
        1. 校验 binding_id 是否存在
        2. 获取 device binding 记录
        3. 构造 AllocatedDevice
        4. 调用热更新接口更新设备 header
        """
        # ========== 步骤 1: 校验 binding_id 是否存在 ==========
        if not binding_id:
            logger.warning(
                f"[_hot_update_by_device_binding] Bot has no binding_id: "
                f"bot_id={bot_id}, user_id={user_id}"
            )
            raise BotServiceError("Bot 未绑定设备")

        # ========== 步骤 2: 获取 device binding 记录 ==========
        service = self._device_service_provider()
        binding = service.get_device(binding_id=binding_id)
        if not binding:
            logger.warning(
                f"[_hot_update_by_device_binding] Binding not found: "
                f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}"
            )
            raise BotServiceError("设备 binding 不存在")

        # ========== 步骤 3: 构造 AllocatedDevice ==========
        device = AllocatedDevice(
            device_id=binding.device_id,
            device_provider=binding.device_provider,
            device_props=binding.device_props,
        )

        # ========== 步骤 4: 调用热更新接口更新设备 header ==========
        update_result = service.update_device_headers(device=device, agent_pass_token=token, agent_code=agent_code)
        devices = update_result if isinstance(update_result, list) else []
        logger.info(
            f"[_hot_update_by_device_binding] Hot-update succeeded: "
            f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}, "
            f"device_id={binding.device_id}, updated_devices={len(devices)}"
        )
        return {"binding_id": binding_id, "devices": devices}

    def _hot_update_service_bot_passport_token(
        self, bot_id: str, user_id: str, token: str, bot: dict, agent_code: str = ""
    ) -> list[dict]:
        """Service Bot Passport Token 热更新入口。

        同时更新草稿态、已发布态和 ACTIVE caller 实例 binding（如果存在）。
        草稿态复用 `_hot_update_by_device_binding`，已发布态调用 `_hot_update_by_publish_binding`。
        caller 实例使用同一份 owner Passport token；caller 自己的
        ``x-caller-token`` 属于独立链路，不在这里处理。

        Args:
            bot_id: Bot ID
            user_id: 用户 ID
            token: 新 Passport Token
            bot: bot 字典（用于获取草稿态 binding_id）

        Returns:
            成功更新的 binding 列表，每项为
            {"binding_id": int, "type": "draft"|"online"|"verify"|"caller"}

        Raises:
            BotServiceError: 任一目标失败，或所有目标均不存在时抛出。所有
                已发现目标都会先 best-effort 尝试完毕，再聚合失败。
        """
        updated_bindings: list[dict] = []
        errors: list[str] = []

        # ========== 步骤 1: 更新草稿态 binding（和 personal bot 逻辑一致） ==========
        draft_binding_id = bot.get("binding_id")
        if draft_binding_id:
            logger.info(
                f"[_hot_update_service_bot_passport_token] Updating draft binding: "
                f"bot_id={bot_id}, user_id={user_id}, binding_id={draft_binding_id}"
            )
            try:
                draft_result = self._hot_update_by_device_binding(
                    bot_id=bot_id, user_id=user_id, token=token,
                    binding_id=draft_binding_id, agent_code=agent_code,
                )
                devices = draft_result.get("devices", [])
                updated_bindings.append({
                    "binding_id": draft_binding_id,
                    "type": "draft",
                    "device_count": len(devices),
                    "devices": devices,
                })
            except Exception as e:
                logger.warning(
                    f"[_hot_update_service_bot_passport_token] Draft binding update failed: "
                    f"bot_id={bot_id}, user_id={user_id}, binding_id={draft_binding_id}, error={e}"
                )
                errors.append(f"草稿态: {e}")
        else:
            logger.info(
                f"[_hot_update_service_bot_passport_token] No draft binding: "
                f"bot_id={bot_id}, user_id={user_id}"
            )

        # ========== 步骤 2: 更新已发布态 binding（线上 SUCCESS + 预发 VALIDATING） ==========
        env = get_current_env()
        publish_repo = self._bot_publish_repo

        _PUBLISHED_STATUS_BINDINGS = [
            (PublishStatus.SUCCESS.value, "online"),
            (PublishStatus.VALIDATING.value, "verify"),
        ]
        has_publish_record = False

        for status, binding_key in _PUBLISHED_STATUS_BINDINGS:
            try:
                publish_record = publish_repo.get_by_publish_bot_id(
                    publish_bot_id=bot_id, owner_id=user_id, env=env,
                    publish_status=status,
                )
            except Exception as e:
                logger.warning(
                    f"[_hot_update_service_bot_passport_token] {binding_key} publish query failed: "
                    f"bot_id={bot_id}, user_id={user_id}, status={status}, error={e}"
                )
                errors.append(f"{binding_key}: 发布记录查询失败: {e}")
                continue
            if not publish_record:
                continue
            has_publish_record = True

            binding_info = (publish_record.ext or {}).get("binding") or {}
            binding_id = binding_info.get(binding_key)
            if not binding_id:
                logger.error(
                    f"[_hot_update_service_bot_passport_token] Missing {binding_key} binding in {status} record: "
                    f"bot_id={bot_id}, user_id={user_id}, publish_id={publish_record.id}"
                )
                errors.append(f"{binding_key}: 发布记录中缺少 binding_id")
                continue

            logger.info(
                f"[_hot_update_service_bot_passport_token] Updating {binding_key} binding: "
                f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}"
            )
            try:
                publish_result = self._hot_update_by_publish_binding(
                    bot_id=bot_id, user_id=user_id, token=token,
                    binding_id=binding_id, agent_code=agent_code,
                )
                devices = publish_result.get("devices", [])
                updated_bindings.append({
                    "binding_id": binding_id,
                    "type": binding_key,
                    "device_count": len(devices),
                    "devices": devices,
                })
            except Exception as e:
                logger.warning(
                    f"[_hot_update_service_bot_passport_token] {binding_key} binding update failed: "
                    f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}, error={e}"
                )
                errors.append(f"{binding_key}: {e}")

        if not has_publish_record:
            logger.info(
                f"[_hot_update_service_bot_passport_token] No publish record: "
                f"bot_id={bot_id}, user_id={user_id}, env={env}"
            )

        # ========== 步骤 3: 并发更新 ACTIVE caller 实例 ==========
        try:
            caller_bindings = self._device_binding_repo.list_active_caller_instance_bindings(
                bot_id=bot_id,
                owner_id=user_id,
                env=env,
            )
        except Exception as e:
            logger.warning(
                f"[_hot_update_service_bot_passport_token] Caller binding query failed: "
                f"bot_id={bot_id}, user_id={user_id}, env={env}, error={e}"
            )
            errors.append(f"caller: binding 查询失败: {e}")
            caller_bindings = []

        if caller_bindings:
            logger.info(
                f"[_hot_update_service_bot_passport_token] Updating caller bindings: "
                f"bot_id={bot_id}, user_id={user_id}, caller_count={len(caller_bindings)}, "
                f"max_workers={_CALLER_REFRESH_MAX_WORKERS}"
            )
            update_caller = bind_current_avernet_tenant(
                self._hot_update_by_caller_binding
            )
            try:
                with ThreadPoolExecutor(
                    max_workers=_CALLER_REFRESH_MAX_WORKERS
                ) as executor:
                    future_to_binding = {
                        executor.submit(
                            update_caller,
                            bot_id=bot_id,
                            user_id=user_id,
                            token=token,
                            binding=binding,
                            agent_code=agent_code,
                        ): binding
                        for binding in caller_bindings
                    }
                    for future in as_completed(future_to_binding):
                        binding = future_to_binding[future]
                        try:
                            updated_bindings.append(future.result())
                        except Exception as e:
                            logger.warning(
                                f"[_hot_update_service_bot_passport_token] Caller binding update failed: "
                                f"bot_id={bot_id}, user_id={user_id}, binding_id={binding.id}, "
                                f"device_id={binding.device_id}, error={e}"
                            )
                            errors.append(
                                f"caller(binding_id={binding.id}): {e}"
                            )
            except Exception as e:
                logger.warning(
                    f"[_hot_update_service_bot_passport_token] Caller fan-out failed: "
                    f"bot_id={bot_id}, user_id={user_id}, error={e}"
                )
                errors.append(f"caller: 并发更新失败: {e}")
        else:
            logger.info(
                f"[_hot_update_service_bot_passport_token] No active caller binding: "
                f"bot_id={bot_id}, user_id={user_id}, env={env}"
            )

        if errors:
            error_detail = "; ".join(errors)
            raise BotServiceError(f"部分设备热更新失败: {error_detail}")

        if not updated_bindings:
            raise BotServiceError("服务 Bot 没有可用的设备 binding")

        logger.info(
            f"[_hot_update_service_bot_passport_token] Hot-update completed: "
            f"bot_id={bot_id}, user_id={user_id}, updated_bindings={updated_bindings}"
        )
        return updated_bindings

    def _hot_update_by_caller_binding(
        self,
        *,
        bot_id: str,
        user_id: str,
        token: str,
        binding: DeviceBindingRecord,
        agent_code: str = "",
    ) -> dict:
        """用 owner Passport token 更新一个 caller BaaS Bot 的 ACTIVE 设备。"""
        service = self._device_service_provider()
        device = AllocatedDevice(
            device_id=binding.device_id,
            device_provider=binding.device_provider,
            device_props={
                **(binding.device_props or {}),
                "bolt_id": bot_id,
                "entity_id": user_id,
            },
        )
        update_result = service.update_device_headers(
            device=device,
            agent_pass_token=token,
            agent_code=agent_code,
            active_only=True,
        )
        devices = update_result if isinstance(update_result, list) else []
        if not devices:
            raise BotServiceError("caller 实例没有 ACTIVE 物理设备")

        logger.info(
            f"[_hot_update_by_caller_binding] Hot-update succeeded: "
            f"bot_id={bot_id}, user_id={user_id}, binding_id={binding.id}, "
            f"device_id={binding.device_id}, updated_devices={len(devices)}"
        )
        return {
            "binding_id": binding.id,
            "type": "caller",
            "device_count": len(devices),
            "devices": devices,
        }

    def _hot_update_by_publish_binding(
        self, bot_id: str, user_id: str, token: str, binding_id: int, agent_code: str = ""
    ) -> dict:
        """通过 publish binding 热更新已发布 Service Bot 的设备 header。

        适用于 service bot 的已发布态（线上/预发）。
        只需通过 binding_id 查到 binding，取 device_id（即 BaaS bot_uuid），
        构造 AllocatedDevice 后批量更新即可。

        流程：
        1. 获取 device binding 记录
        2. 构造 AllocatedDevice
        3. 调用热更新接口
        """
        # ========== 步骤 1: 获取 device binding 记录 ==========
        binding = self._bot_publish_provider().get_device_binding_by_id(binding_id)
        if not binding or not binding.device_id:
            logger.warning(
                f"[_hot_update_by_publish_binding] Binding not found or invalid: "
                f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}"
            )
            raise BotServiceError("设备 binding 不存在或无效")

        # ========== 步骤 2: 构造 AllocatedDevice ==========
        service = self._device_service_provider()
        device = AllocatedDevice(
            device_id=binding.device_id,
            device_provider=binding.device_provider,
            device_props={
                "bolt_id": bot_id,
                "entity_id": user_id,
            },
        )

        # ========== 步骤 3: 调用热更新接口 ==========
        update_result = service.update_device_headers(device=device, agent_pass_token=token, agent_code=agent_code)
        devices = update_result if isinstance(update_result, list) else []
        logger.info(
            f"[_hot_update_by_publish_binding] Hot-update succeeded: "
            f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}, "
            f"device_id={binding.device_id}, updated_devices={len(devices)}"
        )
        return {"binding_id": binding_id, "devices": devices}

    def update_bot_ext(self, bot_id: str, user_id: str, ext_update: Dict[str, Any]) -> None:
        """局部更新 bot.ext 字段。

        Args:
            bot_id: Bot ID
            user_id: 用户 ID（用于权限检查）
            ext_update: 要更新的 ext 字段内容
        """
        bot = self.get_bot(bot_id, user_id)
        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except json.JSONDecodeError:
                ext = {}
        ext.update(ext_update)
        self._repository.update_by_owner(bot_id, user_id, {"ext": ext})
        logger.info(f"[bot_service.update_bot_ext] Updated ext for bot {bot_id}: {ext_update}")

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

    def ensure_hosted_workspace(self, bot_id: str, user_id: str) -> Optional[str]:
        """为 applicationCoding bot 确保 DIMA workspace 存在（幂等）。

        创建 bot 时若 DIMA 调用失败，bot 仍会成功落库但 template_config 缺少
        ``dima_space_id``。此方法暴露给前端做手动补救：

        1. 已有 ``dima_space_id`` → 直接返回（不重复创建）。
        2. 否则调 DIMA 创建工作空间 + 写回 template_config。

        Args:
            bot_id: Bot ID
            user_id: 操作者用户 ID（用于查询 bot；权限校验在 router 层
                由 ``CollaboratorPermissionInterceptor`` 完成）

        Returns:
            DIMA workspace ID；DIMA 调用失败时返回 None。

        Raises:
            BotNotFoundError: bot 不存在或当前用户无权访问
            BotServiceError: bot 不是 applicationCoding 模板
        """
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        template_type = bot.get("template_type")
        if template_type != "applicationCoding":
            raise BotServiceError(
                f"Bot {bot_id} 不是 applicationCoding 模板（template_type={template_type}），"
                "无法创建 DIMA 工作空间"
            )

        template_config = self._template_service.get_template_config(bot_id) or {}

        existing_id = template_config.get("dima_space_id")
        if existing_id:
            logger.info(
                "[bot_service.ensure_hosted_workspace] Bot %s already has dima_space_id=%s, skipping",
                bot_id, existing_id,
            )
            return existing_id

        bot_name = bot.get("bot_name") or bot_id
        owner_id = bot.get("owner_id") or user_id

        workspace_id = self._require_workspace_hosting().create_workspace_for_bot(
            staff_id=owner_id,
            bot_id=bot_id,
            bot_name=bot_name,
            template_config=template_config,
            raise_on_failure=True,
        )

        if not workspace_id:
            logger.warning(
                "[bot_service.ensure_hosted_workspace] DIMA create failed for bot %s",
                bot_id,
            )
            return None

        # template_config 已被 create_workspace_for_bot inline 写入 dima_space_id；
        # 用 create_or_update 兜底首次（template 记录可能尚不存在）
        try:
            self._template_service.create_or_update_template(
                bot_id=bot_id,
                template_config=template_config,
                template_type=template_type,
            )
            logger.info(
                "[bot_service.ensure_hosted_workspace] Persisted dima_space_id=%s for bot %s",
                workspace_id, bot_id,
            )
        except Exception as e:
            logger.error(
                "[bot_service.ensure_hosted_workspace] Failed to persist template_config for bot %s: %s",
                bot_id, e, exc_info=True,
            )
            # workspace 已经创建成功，持久化失败仍返回 ID 让前端可重试持久化逻辑
            # （下次调用会因 dima_space_id 不在持久化记录中而重新进入此分支）

        return workspace_id
