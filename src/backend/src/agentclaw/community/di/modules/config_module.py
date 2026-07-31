"""ConfigModule — single source of truth for ``user_config.get(...)`` reads.

Every cluster of legacy ``user_config.get(...)`` calls collapses to one
``@singleton`` ``@provider`` here. Downstream services receive the typed
dataclass via constructor injection and never reach into
``sofa.sofa_config`` themselves.

Per architectural test (Task 8), ``user_config.get`` and ``get_config()``
must NOT appear anywhere outside this file (and the thin
``core/config/sofa.py`` handle).

Defaults live in ``agentclaw.community.di.config`` (the dataclass field defaults).
Providers instantiate the dataclass to read those defaults, then
override individual fields from YAML where present. This keeps the
literal default in exactly one place per field.

Providers do **no I/O** (no Mist calls, no HTTP) — secret resolution
and client construction happen in downstream module providers.
"""
from __future__ import annotations

from typing import Any

from injector import Module, inject, provider, singleton

from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


def _user_config() -> dict[str, Any]:
    """Read the ``user_config`` dict from sofa, defensively.

    Local mode and tests often have no sofa config; return ``{}`` rather
    than raising so providers fall through to dataclass defaults.
    """
    try:
        from agentclaw.community.core.config import sofa

        cfg_obj = sofa.sofa_config
        user_cfg = getattr(cfg_obj, "user_config", None)
        return dict(user_cfg) if user_cfg else {}
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("ConfigModule: sofa user_config unavailable (%s)", exc)
        return {}


def _block(name: str) -> dict[str, Any]:
    """Pull one named block out of ``user_config``; ``{}`` if missing."""
    raw = _user_config().get(name) or {}
    return dict(raw) if isinstance(raw, dict) else {}


