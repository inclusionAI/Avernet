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
  - GovernanceDingTalkConfig       — DingTalk credentials + TC card template
"""
from __future__ import annotations

import os

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.economy.governance.services.admin_service import (
    GovernanceAdminService,
)
from agentclaw.community.core.economy.governance.services.feedback_service import (
    GovernanceFeedbackService,
)
from agentclaw.community.core.economy.governance.services.whitelist_service import (
    GovernanceWhitelistService,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)
from agentclaw.community.core.economy.governance.domain.protocols import (
    AuditRepositoryProtocol,
    NotifyLogRepositoryProtocol,
    TaskRecordRepositoryProtocol,
    WhitelistRepositoryProtocol,
)
from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin
from agentclaw.community.api.governance_service import (
    GovernanceAdminServiceProtocol,
    GovernanceBotServiceProtocol,
    GovernanceFeedbackServiceProtocol,
    GovernanceRecordProcessProtocol,
    GovernanceWhitelistServiceProtocol,
)
from agentclaw.community.core.economy.governance.services.scan_service import GovernanceBotService
from agentclaw.community.core.economy.governance.services.record_process_service import (
    GovernanceRecordService,
)
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
)
from agentclaw.community.di.config import EconomyGovernanceConfig, GovernanceDingTalkConfig
from agentclaw.community.core.economy.governance.lifecycle import GovernanceBotLifecycle
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.database import DatabasePlugin

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
_ENV_IFRAME_CALLBACK_URL = "GOVERNANCE_IFRAME_CALLBACK_URL"
# DingTalk credentials env overrides — take precedence over the YAML ``dingtalk``
# block so real creds need never be committed (singlebox/local dev injects real
# creds via env; the shipped YAML keeps dummy values). Mirrors the
# ``ECONOMY_GOVERNANCE_*`` / ``GOVERNANCE_IFRAME_CALLBACK_URL`` override pattern.
_ENV_DINGTALK_APP_KEY = "DINGTALK_APP_KEY"
_ENV_DINGTALK_APP_SECRET = "DINGTALK_APP_SECRET"
_ENV_DINGTALK_ROBOT_CODE = "DINGTALK_ROBOT_CODE"


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
    ) -> GovernanceFeedbackService:
        return GovernanceFeedbackService(
            whitelist_service=whitelist_service, notify_repo=notify_repo,
            audit_repo=audit_repo, task_repo=task_repo, config=config,
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
    ) -> GovernanceWhitelistService:
        return GovernanceWhitelistService(
            whitelist_repo=whitelist_repo, notify_repo=notify_repo,
            audit_repo=audit_repo, config=config,
        )

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
        dingtalk_config: GovernanceDingTalkConfig,
    ) -> GovernanceAdminService:
        return GovernanceAdminService(
            cache=cache,
            whitelist_service=whitelist_service,
            notify_repo=notify_repo, audit_repo=audit_repo,
            task_repo=task_repo,
            config=config,
            notify_sender=notify_sender, dingtalk_config=dingtalk_config,
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
        admin_svc: GovernanceAdminService,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        config: EconomyGovernanceConfig,
        notify_sender: NotifySenderPlugin,
        dingtalk_config: GovernanceDingTalkConfig,
    ) -> GovernanceBotService:
        """Construct GovernanceBotService."""
        return GovernanceBotService(
            task_repo=task_repo,
            admin_svc=admin_svc,
            notify_repo=notify_repo,
            audit_repo=audit_repo,
            config=config,
            notify_sender=notify_sender,
            dingtalk_config=dingtalk_config,
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
    ) -> GovernanceRecordService:
        """Construct GovernanceRecordService."""
        return GovernanceRecordService(
            task_repo=task_repo,
            whitelist_repo=whitelist_repo,
            notify_repo=notify_repo,
            audit_repo=audit_repo,
            config=config,
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

        return EconomyGovernanceConfig(
            dry_run=dry_run,
            skip_weekends=skip_weekends,
            scan_hour=scan_hour,
            scan_minute=scan_minute,
            cooldown_days=cooldown_days,
            auto_silence_close_days=auto_silence_close_days,
            notify_channel=notify_channel,
            tc_card_id=tc_card_id,
            tc_card_template_id=tc_card_template_id,
            tc_card_preview_url=tc_card_preview_url,
        )

    @singleton
    @provider
    def _governance_dingtalk_config(self) -> GovernanceDingTalkConfig:
        """Construct DingTalk credentials from YAML ``dingtalk`` block.

        Credentials are read from ``user_config.dingtalk`` in
        ``application-<env>.yaml``, matching the BCS pattern where each
        environment's ``bcs-config-<env>.toml`` carries its own
        ``[[dingtalk_accounts]]`` in plaintext.

        For pre+prod shared YAML, ``_pre`` suffix fields override when
        ``SERVER_ENV`` is ``pre`` / ``prepub`` (same pattern as
        bcsfuse.base_url_pre / secbaas.api_base_url_pre).

        Resolution order for ``iframe_callback_url``:
          1. Env var ``GOVERNANCE_IFRAME_CALLBACK_URL``
          2. YAML ``economy_governance.iframe_callback_url``
             (prepub 环境读 ``iframe_callback_url_pre``，同 bcsfuse/secbaas _pre 后缀)

        Resolution order for DingTalk credentials (``app_key`` / ``app_secret`` /
        ``robot_code``):
          1. Env var (``DINGTALK_APP_KEY`` / ``DINGTALK_APP_SECRET`` /
             ``DINGTALK_ROBOT_CODE``) — lets singlebox/local dev inject real creds
             without committing them; the shipped YAML carries dummy values.
          2. YAML ``dingtalk`` block (``app_key_pre`` etc. for prepub env).

        All DingTalk creds empty → CommunityNotifySender (log-only, no real delivery).
        """
        from agentclaw.community.utils.env_utils import get_current_env

        yaml_block = _block("dingtalk")
        env = get_current_env()
        is_pre = env in ("pre", "prepub")

        # Env override takes precedence over YAML so real creds never ship in YAML.
        app_key = os.environ.get(_ENV_DINGTALK_APP_KEY) or str(yaml_block.get(
            "app_key_pre" if is_pre else "app_key", "",
        ))
        app_secret = os.environ.get(_ENV_DINGTALK_APP_SECRET) or str(yaml_block.get(
            "app_secret_pre" if is_pre else "app_secret", "",
        ))
        robot_code = os.environ.get(_ENV_DINGTALK_ROBOT_CODE) or str(yaml_block.get(
            "robot_code_pre" if is_pre else "robot_code", "",
        ))
        # iframe_callback_url: governance business config, read from
        # economy_governance YAML block (not dingtalk block).
        # _pre suffix for prepub env (same pattern as dingtalk credentials).
        egov_block = _block("economy_governance")
        iframe_callback_url = (
            os.environ.get(_ENV_IFRAME_CALLBACK_URL, "")
            or str(egov_block.get(
                "iframe_callback_url_pre" if is_pre else "iframe_callback_url", "",
            ))
        )

        if app_key:
            from_env = any(
                os.environ.get(v) for v in (
                    _ENV_DINGTALK_APP_KEY,
                    _ENV_DINGTALK_APP_SECRET,
                    _ENV_DINGTALK_ROBOT_CODE,
                )
            )
            logger.info(
                "[economy_governance_module] DingTalk credentials loaded "
                "(source=%s, app_key=%s***, robot_code=%s)",
                "env" if from_env else "yaml",
                app_key[:6] if len(app_key) >= 6 else app_key,
                robot_code,
            )
        else:
            logger.info(
                "[economy_governance_module] No DingTalk credentials "
                "— CommunityNotifySender (log-only) will be used",
            )

        logger.info(
            "[economy_governance_module] iframe_callback_url=%s "
            "(card React component fetch POST target)",
            iframe_callback_url,
        )

        return GovernanceDingTalkConfig(
            app_key=app_key,
            app_secret=app_secret,
            robot_code=robot_code,
            iframe_callback_url=iframe_callback_url,
        )

    # NOTE: the ``NotifySenderPlugin`` binding is profile-specific (corp =
    # DingTalk, community = no-op), so it is bound by a per-concern column module
    # (``infrastructure/{corp,community}/notify.py``), NOT here — this
    # base-list module must name no ``plugins.prod`` import so selecting the
    # community profile never drags the DingTalk import tree in (B11 Phase A).
    # The neutral ``GovernanceDingTalkConfig`` it needs is still provided above.

    @singleton
    @provider
    @inject
    def _governance_bot_lifecycle(
        self,
        service: GovernanceBotService,
        cache: CachePlugin,
        config: EconomyGovernanceConfig,
    ) -> GovernanceBotLifecycle:
        """Construct GovernanceBotLifecycle — picked up by
        discover_lifecycle_participants."""
        return GovernanceBotLifecycle(service=service, cache=cache, config=config)
