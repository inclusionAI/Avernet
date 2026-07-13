"""EconomyGovernanceModule — production singletons for the economy/governance subsystem.

Bindings registered here:
  - GovernanceWhitelistRepository  — batch-adds bots to the governance whitelist
  - GovernanceFeedbackService      — user feedback on governance notifications
  - GovernanceAdminService         — backend admin (pause/resume/bulk-whitelist)
  - GovernanceWhitelistService     — whitelist batch add + delete
  - TaskRecordRepository           — task_record_daily ticket lifecycle
  - GovernanceBotService           — scan-and-decision orchestrator
  - GovernanceBotLifecycle         — single-cron lifecycle participant
  - NotifySenderPlugin             — notification dispatcher (Markdown / TC card)
"""
from __future__ import annotations

import os

from agentclaw.community.api.governance_service import (
    GovernanceAdminServiceProtocol,
    GovernanceBotServiceProtocol,
    GovernanceFeedbackServiceProtocol,
    GovernanceLifecycleServiceProtocol,
    GovernanceRecordProcessProtocol,
    GovernanceWhitelistServiceProtocol,
    GovernanceWorkflowServiceProtocol,
    NotifyLifecycleServiceProtocol,
)
from agentclaw.community.core.economy.governance.domain.protocols import (
    AuditRepositoryProtocol,
    NotifyLogRepositoryProtocol,
    TaskRecordRepositoryProtocol,
    WhitelistRepositoryProtocol,
)
from agentclaw.community.core.economy.governance.lifecycle import GovernanceBotLifecycle
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
)
from agentclaw.community.core.economy.governance.services.admin_service import (
    GovernanceAdminService,
)
from agentclaw.community.core.economy.governance.services.feedback_service import (
    GovernanceFeedbackService,
)
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)
from agentclaw.community.core.economy.governance.services.notify_lifecycle_service import (
    NotifyLifecycleService,
)
from agentclaw.community.core.economy.governance.services.notify_render_service import (
    NotifyRenderService,
)
from agentclaw.community.core.economy.governance.services.record_process_service import (
    GovernanceRecordService,
)
from agentclaw.community.core.economy.governance.services.scan_service import GovernanceBotService
from agentclaw.community.core.economy.governance.services.workflow_service import (
    GovernanceWorkflowService,
)
from agentclaw.community.core.economy.governance.services.whitelist_service import (
    GovernanceWhitelistService,
)
from agentclaw.community.di.config import EconomyGovernanceConfig
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin
from agentclaw.community.utils.env_utils import get_current_env
from injector import Binder, Module, inject, provider, singleton


logger = get_logger()


# ---------------------------------------------------------------------------
# YAML user_config helpers (same pattern as config_module.py)
# ---------------------------------------------------------------------------


def _user_config() -> dict[str, object]:
    """Read ``user_config`` dict from sofa config; return ``{}`` on failure."""
    try:
        from agentclaw.community.core.config.sofa import sofa_config
        cfg = sofa_config.user_config
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _block(name: str) -> dict[str, object]:
    """Extract a named sub-dict from ``user_config``."""
    block = _user_config().get(name)
    return block if isinstance(block, dict) else {}


# Env var overrides for governance config knobs.
_ENV_NOTIFY_CHANNEL = "ECONOMY_GOVERNANCE_NOTIFY_CHANNEL"