class ConfigModule(Module):
    """Bind every typed config dataclass."""

    # ── Workspace ───────────────────────────────────────────────────

    @singleton
    @provider
    def workspace(self) -> cfg.WorkspaceConfig:
        """Bot workspace filesystem layout.

        Sources both roots from the ``workspace`` user_config block;
        falls back to the dataclass defaults (sandbox paths) when the
        block is absent or a field is missing. ``~`` is expanded for
        each path so application-dev.yaml can use ``~/.openclaw`` and
        get the dev's home directory at boot.
        """
        import pathlib

        block = _block("workspace")
        defaults = cfg.WorkspaceConfig()

        def _expand(value: str | None, default: str) -> str:
            raw = value if isinstance(value, str) and value else default
            return str(pathlib.Path(raw).expanduser())

        return cfg.WorkspaceConfig(
            openclaw_root=_expand(block.get("openclaw_root"), defaults.openclaw_root),
            claude_code_root=_expand(
                block.get("claude_code_root"), defaults.claude_code_root
            ),
        )

    # ── Access policy ───────────────────────────────────────────────
    #
    # Corp auth/SSO/token-exchange/aceagent/skill-center-api providers moved to
    # ``CorpConfigModule`` (B8); this neutral module binds no corp config type.

    @singleton
    @provider
    def whitelist(self) -> cfg.WhitelistConfig:
        block = _block("whitelist")
        names = block.get("operator_names") or []
        return cfg.WhitelistConfig(operator_names=frozenset(names))

    @singleton
    @provider
    def bot_chat(self) -> cfg.BotChatConfig:
        """Bot-chat Langfuse config (neutral empty; corp env overlays set the
        ``bot_chat`` yaml block)."""
        block = _block("bot_chat")
        defaults = cfg.BotChatConfig()
        return cfg.BotChatConfig(
            langfuse_base_url=block.get("langfuse_base_url", defaults.langfuse_base_url),
            langfuse_public_key=block.get(
                "langfuse_public_key", defaults.langfuse_public_key
            ),
            langfuse_secret_key=block.get(
                "langfuse_secret_key", defaults.langfuse_secret_key
            ),
        )

    @singleton
    @provider
    def yuque(self) -> cfg.YuqueConfig:
        """Yuque verify endpoint config (neutral empty; corp env overlays set the
        ``yuque.user_api`` yaml key)."""
        block = _block("yuque")
        defaults = cfg.YuqueConfig()
        return cfg.YuqueConfig(
            user_api=block.get("user_api", defaults.user_api),
        )

    @singleton
    @provider
    def kb(self) -> cfg.KbConfig:
        """Internal knowledge-base config for D-TOOLS-002 (neutral empty; corp
        env overlays set the ``kb`` yaml block)."""
        block = _block("kb")
        defaults = cfg.KbConfig()
        return cfg.KbConfig(
            base_url=block.get("base_url", defaults.base_url),
            token=block.get("token", defaults.token),
            function_name=block.get("function_name", defaults.function_name),
            instance_name=block.get("instance_name", defaults.instance_name),
            interface_name=block.get("interface_name", defaults.interface_name),
        )

    @singleton
    @provider
    def bcn(self) -> cfg.BcnConfig:
        """BCN host + provider credentials (neutral empty; corp env overlays set
        pre/prod hosts, provider ids, and admin tokens)."""
        block = _block("bcn")
        defaults = cfg.BcnConfig()
        return cfg.BcnConfig(
            base_url=block.get("base_url", defaults.base_url),
            base_url_pre=block.get("base_url_pre", defaults.base_url_pre),
            provider_id_prod=block.get("provider_id_prod", defaults.provider_id_prod),
            provider_id_pre=block.get("provider_id_pre", defaults.provider_id_pre),
            provider_admin_token_prod=block.get(
                "provider_admin_token_prod", defaults.provider_admin_token_prod
            ),
            provider_admin_token_pre=block.get(
                "provider_admin_token_pre", defaults.provider_admin_token_pre
            ),
        )

    @singleton
    @provider
    def llm_harness(self) -> cfg.LLMHarnessConfig:
        """Harness LLM config (neutral empty defaults; corp env overlays set the
        endpoint/secret name via the ``llm`` yaml block)."""
        block = _block("llm")
        defaults = cfg.LLMHarnessConfig()
        return cfg.LLMHarnessConfig(
            base_url=block.get("base_url", defaults.base_url),
            secret_name=block.get("secret_name", defaults.secret_name),
        )

    @singleton
    @provider
    def secret_names(self) -> cfg.SecretNamesConfig:
        """Secret-registry key names (neutral empty defaults; corp env overlays
        set the real Mist names via the ``secret_names`` yaml block)."""
        block = _block("secret_names")
        defaults = cfg.SecretNamesConfig()
        return cfg.SecretNamesConfig(
            dormant_internal_token=block.get(
                "dormant_internal_token", defaults.dormant_internal_token
            ),
            aiworkbench_repo_url=block.get(
                "aiworkbench_repo_url", defaults.aiworkbench_repo_url
            ),
        )

    @singleton
    @provider
    def cors(self) -> cfg.CorsConfig:
        """Browser CORS allow-list (neutral localhost default; corp origins via
        the ``cors`` yaml block)."""
        block = _block("cors")
        defaults = cfg.CorsConfig()
        origins = block.get("allow_origins")
        regex = block.get("allow_origin_regex")
        return cfg.CorsConfig(
            allow_origins=(
                list(origins) if isinstance(origins, list) else defaults.allow_origins
            ),
            allow_origin_regex=(
                list(regex) if isinstance(regex, list) else defaults.allow_origin_regex
            ),
        )

    # NOTE: BcsAuthConfig is provided by ``CommunityIdentityModule`` (community
    # column only), NOT here — corp/test never resolve it. ``_block`` stays the
    # single sofa user_config reader the community provider reuses.

    # NOTE: arca_sandbox + arca_aicoding_template providers moved to
    # ``CorpConfigModule`` (B8). BaasService no longer reads ArcaSandboxConfig —
    # its TTL comes from ``BaasConfig.default_ttl_minutes`` (provider above).

    @singleton
    @provider
    def bot_oss(self) -> cfg.ObjectStorageConfig:
        """``secret_name`` is the SecretResolver key (not the credential).

        Prefers the neutral YAML key ``secret_name``; falls back to the legacy
        ``access_key_secret`` key so the corp YAML keeps working unchanged.
        """
        block = _block("bot_oss_config")
        defaults = cfg.ObjectStorageConfig()
        return cfg.ObjectStorageConfig(
            endpoint=block.get("endpoint", defaults.endpoint),
            bucket_name=block.get("bucket_name", defaults.bucket_name),
            secret_name=block.get(
                "secret_name",
                block.get("access_key_secret", defaults.secret_name),
            ),
        )

    # NOTE: codefuse_token provider moved to ``CorpConfigModule`` (B8).

    @singleton
    @provider
    def oss_to_nas(self) -> cfg.OssToNasConfig:
        user = _user_config()
        defaults = cfg.OssToNasConfig()
        return cfg.OssToNasConfig(
            oss_root=user.get("oss_mount_root", defaults.oss_root),
            nas_root=user.get("nas_mount_root", defaults.nas_root),
        )

    # ── Device / DaaS ───────────────────────────────────────────────
    #
    # NOTE: device_local provider moved to ``CorpConfigModule`` (B8).

    @singleton
    @provider
    def device_provider(self) -> cfg.DeviceProviderConfig:
        user = _user_config()
        defaults = cfg.DeviceProviderConfig()
        return cfg.DeviceProviderConfig(
            default_provider=user.get("device_provider", defaults.default_provider)
        )

    @singleton
    @provider
    def device_allocation(self) -> cfg.DeviceAllocationConfig:
        block = _block("device_allocation")
        defaults = cfg.DeviceAllocationConfig()
        return cfg.DeviceAllocationConfig(
            mode=block.get("mode", defaults.mode),
            max_devices_per_entity=int(
                block.get("max_devices_per_entity", defaults.max_devices_per_entity)
            ),
            arca_legacy_tenant=str(
                block.get("arca_legacy_tenant", defaults.arca_legacy_tenant)
            ),
        )

    # ── BCS / BaaS ──────────────────────────────────────────────────

    @singleton
    @provider
    def bcsfuse(self) -> cfg.BcsFuseConfig:
        """Reads ``user_config.bcsfuse``, falls back to root-level
        ``bcsfuse``. Mirrors the lookup in ``BotDiscoverService``.

        The hardcoded prod URL fallback (legacy line 67 of
        ``BotDiscoverService``) lives on the dataclass default, not
        here — consumers reading ``cfg.base_url`` get it automatically
        when the YAML block is missing.
        """
        user_block = _block("bcsfuse")
        if not user_block:
            try:
                from agentclaw.community.core.config import sofa

                root = getattr(sofa.sofa_config, "bcsfuse", None)
                if isinstance(root, dict):
                    user_block = dict(root)
            except Exception:
                user_block = {}
        defaults = cfg.BcsFuseConfig()
        return cfg.BcsFuseConfig(
            base_url=user_block.get("base_url", defaults.base_url),
            base_url_pre=user_block.get("base_url_pre", defaults.base_url_pre),
            raw=user_block,
        )

    @singleton
    @provider
    def aix(self) -> cfg.AixConfig:
        """AIX preview endpoint for dingding channels (neutral empty; corp env
        overlays set the ``aix`` yaml block)."""
        block = _block("aix")
        defaults = cfg.AixConfig()
        return cfg.AixConfig(
            preview_url=block.get("preview_url", defaults.preview_url),
        )

    @singleton
    @provider
    def ecb(self) -> cfg.EcbConfig:
        """ECB downstream-sync host (neutral empty; corp env overlays set the
        ``ecb`` yaml block)."""
        block = _block("ecb")
        defaults = cfg.EcbConfig()
        return cfg.EcbConfig(
            base_url=block.get("base_url", defaults.base_url),
            base_url_pre=block.get("base_url_pre", defaults.base_url_pre),
        )

    @singleton
    @provider
    def gateway(self) -> cfg.GatewayConfig:
        """Public API gateway hosts (neutral empty; corp env overlays set the
        ``gateway`` yaml block)."""
        block = _block("gateway")
        defaults = cfg.GatewayConfig()
        return cfg.GatewayConfig(
            base_url=block.get("base_url", defaults.base_url),
            base_url_pre=block.get("base_url_pre", defaults.base_url_pre),
        )

    @singleton
    @provider
    @inject
    def gateway_endpoint(self, gateway: cfg.GatewayConfig) -> cfg.GatewayEndpoint:
        """The gateway host for this environment, resolved here rather than by
        the consumer — selecting a deployment is composition-root work, and it
        keeps ``SERVER_ENV`` out of the core service (see ``GatewayEndpoint``).

        Same pre/prod selection every other host pair in this build uses
        (``http_client_module.py``); pre and prod are distinct gateways, so a
        credential issued for one is not accepted by the other.
        """
        return cfg.GatewayEndpoint(
            base_url=(
                gateway.base_url_pre
                if get_current_env() == "pre"
                else gateway.base_url
            )
        )

    @singleton
    @provider
    def baas(self) -> cfg.BaasConfig:
        block = _block("baas")
        defaults = cfg.BaasConfig()
        # default_ttl_minutes: prefer the baas block, then fall back to the
        # corp ``arca_sandbox.default_ttl_minutes`` (where it lived per-env
        # before B8 decoupled BaasService from ArcaSandboxConfig) so corp keeps
        # its exact env value with no YAML change; community (no arca_sandbox
        # block) takes the baas value or the neutral default.
        ttl = block.get(
            "default_ttl_minutes",
            _block("arca_sandbox").get(
                "default_ttl_minutes", defaults.default_ttl_minutes
            ),
        )
        return cfg.BaasConfig(
            api_base_url=block.get("api_base_url", defaults.api_base_url),
            api_base_url_pre=block.get(
                "api_base_url_pre", defaults.api_base_url_pre
            ),
            tenant=block.get("tenant", defaults.tenant),
            template_uuid=block.get("template_uuid", defaults.template_uuid),
            desktop_template_uuid=block.get(
                "desktop_template_uuid", defaults.desktop_template_uuid
            ),
            teclaw_template_uuid=block.get(
                "teclaw_template_uuid", defaults.teclaw_template_uuid
            ),
            personal_bot_template_uuid=block.get(
                "personal_bot_template_uuid", defaults.personal_bot_template_uuid
            ),
            default_ttl_minutes=int(ttl),
        )

    @singleton
    @provider
    def workspace_hosting(self) -> cfg.WorkspaceHostingConfig:
        """Coding-workspace hosting backend config (neutral; corp values via the
        ``dima`` yaml block). Non-strict — defaults when the block is absent, so
        community (no ``dima`` block, client unbound) constructs cleanly.
        """
        block = _block("dima")
        defaults = cfg.WorkspaceHostingConfig()
        # admin_member_staff_ids: employee IDs granted workspace-admin on
        # creation. Environment overlays supply the real list via
        # ``admin_member_staff_ids``; neutral empty default keeps community
        # source free of employee IDs (data-leak guard). Normalised to a tuple
        # to satisfy the frozen dataclass + immutability invariant.
        admin_ids = block.get("admin_member_staff_ids", defaults.admin_member_staff_ids)
        if isinstance(admin_ids, (list, tuple)):
            admin_ids = tuple(str(i) for i in admin_ids)
        else:
            admin_ids = defaults.admin_member_staff_ids

        return cfg.WorkspaceHostingConfig(
            base_url=block.get("base_url", defaults.base_url),
            access_key=block.get("access_key", defaults.access_key),
            access_secret=block.get("access_secret", defaults.access_secret),
            tenant=block.get("tenant", defaults.tenant),
            timeout=int(block.get("timeout", defaults.timeout)),
            aixcore_base_url=block.get("aixcore_base_url", defaults.aixcore_base_url),
            aixcore_base_url_pre=block.get(
                "aixcore_base_url_pre", defaults.aixcore_base_url_pre
            ),
            admin_member_staff_ids=admin_ids,
        )

    @singleton
    @provider
    def skill_scan(self) -> cfg.SkillScanConfig:
        """Skill-scan worker config (neutral; corp values via the ``skill_scan``
        yaml block). SkillScanService is resolved in every profile (Rule-25
        contract); the corp scan SDK lives behind the SkillScannerPlugin (B7)."""
        block = _block("skill_scan")
        defaults = cfg.SkillScanConfig()
        return cfg.SkillScanConfig(
            enabled=block.get("enabled", defaults.enabled),
            storage_dir=block.get("storage_dir", defaults.storage_dir),
            enable_scheduled_scan=block.get(
                "enable_scheduled_scan", defaults.enable_scheduled_scan
            ),
            scan_interval_hours=int(
                block.get("scan_interval_hours", defaults.scan_interval_hours)
            ),
            scan_interval_minutes=int(
                block.get("scan_interval_minutes", defaults.scan_interval_minutes)
            ),
            max_concurrent_scans=int(
                block.get("max_concurrent_scans", defaults.max_concurrent_scans)
            ),
            auth_app_key=block.get("auth_app_key", defaults.auth_app_key),
            auth_endpoint=block.get("auth_endpoint", defaults.auth_endpoint),
            auth_app_secret=block.get("auth_app_secret", defaults.auth_app_secret),
            mist_mode=block.get("mist_mode", defaults.mist_mode),
            env=block.get("env", defaults.env),
            git_download_dir=block.get(
                "git_download_dir", defaults.git_download_dir
            ),
        )

    @singleton
    @provider
    def masa_agent_eval(self) -> cfg.MasaAgentEvalConfig:
        """MasaAgentEval API 配置 — 评测服务外部调用。"""
        block = _block("masa_agent_eval")
        defaults = cfg.MasaAgentEvalConfig()
        return cfg.MasaAgentEvalConfig(
            base_url=block.get("base_url", defaults.base_url),
            base_url_pre=block.get("base_url_pre", defaults.base_url_pre),
        )

    @singleton
    @provider
    def desktop_bot_periodic_scan(self) -> cfg.DesktopBotPeriodicScanConfig:
        """Desktop-bot periodic health scan policy.

        YAML shape under ``user_config.desktop_bot_periodic_scan``::

            enabled: true
            apply_owner_whitelist:    # who gets REAL DB writes
              - "100000"              # — others are LOG_ONLY
            global_dry_run: false     # true → log only, never apply

        Empty/missing whitelist means NOBODY applied (safe default).
        Use the literal "*" or "ALL" entry to opt every owner into apply.
        """
        block = _block("desktop_bot_periodic_scan")
        defaults = cfg.DesktopBotPeriodicScanConfig()
        raw_list = block.get("apply_owner_whitelist") or []
        if isinstance(raw_list, (list, tuple, set, frozenset)):
            whitelist = frozenset(
                str(x).strip()
                for x in raw_list
                if str(x).strip()
            )
        else:
            whitelist = defaults.apply_owner_whitelist
        return cfg.DesktopBotPeriodicScanConfig(
            enabled=bool(block.get("enabled", defaults.enabled)),
            apply_owner_whitelist=whitelist,
            global_dry_run=bool(block.get("global_dry_run", defaults.global_dry_run)),
        )

    # NOTE: skill_scan + antcode + dima providers moved to ``CorpConfigModule`` (B8) —
    # AntCode git + DIMA hosting are corp-only.

    # ── Dormant bot recycle ──────────────────────────────────────────────

    @singleton
    @provider
    def dormant_config(self) -> cfg.DormantConfig:
        """Legacy DormantConfig fallback.

        Pre/prod dormant scheduled-scan policy is controlled by ac_common_config
        (``bot_dormant`` / ``scan_policy``) via DormantScanPolicyService. This
        provider is kept for direct unit tests and old non-DI construction paths.

        Legacy YAML shape under ``user_config.dormant``::

            dormant:
              dry_run: true   # 凌晨 cron 是否 dry-run（true=不真改 status/不真发钉钉）

        Legacy env vars override YAML when set:
          - DORMANT_DRY_RUN ("true" / "false" / "0" / "1" / "yes" / "no")

        The internal Bearer token is NOT here — it's a secret resolved at
        DI time via SecretResolver in BotDormantModule. See
        ``DormantInternalToken``.
        """
        import os
        block = _block("dormant")

        # dry_run: env wins over YAML, default True (safe)
        env_dry = os.environ.get("DORMANT_DRY_RUN")
        if env_dry is not None:
            dry_run = env_dry.strip().lower() not in ("false", "0", "no")
        else:
            yaml_dry = block.get("dry_run")
            # YAML true/false 都是真 bool；缺失 → 默认 True
            dry_run = True if yaml_dry is None else bool(yaml_dry)

        return cfg.DormantConfig(dry_run=dry_run)

    @singleton
    @provider
    def dormant_notify(self) -> cfg.DormantNotifyConfig:
        """Dormant notification content config (neutral empty; corp env overlays
        set ``dormant.action_link_pattern``)."""
        block = _block("dormant")
        defaults = cfg.DormantNotifyConfig()
        return cfg.DormantNotifyConfig(
            action_link_pattern=block.get(
                "action_link_pattern", defaults.action_link_pattern
            ),
        )

    @singleton
    @provider
    def task_queue_worker(self) -> cfg.TaskQueueWorkerConfig:
        """In-process distributed-task-queue worker policy.

        YAML shape under ``user_config.task_queue_worker``::

            task_queue_worker:
              enabled: false              # default off; flip per-env once a
                                          #   handler is wired + table exists
              poll_interval_seconds: 2.0
              poll_jitter_seconds: 0.5
              lease_seconds: 60
              batch_size: 10
              max_concurrency: 10
              retry_backoff_min_seconds: 1.0
              retry_backoff_max_seconds: 60.0

        Any omitted key falls back to the ``TaskQueueWorkerConfig`` default.
        Numeric keys are typed (``int``/``float``) — do NOT set them to YAML
        ``null`` (omit the key to take the default instead). Operational note:
        keep ``lease_seconds`` comfortably above the longest expected handler
        runtime, else a slow handler's outcome write loses to a reclaim.
        """
        block = _block("task_queue_worker")
        defaults = cfg.TaskQueueWorkerConfig()
        return cfg.TaskQueueWorkerConfig(
            enabled=bool(block.get("enabled", defaults.enabled)),
            poll_interval_seconds=float(
                block.get("poll_interval_seconds", defaults.poll_interval_seconds)
            ),
            poll_jitter_seconds=float(
                block.get("poll_jitter_seconds", defaults.poll_jitter_seconds)
            ),
            lease_seconds=int(block.get("lease_seconds", defaults.lease_seconds)),
            batch_size=int(block.get("batch_size", defaults.batch_size)),
            max_concurrency=int(block.get("max_concurrency", defaults.max_concurrency)),
            retry_backoff_min_seconds=float(
                block.get("retry_backoff_min_seconds", defaults.retry_backoff_min_seconds)
            ),
            retry_backoff_max_seconds=float(
                block.get("retry_backoff_max_seconds", defaults.retry_backoff_max_seconds)
            ),
        )
