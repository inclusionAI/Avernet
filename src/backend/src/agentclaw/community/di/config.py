"""Typed configuration dataclasses.

Each dataclass corresponds to one cluster of ``user_config.get(...)``
calls in the legacy codebase. ``ConfigModule`` (Task 5) provides one
``@singleton`` ``@provider`` per type; downstream services receive the
typed object via constructor injection rather than reaching into
``sofa.sofa_config`` themselves.

The ``raw`` dict on some types is an escape hatch — there are config
clusters with sub-blocks (e.g. ``arca_sandbox.alt``) that aren't yet
worth fully typing. The hatch lets us pull the typed extraction work
forward without blocking on every nested field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Access policy ────────────────────────────────────────────────────────
#
# Corp auth/SSO/token-exchange config types (BuserviceSsoConfig,
# BuserviceTokenExchangeConfig, TokenExchangeConfig) moved to
# ``di/config_corp.py`` (B8) — they are consumed only by corp-column code.


@dataclass(frozen=True)
class WhitelistConfig:
    """Operator whitelist — frozen set of operator names allowed in.

    Sourced from the ``whitelist`` block of ``user_config``.
    """

    operator_names: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class BotChatConfig:
    """Bot-chat trace-store (Langfuse) config (the ``bot_chat`` user_config block).

    Neutral empty defaults — the community build embeds no trace-store endpoint
    or credentials. Corp env overlays set them; empty ⇒ the Langfuse-backed
    bot-chat features report unconfigured (the DB-backed path is unaffected).
    """

    langfuse_base_url: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""


@dataclass(frozen=True)
class YuqueConfig:
    """Yuque binding-verify endpoint config (the ``yuque`` user_config block).

    Neutral empty default — the community build embeds no Yuque endpoint; each
    corp env overlay sets ``user_api``. Empty ⇒ the verify endpoint returns an
    "unconfigured" response.
    """

    user_api: str = ""


@dataclass(frozen=True)
class BcnConfig:
    """BCN (Bot Coordination Network) host + provider credentials (the ``bcn``
    user_config block).

    ``base_url`` is the prod BCN host and ``base_url_pre`` overrides it when
    env=='pre'. The ``provider_*`` pairs are the claude_code down-link Provider
    credentials, keyed by env (prod / pre); only those two envs register to a real BCN.
    Neutral empty defaults — the community build embeds no BCN host or provider
    credentials. Empty host ⇒ BCN calls degrade; empty provider id / token ⇒
    ``register_provider_bot`` skips.
    """

    base_url: str = ""
    base_url_pre: str = ""
    provider_id_prod: str = ""
    provider_id_pre: str = ""
    provider_admin_token_prod: str = ""
    provider_admin_token_pre: str = ""


@dataclass(frozen=True)
class KbConfig:
    """Internal knowledge-base config for the D-TOOLS-002 diagnostic (the ``kb``
    user_config block).

    ``base_url`` + ``token`` are the KB gateway endpoint/credential;
    ``function_name`` / ``instance_name`` / ``interface_name`` name the corp KB
    service the request targets. Neutral empty defaults — the community build
    embeds no KB. Empty ``base_url`` or ``token`` ⇒ the KB query is skipped and
    the diagnostic returns no supplemental context (feature-off).
    """

    base_url: str = ""
    token: str = ""
    function_name: str = ""
    instance_name: str = ""
    interface_name: str = ""


@dataclass(frozen=True)
class LLMHarnessConfig:
    """Harness-internal LLM utility config (the ``llm`` user_config block).

    Neutral defaults empty — the neutral shipped code embeds no LLM endpoint,
    secret name, or token. Each corp env overlay sets its own ``base_url`` /
    ``secret_name``; a community deployment sets its own (or uses the
    ``LLM_BASE_URL`` / ``LLM_SECRET_NAME`` / ``LLM_AUTH_TOKEN`` env vars). Empty
    base_url = the harness LLM is disabled (feature-off).
    """

    base_url: str = ""
    secret_name: str = ""


@dataclass(frozen=True)
class SecretNamesConfig:
    """Secret-registry key *names* (not values) the app resolves via
    ``SecretResolver`` (the ``secret_names`` user_config block).

    Neutral defaults are empty — the neutral shipped code carries no corp
    secret-registry key. Each corp env overlay sets the real Mist registry
    name; a community deployment leaves them empty (the features that need
    them stay off / permissive, or fall back to the local path). This keeps
    the shipped source free of ``*_manual_*`` secret references while letting
    the name legitimately differ per deployment.
    """

    dormant_internal_token: str = ""
    aiworkbench_repo_url: str = ""


def _default_cors_origins() -> list[str]:
    """Neutral localhost CORS origins (each deploy adds its own via the yaml)."""
    return [
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:8080",
        "http://localhost:8888",
    ]


@dataclass(frozen=True)
class CorsConfig:
    """Browser CORS allow-list for the HTTP API (the ``cors`` user_config block).

    Neutral default = localhost origins only; each deploy adds its own frontend
    origins via the ``cors`` yaml block (corp env overlays carry the corp origins;
    a community deployment lists its own). Because the API sets
    ``allow_credentials=True``, a ``"*"`` wildcard is not permitted by browsers —
    origins must be enumerated explicitly (exact strings) or matched by regex.
    """

    allow_origins: list[str] = field(default_factory=_default_cors_origins)
    allow_origin_regex: list[str] = field(default_factory=list)


# NOTE: community-only config types (BcsAuthConfig + the data-plane Community*Config
# classes) live in ``di/config_community.py`` — kept physically separate from the
# corp config so the open-source distribution ships only the community surface.


# ── Object storage ──────────────────────────────────────────────────────
#
# NOTE: ArcaSandboxConfig + ArcaAicodingTemplateConfig moved to
# ``di/config_corp.py`` (B8) — corp-only.


@dataclass(frozen=True)
class ObjectStorageConfig:
    """``bot_oss_config`` block — neutral declarative object-store config.

    YAML carries ``endpoint``, ``bucket_name``, and a secret *name*
    (``secret_name``; legacy YAML key ``access_key_secret`` is still read).
    ``secret_name`` is the key the active ``SecretResolver`` resolves to the
    real credential — not the credential itself, and not Mist-specific (a
    community deployment resolves it via env / Vault / whichever resolver is
    bound). The field name is deployment-neutral so it works across backends.

    Object-store client construction happens in the column's ``bot_oss_client``
    provider (bound to the ``ObjectStoragePlugin`` key), keeping config pure.
    """

    endpoint: str = ""
    bucket_name: str = ""
    secret_name: str = ""


# NOTE: CodefuseTokenConfig moved to ``di/config_corp.py`` (B8) — corp-only.


@dataclass(frozen=True)
class OssToNasConfig:
    """OSS-to-NAS migration roots."""

    # Neutral defaults; corp env overlays set the real host mount roots
    # (oss_mount_root / nas_mount_root) — OSS-0 #3.
    oss_root: str = "./data/oss"
    nas_root: str = "./data/nas"


# ── Device / DaaS ───────────────────────────────────────────────────────
#
# NOTE: DeviceLocalConfig moved to ``di/config_corp.py`` (B8) — corp-only.


@dataclass(frozen=True)
class DeviceProviderConfig:
    """``device_provider`` default — used when system_config has no override."""

    default_provider: str = "local"


@dataclass(frozen=True)
class DeviceAllocationConfig:
    """Device-allocation policy — single vs multi and per-entity cap.

    ``arca_legacy_tenant`` is the legacy/rollback tenant slug for the arca
    tenant-switch feature (its rollback target). It
    is a corp-specific tenant identifier, so shipped source carries only a
    neutral default; each corp env overlay supplies the real value via the
    ``device_allocation.arca_legacy_tenant`` yaml key (OSS-0 #3).
    """

    mode: str = "single"
    max_devices_per_entity: int = 5
    arca_legacy_tenant: str = "legacy"


# ── BCS / BaaS ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BcsFuseConfig:
    """BCSFuse client config — backend's outbound URL for talking to BCS.

    Env-aware (``base_url_pre`` overrides ``base_url`` when env=='pre').
    The ``base_url`` default is neutral (empty) — each deploy supplies its own
    via the ``bcsfuse`` yaml block (corp env overlays / community overlay).
    Empty = bot-discovery inert (OSS-0 #3).
    """

    base_url: str = ""
    base_url_pre: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EcbConfig:
    """ECB (knowledge-graph) client config — backend's outbound URL for the
    bot-init downstream sync (the ``ecb`` user_config block).

    Env-aware (``base_url_pre`` overrides ``base_url`` when env=='pre'). Neutral
    empty default — each deploy supplies its own via the ``ecb`` yaml block.
    Empty = the ECB leg of the downstream sync degrades (retry/fallback wrap).
    """

    base_url: str = ""
    base_url_pre: str = ""


@dataclass(frozen=True)
class BaasConfig:
    """BaaS service config — backend's outbound HTTP client for the BaaS API."""

    # Neutral defaults; corp env overlays / community overlay supply real values
    # via the ``baas`` yaml block. Empty template uuids = "not configured" (the
    # consumers raise a clear error), tenant is a neutral placeholder — OSS-0 #3.
    api_base_url: str = "http://localhost:8888"
    api_base_url_pre: str = "http://localhost:8888"
    tenant: str = "default"
    template_uuid: str = ""
    desktop_template_uuid: str = ""
    desktop_ttl_minutes: int = 0
    # Default container TTL (minutes) BaasService provisions bots with. A neutral
    # provisioning knob (was sourced from ArcaSandboxConfig.default_ttl_minutes;
    # the provider still falls back to that YAML block for corp parity — B8).
    default_ttl_minutes: int = 10080
    # Teclaw (pull-based external container) template uuid — deploy supplies it.
    teclaw_template_uuid: str = ""
    # Personal bot via BaaS (poolab template) — deploy supplies it.
    personal_bot_template_uuid: str = ""


@dataclass(frozen=True)
class DesktopBotPeriodicScanConfig:
    """Desktop-bot periodic health scan policy.

    Safe-by-default: with the empty defaults below, the scan QUERIES BaaS
    and logs decisions, but DOES NOT mutate any user's bot state.

    enabled              False → don't even start the periodic task
                                  (startup PENDING-recovery still runs)
    apply_owner_whitelist Real DB writes only happen for owners listed here.
                          Empty list → NOBODY applied (safe default).
                          ["*"] or ["ALL"] → full rollout (every owner applied).
    global_dry_run       True → log decisions only, never apply (overrides
                                whitelist; strongest kill switch).
    """

    enabled: bool = True
    apply_owner_whitelist: frozenset[str] = frozenset()
    global_dry_run: bool = False


@dataclass(frozen=True)
class AixConfig:
    """AIX preview config for dingding channels (the ``aix`` user_config block).

    ``preview_url`` is the AIX preview endpoint stamped onto a channel config
    when the caller does not supply one. Neutral empty default — the community
    build embeds no preview endpoint; corp env overlays set it. Empty ⇒ the
    channel config carries no preview URL (feature-off).
    """

    preview_url: str = ""


@dataclass(frozen=True)
class MasaAgentEvalConfig:
    """MasaAgentEval API 配置 — 评测服务外部调用。"""

    base_url: str = "http://localhost:8080"
    base_url_pre: str = "http://localhost:8080"


# ── Coding-workspace hosting ─────────────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceHostingConfig:
    """Coding-workspace hosting backend config (neutral type, corp values).

    A generic hosting-API config (URL + creds) consumed by
    ``WorkspaceHostingClient`` for applicationCoding bots. Neutral like
    ``BaasConfig`` — corp VALUES point at DIMA via the ``dima`` yaml block; a
    community deployment sets its own (or leaves the defaults, since the client
    is an optional/unbound-in-community dependency). All fields default so the
    type constructs with no config.
    """

    base_url: str = ""
    access_key: str = ""
    access_secret: str = ""
    tenant: str = "default"
    timeout: int = 30
    # Neutral defaults; corp env overlays supply real aixcore hosts via the
    # ``dima`` yaml block (OSS-0 #3).
    aixcore_base_url: str = ""
    aixcore_base_url_pre: str = ""
    # Staff IDs auto-added as workspace admins after a bot workspace is created.
    # Neutral empty default — no employee IDs ship in community source (data-
    # leak guard, enforced by test_shipped_config_no_corp_identifiers). The real
    # list is supplied via the hosting backend yaml block
    # (``admin_member_staff_ids``) by each environment overlay. Tuple so the
    # frozen dataclass stays immutable; the service normalises to a list when
    # calling the hosting addMembers API.
    admin_member_staff_ids: tuple[str, ...] = ()


# NOTE: AntCodeConfig moved to ``di/config_corp.py`` (B8) — corp-only (AntCode git).


# ── Skill scan ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SkillScanConfig:
    """Skill-scan worker config (neutral type, corp values via ``skill_scan`` yaml).

    Capability-shaped scan-worker settings. The corp scan SDK lives behind the
    ``SkillScannerPlugin`` (B7); community uses the Noop scanner but still
    resolves ``SkillScanService`` (Rule-25 contract), so this config is neutral.
    All fields default.
    """

    enabled: bool = True
    storage_dir: str = "./data/skills_scan"
    enable_scheduled_scan: bool = False
    scan_interval_hours: int = 1
    scan_interval_minutes: int = 0
    max_concurrent_scans: int = 3
    auth_app_key: str = "agentclaw"
    # Neutral default; corp env overlays supply the real auth endpoint via the
    # ``skill_scan`` yaml block (OSS-0 #3).
    auth_endpoint: str = ""
    auth_app_secret: str = ""
    mist_mode: str = "dev"
    env: str = "prod"
    git_download_dir: str = "/home/admin/logs/tmp/sec/code"


# ── Workspace ────────────────────────────────────────────────────────────
#
# Where bot workspace files live on whichever machine the backend is
# running on. Resolved once from application-<env>.yaml at boot and
# injected wherever the path is needed. The provider must not branch on
# runtime mode — application-dev.yaml sets it to ``~/.openclaw`` (the
# dev's home), application-prod.yaml leaves it at the sandbox default,
# and a future bare-metal env can choose its own. Rule 14: nobody
# downstream re-reads LOCAL_DEV_MODE to decide which root to use.


@dataclass(frozen=True)
class WorkspaceConfig:
    """Bot workspace filesystem layout.

    Fields:
        openclaw_root: Absolute path under which OpenClaw bots' files
            live on this host. Prod default: ``/home/admin/.openclaw``
            (the sandbox mount). Local dev: ``~/.openclaw`` (expanded
            from the YAML override). Source: ``user_config.workspace``
            block in ``application.yaml``.
        claude_code_root: Same shape, for Claude Code bots.
    """

    openclaw_root: str = "/home/admin/.openclaw"
    claude_code_root: str = "/home/admin/.claude_code"
    claude_code_session_root: str = "/home/admin/.claude"


# ── Dormant bot recycle ──────────────────────────────────────────────────


@dataclass(frozen=True)
class DormantConfig:
    """Legacy dormant bot recycle fallback configuration.

    Production/pre DI now reads scheduled-scan and dry-run policy from
    ``ac_common_config`` via ``DormantScanPolicyService``. This dataclass remains
    only for direct unit tests / non-DI construction fallback paths.

    dry_run         True (default) → scan runs every step but does NOT mutate
                    bot.status / call stop_bot / call passport freeze; notify_log
                    rows are written with dry_run=1 so the 9:00 notify cron skips
                    them. Flip to False to enable real recycle in pre/prod.
                    Legacy fallback only. Do not use YAML/env for pre/prod ops.

    Note: the internal Bearer token is NOT here — it's a secret resolved
    via SecretResolver at DI time. See ``DormantInternalToken``.
    """

    dry_run: bool = True


@dataclass(frozen=True)
class DormantInternalToken:
    """Resolved bearer token for /api/internal/dormant/* endpoints.

    Produced by ``BotDormantModule._resolved_dormant_token``. Single source
    of truth for the secret name is the constant in BotDormantModule; YAML
    does NOT carry the token (would leak it in repo). Resolution rules
    match the project-wide pattern (see ``plugins/prod/outbound_rules.py``
    theat_token handling, ``core/skill_center/services/skill_scan.py``
    skillscan_agent_api_key):

      - Mist returns a secret object  → ``.value = secret.secret_value``
      - Mist returns None (singlebox /  → ``.value = <fallback constant>``
        Mist unreachable / no secret)     (so singlebox联调依然可用)
      - resolver raises                → ``.value = ""`` (failure-closed)

    Empty ``value`` makes the auth Depends 401 all requests
    (feature-off failure mode).
    """

    value: str = ""


@dataclass(frozen=True)
class DormantNotifyConfig:
    """Dormant-bot notification content config (``dormant`` yaml block).

    ``action_link_pattern`` is a ``.format(bot_id=...)`` template for the
    "view details" link in the notification copy. Neutral empty default — the
    community build embeds no bot-detail URL; corp env overlays set it. Empty ⇒
    the rendered link is empty (copy still renders).
    """

    action_link_pattern: str = ""


@dataclass(frozen=True)
class TaskQueueWorkerConfig:
    """In-process distributed-task-queue worker policy.

    Sourced from the ``task_queue_worker`` block of ``user_config``.

    Disabled by default: with no adopter registering handlers and no prod
    ``ac_task_queue`` table provisioned, a running loop would be pointless
    (and could crash-loop on a missing table). Flip ``enabled: true`` per-env
    only once a handler is wired and the table exists.

    - ``poll_interval_seconds`` / ``poll_jitter_seconds`` — idle cadence when a
      poll returns a non-full batch (jitter avoids fleet lockstep stampedes).
    - ``lease_seconds`` — claim lease; a crashed worker's task is reclaimable
      after this. Must exceed the longest expected handler runtime.
    - ``batch_size`` — max tasks claimed per poll; a full batch triggers an
      immediate re-poll so a backlog drains at fleet speed.
    - ``max_concurrency`` — in-flight handler cap per worker (<= batch_size).
    - ``retry_backoff_min_seconds`` / ``retry_backoff_max_seconds`` —
      exponential backoff floor and cap for the error-retry path. The first
      retry waits ``min``; each subsequent retry doubles, capped at ``max``.
      (There is no max-attempts cap — retries are bounded by the task's
      deadline, enforced DB-side.)
    """

    enabled: bool = False
    poll_interval_seconds: float = 2.0
    poll_jitter_seconds: float = 0.5
    lease_seconds: int = 60
    batch_size: int = 10
    max_concurrency: int = 10
    retry_backoff_min_seconds: float = 1.0
    retry_backoff_max_seconds: float = 60.0


# ── Economy / Governance ────────────────────────────────────────────────


@dataclass(frozen=True)
class EconomyGovernanceConfig:
    """Economy/governance subsystem configuration (non-secret knobs only).

    Phase 1 enhancements:
      - expire_days      Days after creation before open notification expires.
      - notify_channel   Notification dispatch channel: ``"markdown"`` or
        ``"tc_card"``.  When ``"tc_card"`` but credentials are missing,
        auto-degrades to ``"markdown"`` (recorded in audit + notify_log).

    dry_run       True (default) → scan runs but does NOT create
                  GovernanceNotification rows; audit rows are written
                  with dry_run=1. Flip to False to enable real
                  notifications in pre/prod.
    skip_weekends  True (default) → skip DingTalk delivery on Saturday/Sunday.
                  The scan still runs (data readiness, state tracking), but
                  first delivery and reminders are suppressed until Monday.
    scan_hour     Daily scan hour (0-23). Default: 14 (after ODPS data).
    max_notify_per_run  Cap on new notifications per scan run.
    cooldown_days        Days after **any** closure (auto_resolved,
                  user_confirmed, mute_expired, no_response_expired)
                  before re-notifying the same bot.  Also used as the
                  grace period added to repair_deadline when the user
                  responds "need_time" (mute_until = repair_deadline +
                  cooldown_days).  One config, uniform everywhere.
    auto_silence_close_days     Consecutive normal days before auto-silence
                      closes a ticket (mute → closed).  Default: 7.
        auto_resolve_threshold_days  Consecutive normal days for auto-resolve.
    expire_days          Days until an unanswered open notification expires.
    notify_channel       Channel: ``"markdown"`` (sampleMarkdown batchSend)
                         or ``"tc_card"`` (createAndDeliver + Markdown reason
                         + detailLink).  TC card auto-degrades to markdown
                         when ``tc_card_template_id`` or ``app_key``/``app_secret``
                         are not configured.
    tc_card_id           Aix 卡片组件 ID (v3 React)。Neutral empty default —
                         corp env overlays supply the real component id via
                         YAML ``economy_governance.tc_card_id`` (OSS-0 #3).
                         Empty ⇒ tc_card auto-degrades to markdown.
    tc_card_template_id  DingTalk 卡片壳模板 ID（schema 后缀）。每个钉钉应用
                         需注册自己的模板；与 ``tc_card_id``（Aix React 组件 ID）
                         成对出现。来源：YAML ``economy_governance.tc_card_template_id``。
    """

    dry_run: bool = True
    skip_weekends: bool = True
    scan_hour: int = 14
    scan_minute: int = 0
    max_notify_per_run: int = 200
    cooldown_days: int = 14
    auto_silence_close_days: int = 7
    auto_resolve_threshold_days: int = 3
    expire_days: int = 7
    notify_channel: str = "tc_card"  # "tc_card" | "markdown"
    tc_card_id: str = ""
    tc_card_template_id: str = ""
    # TC card detail-link preview host. Neutral empty default — corp env overlays
    # supply the real endpoint via ``economy_governance.tc_card_preview_url``
    # (OSS-0 #3). Empty ⇒ the deep link carries no preview host (feature-off).
    tc_card_preview_url: str = ""
