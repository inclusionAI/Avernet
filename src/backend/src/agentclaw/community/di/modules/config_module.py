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
import math
from dataclasses import fields
from typing import Any
from injector import Module, inject, provider, singleton
from agentclaw.community.core.skill_center import draft_content
from agentclaw.community.core.task_queue.types import MAX_APP_LEN
from agentclaw.community.core.skill_center.canonical_center_store import CanonicalCenterStoreConfig
from agentclaw.community.di import config as cfg
from agentclaw.community.kernel.deploy_runtime import DeployRuntime
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_BAAS,
    QUALIFIER_BCN,
    QUALIFIER_GENERAL,
    QUALIFIER_MASA_AGENT_EVAL,
)
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

read_user_config = _user_config  # public seam for sibling DI config modules

def _object_prefix_setting(name: str, default: str) -> Any:
    raw = _user_config().get(name)
    if raw is None:
        return default
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be a mapping")
    unknown = sorted(set(raw) - {"base_prefix_template"})
    if unknown:
        raise ValueError(f"{name} contains unknown keys: " + ", ".join(unknown))
    return raw.get("base_prefix_template", default)

def _app_name() -> str | None:
    """Return the configured top-level app name, or None without a source."""
    from agentclaw.community.core.config import provider as config_provider

    if not config_provider.has_config_provider():
        return None
    from agentclaw.community.core.config import sofa
    return str(getattr(sofa.sofa_config, "app_name", "") or "")

# The closed set of HttpClient bindings an ``overrides`` entry may name. Taken
# from the qualifier constants themselves so the config surface cannot drift
# from the injector keys.
_HTTP_CLIENT_QUALIFIERS = frozenset(
    {QUALIFIER_BAAS, QUALIFIER_BCN, QUALIFIER_GENERAL, QUALIFIER_MASA_AGENT_EVAL}
)
# The policy fields an ``http_client`` block (or an override body) may name,
# derived from the dataclass so the accepted config surface cannot drift from
# the type it populates.
_POOL_POLICY_FIELDS = frozenset(f.name for f in fields(cfg.HttpClientPoolPolicy))
_TRUE_SCALARS = frozenset({"true", "yes", "on", "1"})
_FALSE_SCALARS = frozenset({"false", "no", "off", "0"})

def _coerce(block: dict[str, Any], key: str, cast, fallback, where: str, valid=None):
    """One config value, cast defensively, falling back on anything unusable.

    An empty, malformed or out-of-range YAML scalar (``max_connections:`` with
    no value, ``keepalive_expiry: ~``, ``max_connections: .inf``,
    ``max_connections: 0``) would otherwise either raise inside the provider or
    sail through as a working-looking value that breaks the binding.

    Raising here is not a loud failure — it is the quietest one available:
    ``discover_lifecycle_participants`` swallows a provider exception, so all
    four HttpClient bindings would vanish at boot with no log line and no
    teardown registration, and the first outbound call would die somewhere
    unrelated. Hence the broad ``except``: no config value is worth losing the
    transport over, and every rejection is logged with the offending key.

    ``valid`` rejects values that cast cleanly but cannot work — a
    ``max_connections`` of 0 turns every request on that binding into a
    ``PoolTimeout`` for the life of the process.
    """
    if key not in block:
        return fallback
    raw = block[key]
    if raw is None:
        logger.warning(
            "ConfigModule: %s.%s is empty; using %r.", where, key, fallback
        )
        return fallback
    try:
        value = cast(raw)
    except Exception as exc:  # noqa: BLE001 — see docstring: never fatal
        logger.warning(
            "ConfigModule: %s.%s=%r is not a valid %s (%s); using %r.",
            # `_as_bool` / `_as_int` are internal names; an operator reading a
            # boot log wants the type, not our helper.
            where, key, raw, cast.__name__.removeprefix("_as_"),
            type(exc).__name__, fallback,
        )
        return fallback
    if valid is not None and not valid(value):
        logger.warning(
            "ConfigModule: %s.%s=%r is out of range; using %r.",
            where, key, value, fallback,
        )
        return fallback
    return value