class EconomyGovernanceModule(Module):
    """Production bindings for the economy/governance subsystem."""

    def configure(self, binder: Binder) -> None:
        # All services are now provided via @provider methods below.
        # binder.bind() was removed because auto-construction fails when
        # the service's __init__ references types imported only under
        # TYPE_CHECKING (e.g. CachePlugin) — the injector resolves
        # string annotations against the service module's globals, where
        # those names don't exist at runtime.
        # @provider methods live in *this* module, which imports all
        # required types at the top level, so resolution always succeeds.
        pass

    @singleton
    @provider
    @inject
    def _whitelist_repository(
        self, db: DatabasePlugin,
    ) -> GovernanceWhitelistRepository:
        return GovernanceWhitelistRepository(db=db)

    @singleton
    @provider
    @inject
    def _notify_log_repository(
        self, db: DatabasePlugin,
    ) -> NotifyLogRepository:
        return NotifyLogRepository(db=db)

    @singleton
    @provider
    @inject
    def _audit_repository(
        self, db: DatabasePlugin,
    ) -> GovernanceAuditRepository:
        return GovernanceAuditRepository(db=db)

    @singleton
    @provider
    @inject
    def _feedback_service(
        self,
        whitelist_service: GovernanceWhitelistService,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        task_repo: TaskRecordRepository,
        config: EconomyGovernanceConfig,
        lifecycle_service: GovernanceLifecycleService,
    ) -> GovernanceFeedbackService:
        return GovernanceFeedbackService(
            whitelist_service=whitelist_service, notify_repo=notify_repo,
            audit_repo=audit_repo, task_repo=task_repo, config=config,
            lifecycle_svc=lifecycle_service,
        )

    @singleton
    @provider
    @inject
    def _whitelist_service(
        self,
        whitelist_repo: GovernanceWhitelistRepository,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        config: EconomyGovernanceConfig,
        lifecycle_service: GovernanceLifecycleService,
    ) -> GovernanceWhitelistService:
        # Circular DI (whitelist_service ↔ lifecycle_service) is resolved by
        # injector's singleton providers at injection time — no runtime cycle.
        return GovernanceWhitelistService(
            whitelist_repo=whitelist_repo, notify_repo=notify_repo,
            audit_repo=audit_repo, config=config,
            lifecycle_svc=lifecycle_service,
        )

    @singleton
    @provider
    @inject
    def _lifecycle_service(
        self,
        task_repo: TaskRecordRepository,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
    ) -> GovernanceLifecycleService:
        """Construct GovernanceLifecycleService — sole driver of the ticket
        main state machine (Rule 14). Injected into the entry services
        (Feedback/Admin/Bot/Record/Whitelist). Deliberately has NO
        whitelist_service dependency — the whitelist-add side effect of
        accept_feedback is owned by feedback_service, and whitelist_service's
        bulk_whitelist ticket-close calls back into this driver; keeping
        whitelist_service out of this constructor breaks the DI cycle."""
        return GovernanceLifecycleService(
            task_repo=task_repo,
            notify_repo=notify_repo,
            audit_repo=audit_repo,
        )

    @singleton
    @provider
    def _notify_render_service(self) -> NotifyRenderService:
        """Construct NotifyRenderService — 通知渲染内核(收口散落三处渲染)。

        无状态、无 IO:不依赖 repo / notify_sender / config,只复用
        ``notify_builder_service`` 模块函数(直接 import)。注入到编排服务
        scan/record_process(Task 3/4),达成"渲染口径唯一"(spec A4)。
        """
        return NotifyRenderService()

    @singleton
    @provider
    @inject
    def _notify_lifecycle_service(
        self, notify_repo: NotifyLogRepository,
    ) -> NotifyLifecycleService:
        """Construct NotifyLifecycleService — 通知发送状态机正常路径唯一驱动。

        对齐工单机 GovernanceLifecycleService 收口标准:claim/mark_sent/
        mark_failed 走领域往返(领域守卫复活)。注入到 scan_service(Task 4),
        完成"正常路径单一驱动"(spec A1/A2)。
        """
        return NotifyLifecycleService(notify_repo=notify_repo)

    @singleton
    @provider
    @inject
    def _admin_service(
        self,
        cache: CachePlugin,
        whitelist_service: GovernanceWhitelistService,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        task_repo: TaskRecordRepository,
        config: EconomyGovernanceConfig,
        notify_sender: NotifySenderPlugin,
        lifecycle_service: GovernanceLifecycleService,
        render_svc: NotifyRenderService,
    ) -> GovernanceAdminService:
        return GovernanceAdminService(
            cache=cache,
            whitelist_service=whitelist_service,
            notify_repo=notify_repo, audit_repo=audit_repo,
            task_repo=task_repo,
            config=config,
            notify_sender=notify_sender,
            lifecycle_svc=lifecycle_service,
            render_svc=render_svc,
        )

    @singleton
    @provider
    @inject
    def _workflow_service(
        self,
        task_repo: TaskRecordRepository,
        audit_repo: GovernanceAuditRepository,
        config: EconomyGovernanceConfig,
        lifecycle_service: GovernanceLifecycleService,
        whitelist_service: GovernanceWhitelistService,
    ) -> GovernanceWorkflowService:
        """Construct GovernanceWorkflowService — 工单审批(从 admin 按路由边界拆出)。

        审批副作用(加白名单/关工单经状态机驱动)自带,零反向依赖 admin_service。
        workflow_router 注入 GovernanceWorkflowServiceProtocol。
        """
        return GovernanceWorkflowService(
            task_repo=task_repo,
            audit_repo=audit_repo,
            config=config,
            lifecycle_svc=lifecycle_service,
            whitelist_service=whitelist_service,
        )

    @singleton
    @provider
    @inject
    def _task_record_repository(
        self, db: DatabasePlugin,
    ) -> TaskRecordRepository:
        return TaskRecordRepository(db=db)

    @singleton
    @provider
    @inject
    def _governance_bot_service(
        self,
        task_repo: TaskRecordRepository,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        config: EconomyGovernanceConfig,
        notify_sender: NotifySenderPlugin,
        lifecycle_service: GovernanceLifecycleService,
        render_svc: NotifyRenderService,
        notify_lifecycle_service: NotifyLifecycleService,
    ) -> GovernanceBotService:
        """Construct GovernanceBotService."""
        return GovernanceBotService(
            task_repo=task_repo,
            notify_repo=notify_repo,
            audit_repo=audit_repo,
            config=config,
            notify_sender=notify_sender,
            lifecycle_svc=lifecycle_service,
            render_svc=render_svc,
            notify_lifecycle_svc=notify_lifecycle_service,
        )

    @singleton
    @provider
    @inject
    def _record_process_service(
        self,
        task_repo: TaskRecordRepository,
        whitelist_repo: GovernanceWhitelistRepository,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        config: EconomyGovernanceConfig,
        lifecycle_service: GovernanceLifecycleService,
        render_svc: NotifyRenderService,
    ) -> GovernanceRecordService:
        """Construct GovernanceRecordService."""
        return GovernanceRecordService(
            task_repo=task_repo,
            whitelist_repo=whitelist_repo,
            notify_repo=notify_repo,
            audit_repo=audit_repo,
            config=config,
            lifecycle_svc=lifecycle_service,
            render_svc=render_svc,
        )

    # -----------------------------------------------------------------
    # Protocol → concrete bindings (Rule 14 layering).
    #
    # The HTTP router (adapters/http/economy/router.py) injects the
    # ``api/governance_service`` Protocols instead of importing concrete
    # ``core/`` service classes. python-injector binds by return
    # annotation, so each Protocol needs an explicit provider that returns
    # the already-constructed concrete singleton. Without these, the
    # injector tries to auto-construct the Protocol itself and raises
    # ``TypeError: Protocols cannot be instantiated``.
    # -----------------------------------------------------------------

    @singleton
    @provider
    @inject
    def _feedback_service_protocol(
        self, svc: GovernanceFeedbackService,
    ) -> GovernanceFeedbackServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _admin_service_protocol(
        self, svc: GovernanceAdminService,
    ) -> GovernanceAdminServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _workflow_service_protocol(
        self, svc: GovernanceWorkflowService,
    ) -> GovernanceWorkflowServiceProtocol:
        """Rule 14 binding:workflow_router 注入 GovernanceWorkflowServiceProtocol
        而非具体类(对齐其他 service protocol binding)。"""
        return svc

    @singleton
    @provider
    @inject
    def _governance_bot_service_protocol(
        self, svc: GovernanceBotService,
    ) -> GovernanceBotServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _record_process_service_protocol(
        self, svc: GovernanceRecordService,
    ) -> GovernanceRecordProcessProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _whitelist_protocol(
        self, repo: GovernanceWhitelistRepository,
    ) -> WhitelistRepositoryProtocol:
        return repo

    @singleton
    @provider
    @inject
    def _whitelist_service_protocol(
        self, svc: GovernanceWhitelistService,
    ) -> GovernanceWhitelistServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _lifecycle_service_protocol(
        self, svc: GovernanceLifecycleService,
    ) -> GovernanceLifecycleServiceProtocol:
        """Rule 14 binding: router/other services inject the service Protocol
        rather than the concrete class (avoids ``Protocols cannot be
        instantiated``). Service Protocol, not a Plugin — conformance pinned
        by the contract suite + grep guard (see test_governance_lifecycle)."""
        return svc

    @singleton
    @provider
    @inject
    def _notify_lifecycle_service_protocol(
        self, svc: NotifyLifecycleService,
    ) -> NotifyLifecycleServiceProtocol:
        """Rule 14 binding: scan_service 注入 NotifyLifecycleServiceProtocol
        而非具体类(对齐 lifecycle_service_protocol)。通知发送状态机正常路径
        唯一驱动;conformance 由 test_notify_lifecycle_service 钉住。"""
        return svc

    # -----------------------------------------------------------------
    # Repository Protocol → concrete bindings (Rule 14).
    #
    # Concrete repos structurally satisfy the Protocols — no adapter
    # or # type: ignore needed.  As P4 adds command methods to the
    # concrete repos, the Protocols will be expanded to include them.
    # -----------------------------------------------------------------

    @singleton
    @provider
    @inject
    def _task_record_repo_protocol(
        self, repo: TaskRecordRepository,
    ) -> TaskRecordRepositoryProtocol:
        return repo

    @singleton
    @provider
    @inject
    def _notify_log_repo_protocol(
        self, repo: NotifyLogRepository,
    ) -> NotifyLogRepositoryProtocol:
        return repo

    @singleton
    @provider
    @inject
    def _audit_repo_protocol(
        self, repo: GovernanceAuditRepository,
    ) -> AuditRepositoryProtocol:
        return repo

    @singleton
    @provider
    @inject
    def _whitelist_repo_protocol(
        self, repo: GovernanceWhitelistRepository,
    ) -> WhitelistRepositoryProtocol:
        return repo

    @singleton
    @provider
    @inject
    def _economy_governance_config(self) -> EconomyGovernanceConfig:
        """Construct EconomyGovernanceConfig with defaults.

        YAML override under ``user_config.economy_governance``::

            economy_governance:
              dry_run: true
              skip_weekends: true
              scan_hour: 14
              scan_minute: 0
              cooldown_days: 14
              auto_silence_close_days: 7
              max_notify_per_run: 200
              auto_resolve_threshold_days: 3
              expire_days: 7
              notify_channel: tc_card
              tc_card_id: "<aix-component-id>"
              tc_card_template_id: "xxx.schema"
              iframe_callback_url: "https://..."

        Resolution priority: YAML → env var → dataclass default.
        For pre+prod shared YAML, ``_pre`` suffix fields override when
        ``SERVER_ENV`` is ``pre`` / ``prepub`` (same pattern as bcsfuse/secbaas).
        """
        defaults = EconomyGovernanceConfig()

        # --- YAML block ---
        yaml_block = _block("economy_governance")

        # dry_run: YAML → env var → default (pre + prod 同值)
        dry_run = yaml_block.get("dry_run", defaults.dry_run)
        env_dry = os.environ.get("ECONOMY_GOVERNANCE_DRY_RUN")
        if env_dry is not None:
            dry_run = env_dry.strip().lower() not in ("false", "0", "no")

        # scan_hour: YAML → env var → default
        scan_hour = int(yaml_block.get("scan_hour", defaults.scan_hour))
        env_scan = os.environ.get("ECONOMY_GOVERNANCE_SCAN_HOUR")
        if env_scan is not None:
            scan_hour = int(env_scan)

        # notify_channel: YAML → env var → default
        notify_channel = yaml_block.get("notify_channel", defaults.notify_channel)
        notify_channel = os.environ.get(_ENV_NOTIFY_CHANNEL, notify_channel)
        # Validate channel value
        if notify_channel not in ("markdown", "tc_card"):
            logger.warning(
                "[economy_governance_module] Invalid notify_channel=%r, "
                "falling back to 'markdown'",
                notify_channel,
            )
            notify_channel = "markdown"

        # tc_card_id: YAML → default
        tc_card_id = str(
            yaml_block.get("tc_card_id", defaults.tc_card_id)
        )

        # tc_card_template_id: YAML → default
        tc_card_template_id = str(
            yaml_block.get("tc_card_template_id", defaults.tc_card_template_id)
        )

        # tc_card_preview_url: YAML → default (corp env overlays supply the host)
        tc_card_preview_url = str(
            yaml_block.get("tc_card_preview_url", defaults.tc_card_preview_url)
        )

        # scan_minute: YAML → env var → default
        scan_minute = int(yaml_block.get("scan_minute", defaults.scan_minute))
        env_scan_min = os.environ.get("ECONOMY_GOVERNANCE_SCAN_MINUTE")
        if env_scan_min is not None:
            scan_minute = int(env_scan_min)

        # skip_weekends: YAML → default
        skip_weekends = bool(
            yaml_block.get("skip_weekends", defaults.skip_weekends)
        )

        # cooldown_days: YAML → default
        cooldown_days = int(
            yaml_block.get("cooldown_days", defaults.cooldown_days)
        )

        # auto_silence_close_days: YAML → default
        auto_silence_close_days = int(
            yaml_block.get("auto_silence_close_days", defaults.auto_silence_close_days)
        )

        # max_notify_per_run: YAML → default
        max_notify_per_run = int(
            yaml_block.get("max_notify_per_run", defaults.max_notify_per_run)
        )

        # auto_resolve_threshold_days: YAML → default
        auto_resolve_threshold_days = int(
            yaml_block.get(
                "auto_resolve_threshold_days",
                defaults.auto_resolve_threshold_days,
            )
        )

        # expire_days: YAML → default
        expire_days = int(
            yaml_block.get("expire_days", defaults.expire_days)
        )

        # iframe_callback_url: YAML _pre suffix → YAML base → default.
        # Card React component fetch POST target; env-aware (pre/prod differ).
        _env = get_current_env()
        _is_pre = _env in ("pre", "prepub")
        iframe_callback_url = str(yaml_block.get(
            "iframe_callback_url_pre" if _is_pre else "iframe_callback_url",
            defaults.iframe_callback_url,
        ))

        return EconomyGovernanceConfig(
            dry_run=dry_run,
            skip_weekends=skip_weekends,
            scan_hour=scan_hour,
            scan_minute=scan_minute,
            cooldown_days=cooldown_days,
            auto_silence_close_days=auto_silence_close_days,
            max_notify_per_run=max_notify_per_run,
            auto_resolve_threshold_days=auto_resolve_threshold_days,
            expire_days=expire_days,
            notify_channel=notify_channel,
            tc_card_id=tc_card_id,
            tc_card_template_id=tc_card_template_id,
            tc_card_preview_url=tc_card_preview_url,
            iframe_callback_url=iframe_callback_url,
        )

    # NOTE: the ``NotifySenderPlugin`` binding is profile-specific (corp =
    # DingTalk real delivery, community = log-only), so it is bound by a
    # per-concern column module (``infrastructure/{corp,community}/notify.py``),
    # NOT here — this base-list module must name no ``plugins.prod`` import so
    # selecting the community profile never drags the DingTalk import tree in.

    @singleton
    @provider
    @inject
    def _governance_bot_lifecycle(
        self,
        service: GovernanceBotService,
        cache: CachePlugin,
        config: EconomyGovernanceConfig,
        admin_svc: GovernanceAdminService,
    ) -> GovernanceBotLifecycle:
        """Construct GovernanceBotLifecycle — picked up by
        discover_lifecycle_participants."""
        return GovernanceBotLifecycle(
            service=service, cache=cache, config=config, admin_svc=admin_svc,
        )