def _as_int(raw: Any) -> int:
    """Strict integer: no silent truncation, no ``True`` meaning 1.

    ``int(1.7)`` is ``1``. For a pool ceiling that is a legal-looking but
    pathological value — a one-connection pool serialises every burst — and it
    would pass the range guard, so it has to be rejected at the cast instead.
    ``int("1.7")`` already raises, so string scalars need no special case.
    """
    if isinstance(raw, bool):
        raise TypeError(f"not an integer: {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if not math.isfinite(raw) or raw != int(raw):
            raise ValueError(f"not a whole number: {raw!r}")
        return int(raw)
    if isinstance(raw, str):
        return int(raw.strip())
    raise TypeError(f"not an integer: {type(raw).__name__}")

def _as_bool(raw: Any) -> bool:
    """Strict-ish boolean: YAML may hand back a string, and ``bool("false")``
    is ``True`` — which would silently enable a wire-protocol change that the
    design requires to be opt-in."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in _TRUE_SCALARS:
            return True
        if text in _FALSE_SCALARS:
            return False
        raise ValueError(f"not a boolean: {raw!r}")
    if isinstance(raw, (int, float)):
        # Exactly 0 or 1. `bool()` reads 2, -1, 0.5 and .nan as True alike, and
        # none of those is a plausible way to write a boolean — so accepting
        # them means a typo silently enables a wire change documented as
        # opt-in. 0/1 stay accepted because YAML users do write them.
        if raw == 0:
            return False
        if raw == 1:
            return True
        raise ValueError(f"not a boolean: {raw!r}")
    raise TypeError(f"not a boolean: {type(raw).__name__}")

def _reject_unknown_keys(
    block: dict[str, Any], where: str, *, allow_overrides: bool
) -> None:
    """Fail on any key the policy does not define.

    ``_pool_policy`` probes only the names it knows, so a misspelled field
    (``max_conections``, ``htttp2``) would otherwise be discarded in silence and
    the process would boot on inherited defaults while looking configured —
    the same failure as an unknown qualifier, one level down. ``overrides`` is
    accepted in the top-level block only; nesting it inside an override body is
    a misunderstanding of the shape, not a policy field.
    """
    allowed = _POOL_POLICY_FIELDS | ({"overrides"} if allow_overrides else frozenset())
    unknown = sorted(str(k) for k in block if str(k) not in allowed)
    if unknown:
        valid = ", ".join(repr(k) for k in sorted(allowed))
        raise ValueError(
            f"unknown {where} key(s) "
            f"{', '.join(repr(u) for u in unknown)}; expected one of {valid}"
        )

def _pool_policy(
    block: dict[str, Any], base: cfg.HttpClientPoolPolicy, where: str = "http_client"
) -> cfg.HttpClientPoolPolicy:
    """One transport policy from a YAML mapping, per-field fallback to ``base``.

    Run twice per override: once with the dataclass defaults to resolve the
    shared ``defaults``, then again per override *starting from those defaults*.
    That is what lets an override name one key and still resolve to a total
    policy, so ``for_qualifier`` never has to merge at the call site.
    """
    return cfg.HttpClientPoolPolicy(
        # A ceiling below 1 would make every request on the binding wait for a
        # connection that can never exist, then fail as a timeout — forever.
        max_connections=_coerce(
            block, "max_connections", _as_int, base.max_connections, where,
            valid=lambda v: v >= 1,
        ),
        # 0 is legitimate here: it disables keep-alive without disabling the pool.
        max_keepalive_connections=_coerce(
            block, "max_keepalive_connections", _as_int,
            base.max_keepalive_connections, where,
            valid=lambda v: v >= 0,
        ),
        keepalive_expiry=_coerce(
            block, "keepalive_expiry", float, base.keepalive_expiry, where,
            valid=lambda v: v >= 0 and math.isfinite(v),
        ),
        http2=_coerce(block, "http2", _as_bool, base.http2, where),
    )

class ConfigModule(Module):
    """Bind every typed config dataclass."""

    # ── Workspace ───────────────────────────────────────────────────

    @singleton
    @provider
    def canonical_center_store(self) -> CanonicalCenterStoreConfig:
        defaults = CanonicalCenterStoreConfig(env=get_current_env())
        prefix = _object_prefix_setting(
            "canonical_center_store", defaults.base_prefix_template
        )
        if not isinstance(prefix, str):
            raise ValueError(
                "canonical_center_store.base_prefix_template must be a string"
            )
        return CanonicalCenterStoreConfig(
            env=get_current_env(),
            base_prefix_template=prefix,
        )

    @singleton
    @provider
    def workspace(self) -> cfg.WorkspaceConfig:
        """Resolve workspace roots to absolute host paths at the DI boundary.

        Missing fields retain the dataclass sandbox defaults.
        """
        import os

        block = _block("workspace")
        defaults = cfg.WorkspaceConfig()

        def _expand(value: str | None, default: str) -> str:
            raw = value if isinstance(value, str) and value else default
            return os.path.abspath(os.path.expanduser(raw))

        return cfg.WorkspaceConfig(
            openclaw_root=_expand(block.get("openclaw_root"), defaults.openclaw_root),
            claude_code_root=_expand(
                block.get("claude_code_root"), defaults.claude_code_root
            ),
            aicoding_root=_expand(
                block.get("aicoding_root"), defaults.aicoding_root
            ),
            hermes_root=_expand(block.get("hermes_root"), defaults.hermes_root),
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
        set the real Mist names via the ``secret_names`` yaml block).

        Built reflectively: every field is a plain string whose yaml key is its
        own name, and a hand-written constructor call silently pins any field
        left out of it to its default while nobody reads its yaml key.
        ``skill_center_internal_token`` shipped that way, which for a token
        name meant the auth guard fell back to the public singlebox constant
        in every environment. A field needing other handling must be lifted out.
        """
        block = _block("secret_names")
        defaults = cfg.SecretNamesConfig()
        return cfg.SecretNamesConfig(
            **{
                f.name: block.get(f.name, getattr(defaults, f.name))
                for f in fields(cfg.SecretNamesConfig)
            }
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

    @singleton
    @provider
    def draft_content_store(self) -> draft_content.DraftContentStoreConfig:
        """Immutable Draft revision object-key prefix."""
        defaults = draft_content.DraftContentStoreConfig()
        value = _object_prefix_setting("draft_content_store", defaults.base_prefix_template)
        return draft_content.DraftContentStoreConfig(base_prefix_template=value)

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
            eval_template_uuid=block.get(
                "eval_template_uuid", defaults.eval_template_uuid
            ),
            personal_bot_template_uuid=block.get(
                "personal_bot_template_uuid", defaults.personal_bot_template_uuid
            ),
            default_ttl_minutes=int(ttl),
        )

    @singleton
    @provider
    def deploy_runtime(self) -> cfg.DeployRuntimeConfig:
        """Which container this deployment runs (``baas.deploy_runtime``).

        Validated here rather than where the composer is chosen, so a typo is
        a config-load error naming the values that would have worked — not a
        deployment that boots on the wrong image's payload and reports healthy
        while its bots do not work.
        """
        raw = _block("baas").get("deploy_runtime")
        default = cfg.DeployRuntimeConfig()
        if raw is None:
            return default
        try:
            return cfg.DeployRuntimeConfig(DeployRuntime(str(raw).strip()))
        except ValueError:
            valid = ", ".join(repr(r.value) for r in DeployRuntime)
            raise ValueError(
                f"unknown baas.deploy_runtime {raw!r}; expected one of {valid}"
            ) from None

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
    def http_client_pool(self) -> cfg.HttpClientPoolConfig:
        """Outbound HTTP transport policy for the ``HttpClient`` bindings.

        YAML shape under ``user_config.http_client``::

            max_connections: 100
            max_keepalive_connections: 20
            keepalive_expiry: 5.0
            http2: false
            overrides:                  # optional, keyed by HttpClient qualifier
              baas: {http2: true}

        Missing block ⇒ dataclass defaults for every binding, so no deployment
        needs a config change to adopt pooling. An override names only the keys
        it changes; the rest come from the resolved shared defaults, so a value
        left unset keeps tracking those defaults if they later change.

        An unrecognised qualifier key **raises**. The valid set is closed and
        known (the four ``QUALIFIER_*`` constants), so a name outside it cannot
        be honoured by any binding — unlike a malformed *value*, where falling
        back to a working default is a sane reading of operator intent. Silently
        ignoring it would leave the operator believing a ceiling had been raised
        while the binding ran on the shared defaults, which is the failure this
        block exists to prevent. ``ci.enforce.md`` §E requires startup to fail
        early on invalid config, and ``baas.deploy_runtime`` already rejects an
        unknown value the same way.

        ``HttpClientPoolConfig`` is in ``container.py``'s eager-check allowlist
        so that raise lands at boot on pre/prod rather than at the first
        outbound call.
        """
        block = _block("http_client")
        _reject_unknown_keys(block, "http_client", allow_overrides=True)
        defaults = _pool_policy(block, cfg.HttpClientPoolPolicy())
        raw_overrides = block.get("overrides") or {}
        overrides: dict[str, cfg.HttpClientPoolPolicy] = {}
        if isinstance(raw_overrides, dict):
            unknown = sorted(
                str(name) for name in raw_overrides if str(name) not in _HTTP_CLIENT_QUALIFIERS
            )
            if unknown:
                valid = ", ".join(repr(q) for q in sorted(_HTTP_CLIENT_QUALIFIERS))
                raise ValueError(
                    f"unknown http_client.overrides qualifier(s) "
                    f"{', '.join(repr(u) for u in unknown)}; expected one of {valid}"
                )
            for name, body in raw_overrides.items():
                if isinstance(body, dict):
                    scope = f"http_client.overrides.{name}"
                    _reject_unknown_keys(dict(body), scope, allow_overrides=False)
                    overrides[str(name)] = _pool_policy(dict(body), defaults, scope)
                else:
                    logger.warning(
                        "ConfigModule: http_client.overrides.%s is not a mapping "
                        "(%s); ignoring it — that binding uses the shared defaults.",
                        name,
                        type(body).__name__,
                    )
        elif raw_overrides:
            # Dropping every override is the bigger misconfiguration, so it must
            # not be the quieter one.
            logger.warning(
                "ConfigModule: http_client.overrides is not a mapping (%s); "
                "ignoring ALL per-qualifier overrides — every binding uses the "
                "shared defaults.",
                type(raw_overrides).__name__,
            )
        return cfg.HttpClientPoolConfig(defaults=defaults, overrides=overrides)

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
    def task_queue(self) -> cfg.TaskQueueConfig:
        """Which application owns this deployment's ``ac_task_queue`` rows.

        Read from the **top-level** ``app_name`` — the name the deployment
        already goes by — rather than from a queue-specific key. Two backends
        share the table and each claims only its own rows, so the owner is the
        deployment's identity; giving it a second, independently settable name
        would only create a way for the two to disagree.

        No config at all (local mode, ad-hoc tests) ⇒ ``TaskQueueConfig``'s
        default, which is the column default on the deployed table, so a
        deployment that never set ``app_name`` keeps owning exactly the rows it
        already owned.

        A *present* but unusable value **raises** rather than falling back, and
        the fallback is the reason: the default is the *other* deployment's name
        as often as not, so quietly substituting it is how one backend starts
        claiming and failing another's tasks — the failure this column exists to
        prevent. Three rejections, each a value the ``app`` column cannot carry
        faithfully:

        - empty or whitespace-only — names no application at all;
        - leading or trailing whitespace — MySQL/OceanBase compare with a PAD
          SPACE collation, so ``"claw "`` and ``"claw"`` are one app there and
          two on SQLite, which is a divergence no test on SQLite can see;
        - longer than the stored width — a non-strict server truncates, and the
          rows are then filed under a name the claim filter never matches, so
          the work is enqueued and simply never runs.

        ``container.py`` resolves ``TaskQueueConfig`` eagerly at build time (as
        it does ``HttpClientPoolConfig``) so this raise stops the boot on every
        profile instead of being swallowed by lifecycle discovery — which would
        leave the app running with no worker and no explanation.
        """
        defaults = cfg.TaskQueueConfig()
        app = _app_name()
        if app is None:
            return defaults
        if not app.strip():
            raise ValueError(
                "app_name must name the application; it also owns this "
                "deployment's ac_task_queue rows, and it is empty"
            )
        if app != app.strip():
            raise ValueError(
                f"app_name must not have leading or trailing whitespace "
                f"({app!r}); it is stored on every ac_task_queue row, and "
                "MySQL/OceanBase compare with a PAD SPACE collation, so such a "
                "name would be a different app on SQLite than in production"
            )
        if len(app) > MAX_APP_LEN:
            raise ValueError(
                f"app_name exceeds {MAX_APP_LEN} chars ({len(app)}); it is "
                f"stored on every ac_task_queue row in a VARCHAR({MAX_APP_LEN}), "
                "and a non-strict MySQL/OceanBase would truncate it — this "
                "deployment would then enqueue rows under the truncated name and "
                "claim under the full one, so none of its own work would ever run"
            )
        return cfg.TaskQueueConfig(app=app)

    @singleton
    @provider
    def task_dispatch(self) -> cfg.TaskDispatchConfig:
        """Task dispatch policy.

        YAML shape::

            task_dispatch:
              task_search_skill_enabled: false
              skill_report_enabled: true              # true=skill HTTP Push, false=poller Pull

        The default keeps dispatch deterministic and avoids depending on the
        owner Bot's task-search skill. Set it to true to restore the skill
        round-trip for staged experiments.
        """
        block = _block("task_dispatch")
        defaults = cfg.TaskDispatchConfig()
        return cfg.TaskDispatchConfig(
            task_search_skill_enabled=_coerce(
                block,
                "task_search_skill_enabled",
                _as_bool,
                defaults.task_search_skill_enabled,
                "task_dispatch",
            ),
            skill_report_enabled=_coerce(
                block,
                "skill_report_enabled",
                _as_bool,
                defaults.skill_report_enabled,
                "task_dispatch",
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
