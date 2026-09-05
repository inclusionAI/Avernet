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

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.task_queue.types import DEFAULT_APP
from agentclaw.community.kernel.deploy_runtime import DeployRuntime


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


# ── Task runner dispatch (community, public) ────────────────────────────
#
# ``openapi_bot`` / ``bcs_client`` blocks — task ``single_bot`` / ``coop_group``
# dispatch config. Community (public) flavor: the secret-suffix fields
# (``api_key_secret`` / ``token_secret`` / ``secret_secret``) are LITERAL
# env-injected values served from ``application-community.yaml`` placeholders
# (``${api_key_secret:-}`` etc.) — the community build has no Mist, so they
# read as the real Bearer api_key / HMAC key+secret directly, mirroring how the
# corp ``corp_task_integration`` port provider consumes them. Empty -> port
# None / callback off (fail-closed).


@dataclass(frozen=True)
class OpenApiBotConfig:
    """``openapi_bot`` block — BaaS Open API single-bot dispatch (task ``single_bot``).

    Drives the community ``OpenApiBotAdapter`` (Bearer ``api_key`` against
    ``/openapi/v1/messages`` + ``/api/v1/api-keys/<prefix>/allowed-bots``).

    ``base_url`` / ``base_url_pre`` are env-aware hosts (non-secret), selected
    per ``get_current_env()`` — mirrors the ``bcn`` block convention
    (``base_url``=prod, ``base_url_pre``=pre). ``api_key_secret`` is the LITERAL
    Bearer ``api_key`` (env-injected, no Mist in the community build); empty
    -> the OpenApiBotPort stays None (fail-closed; ``single_bot`` dispatch
    degrades). ``api_key_prefix`` is the optional allowed-bots grant path
    segment; empty -> the adapter falls back to ``api_key[:10]``.
    """

    base_url: str = ""  # env-aware resolved prod host
    base_url_pre: str = ""  # env-aware pre host
    api_key_secret: str = ""  # LITERAL Bearer api_key (no Mist in community)
    api_key_prefix: str = ""


@dataclass(frozen=True)
class BcsClientConfig:
    """``bcs_client`` block — BCS coordinator HMAC client (task ``coop_group``).

    Drives the community ``BcsHttpAdapter`` (HMAC ``X-ECB-Token`` /
    ``X-ECB-Signature`` against ``/groups`` + ``/sessions``).

    Distinct from the ``bcn`` block: that block feeds the BCN management plane
    (Bearer ``provider_admin_token``); this block feeds the coordination plane
    (HMAC, group/session lifecycle) consumed by the coop-group task runner. The
    ``provider_id`` / ``provider_admin_token`` pair REUSES the ``bcn`` block
    identity (env-aware) for the task-mode roster path — empty bcn -> provider_id
    empty -> roster degrades (HMAC group creation still works).

    ``token_secret`` / ``secret_secret`` are the LITERAL HMAC key/secret
    (env-injected, no Mist in the community build); BOTH required or the port
    stays None (fail-closed; ``coop_group`` degrades, ``single_bot``
    unaffected). ``task_callback_url`` / ``task_callback_url_pre`` are
    env-aware callback hosts (the endpoint BCS posts task results back to) —
    non-secret, stay literal in YAML; empty -> the callback URL is not
    surfaced (callback off).
    """

    base_url: str = ""  # prod BCS coordinator host
    base_url_pre: str = ""  # pre BCS coordinator host
    token_secret: str = ""  # LITERAL HMAC key (no Mist in community)
    secret_secret: str = ""  # LITERAL HMAC secret (no Mist in community)
    task_callback_url: str = ""  # prod task-result callback host (BCS -> endpoint)
    task_callback_url_pre: str = ""  # pre task-result callback host (BCS -> endpoint)


@dataclass(frozen=True)
class MerchantTaskBotBindingsConfig:
    """Role-key -> bot uuid map for static-plan template bot binding resolution.

    Read from the ``merchant_task_bot_bindings`` block. In the public community
    profile the block is a single env-injected JSON string
    (``${MERCHANT_TASK_BOT_BINDINGS:-}``); the provider decodes it there. In
    internal profiles the block may instead be a structured YAML sub-tree.
    Empty/absent -> empty map: a bare boot never runs placeholder expansion (it
    only fires from ``from_file``/``from_yaml`` with a NON-EMPTY bindings map and
    matching ``${...}`` in a plan), so content routing/materialize stays green on a
    bare/CI build (placeholders stay literal) and only a real dispatch degrades.
    """
    bot_id_by_role: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BcsBindingConfig:
    """BCS channel-binding orchestration endpoint (yaml ``bcs_binding`` block).

    Neutral empty defaults: the community build embeds no BCS host or service
    token; corp overlays provide them. Empty ``base_url`` ⇒ the client raises
    :class:`ChannelSyncError` on use, so ``bcn_gateway`` channels are
    corp-overlay-only.
    """

    base_url: str = ""
    service_token: str = ""
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class GatewayConfig:
    """Public API gateway host (the ``gateway`` user_config block).

    ``base_url`` is the prod gateway and ``base_url_pre`` overrides it when
    env=='pre'. Held as an ``https://`` origin; the one consumer rewrites the
    scheme when it publishes a WebSocket URL.

    This is the host an external tenant is given, which is why it is separate
    from ``agentclawproxy``: the engine proxy stays the internal hop behind the
    gateway, and every other caller keeps addressing it directly.

    Neutral empty defaults — the community build fronts no gateway. Empty ⇒ the
    connection endpoint reports that this deployment has no gateway rather than
    publishing an address nothing serves.
    """

    base_url: str = ""
    base_url_pre: str = ""


@dataclass(frozen=True)
class GatewayEndpoint:
    """The gateway origin selected for the environment this process runs in.

    :class:`GatewayConfig` holds both hosts; this holds the one that applies.
    Separate because the choice is environment-driven wiring, which belongs to
    the composition root — a core service reading ``SERVER_ENV`` for itself
    would put deployment selection inside domain logic (``AGENTS.md``: raw
    environment access belongs in configuration loading, bootstrap, composition
    roots, or tests).

    Empty when this deployment fronts no gateway.
    """

    base_url: str = ""


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
    ``secret_name`` via the ``llm`` yaml block; a community deployment sets its
    own. The token is resolved from ``secret_name`` through the injected
    ``SecretResolver``; with the defaults empty, no token resolves and the
    harness LLM stays inert (``chat()`` returns ``[llm disabled]``).
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

    ``gateway_principal_signing_key`` is the exception, and deliberately so. It
    defaults to a **generic** name rather than empty — not a corp registry key,
    so it keeps the shipped source clean either way — because every profile
    needs *some* name for the lookup to happen at all. With an empty default,
    each profile had to register a name it would then never vary, so the value
    and the name were two config entries for one secret. Defaulting it means a
    deployment configures only the value, wherever its resolver reads that
    from; corp env overlays still override the name with the real Mist key.
    """

    dormant_internal_token: str = ""
    skill_center_internal_token: str = ""
    aiworkbench_repo_url: str = ""
    gateway_principal_signing_key: str = "gateway_principal_signing_key"
    aicoding_theta_master_key: str = ""


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


# ── Outbound HTTP transport ─────────────────────────────────────────────


@dataclass(frozen=True)
class HttpClientPoolPolicy:
    """Connection-pool + HTTP/2 settings for **one** ``HttpClient`` binding.

    ``max_connections``
        Hard ceiling on simultaneously open connections. It is **per binding and
        pool-wide** — not process-wide, and not per origin: the ``general``
        binding has ``base_url=""`` and its callers pass absolute URLs, so this
        one budget is shared across every host it addresses. Past the ceiling a
        request waits for a free connection and fails with
        ``HttpClientTimeoutError`` (``httpx.PoolTimeout``) once the per-call
        ``timeout`` elapses — the backpressure that keeps a burst of parallel
        callers from opening an unbounded number of sockets.
    ``max_keepalive_connections``
        How many of those stay open for reuse once idle (httpx clamps it to
        ``max_connections``).
    ``keepalive_expiry``
        Seconds an idle connection is kept. Keep this **below** the upstream's
        own idle timeout: a connection the server has already closed but the pool
        still believes is live surfaces as ``httpx.RemoteProtocolError`` on the
        next request to pick it up, and nothing retries that.
    ``http2``
        Negotiate HTTP/2 (request multiplexing over one connection) where the
        upstream offers it. Negotiation is via TLS ALPN, so it engages only on
        ``https://`` upstreams and stays on HTTP/1.1 for ``http://`` ones —
        httpx performs no cleartext h2c upgrade. Defaults off so it can be
        enabled per environment and per binding after the wire change has been
        watched somewhere safe.
    """

    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 5.0
    http2: bool = False


@dataclass(frozen=True)
class HttpClientPoolConfig:
    """Shared transport defaults plus sparse per-qualifier overrides.

    The four ``HttpClient`` bindings front different upstreams with different
    traffic shapes — ``general`` alone carries LLM SSE streams and container
    calls to many origins — so each resolves its own policy rather than all four
    sharing one.

    ``for_qualifier`` resolves **whole-policy**: a qualifier either has an
    override or gets ``defaults``, with no field-level merging at the call site.
    The provider is what makes a sparse override total, by building each one
    starting from the resolved defaults. Merging at read time instead would let a
    half-specified override drift silently as the shared defaults change, which
    is not what someone reading the YAML would predict.
    """

    defaults: HttpClientPoolPolicy = field(default_factory=HttpClientPoolPolicy)
    overrides: Mapping[str, HttpClientPoolPolicy] = field(default_factory=dict)

    def for_qualifier(self, qualifier: str) -> HttpClientPoolPolicy:
        """Effective policy for one binding: its override, else the defaults."""
        return self.overrides.get(qualifier, self.defaults)


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
    worker_id_with_owner: bool = False
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
    # Eval (sandbox) ARCA template uuid for evaluation environment — deploy supplies it.
    eval_template_uuid: str = ""
    # Personal bot via BaaS (poolab template) — deploy supplies it.
    personal_bot_template_uuid: str = ""


@dataclass(frozen=True)
class DeployRuntimeConfig:
    """Which container this deployment runs its bots in.

    Its own type rather than a field on ``BaasConfig`` because it selects a
    component: ``ServiceBotModule`` injects this to pick a
    ``DeployConfigComposer``, and it has no business receiving the BaaS
    endpoint and template uuids to read one enum out of them.

    Sourced from ``baas.deploy_runtime`` — it stays in the ``baas`` yaml block
    because it shapes exactly one thing, the payload posted to the BaaS
    create-bot API. The default keeps every existing deployment on the managed
    bot image; an unrecognized value fails at config load rather than falling
    back, because a deployment that silently composed the wrong image's payload
    would create bots that start and do not work.
    """

    runtime: DeployRuntime = DeployRuntime.MANAGED


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
        aicoding_root: Same shape, for AICoding bots.
        hermes_root: Same shape, for Hermes bots.
    """

    openclaw_root: str = "/home/admin/.openclaw"
    claude_code_root: str = "/home/admin/.claude_code"
    claude_code_session_root: str = "/home/admin/.claude"
    aicoding_root: str = "/home/admin/.aicoding"
    hermes_root: str = "/home/admin/.hermes"


# ── Creating a bot with its configuration manifest (W13) ─────────────────


@dataclass(frozen=True)
class BotCreateWithManifestConfig:
    """Policy for ``POST /openapi/v1/bots/with-manifest``.

    Sourced from the ``bot_create_with_manifest`` block of ``user_config``.

    ``authorization_window_seconds`` is how long a user has to follow the
    authorization link before the creation is retired and the rows submission
    wrote are deleted. It is frozen into the job's payload at enqueue rather
    than re-read per step, so changing it here moves only *future* creations —
    an in-flight one keeps the window it was submitted under, which is what
    keeps the queue's own deadline the safe margin longer that it is meant
    to be.

    Must be positive: a zero or negative window would expire every creation the
    instant it was submitted, so a non-positive value is refused in favour of
    the default rather than honoured.
    """

    authorization_window_seconds: int = 10 * 60

    @classmethod
    def from_block(cls, block: dict) -> "BotCreateWithManifestConfig":
        """Parse the ``bot_create_with_manifest`` block, defaulting defensively.

        A missing key, an unreadable value and a non-positive one all take the
        default. Non-positive is *refused* rather than honoured: zero would
        expire every creation at the instant of submission, which from a
        caller's side is indistinguishable from the feature being broken.
        """
        default = cls().authorization_window_seconds
        try:
            window = int(block.get("authorization_window_seconds", default))
        except (TypeError, ValueError):
            window = default
        return cls(authorization_window_seconds=window if window > 0 else default)


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
class SkillCenterInternalToken:
    """Resolved bearer token for ``/api/internal/skill-center/*`` endpoints.

    Produced by ``SkillCenterInternalTokenBindings._resolved_internal_token``,
    with the same
    resolution rules and the same failure-closed empty default as
    ``DormantInternalToken``: an empty ``value`` makes the auth Depends 401
    every request rather than authorize an unverified caller.

    Separate from the dormant token on purpose — these endpoints converge
    capability state for whole pages of Bots, so the two operations are
    granted independently.
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
class TaskQueueConfig:
    """Which application owns this deployment's ``ac_task_queue`` rows.

    Sourced from the **top-level** ``app_name`` — the deployment's own identity.

    One table is shared by more than one independent backend, so a row carries
    an ``app`` naming its owner: enqueue stamps it, and every query that selects
    work matches it. Without that, each fleet claims the other's tasks and fails
    them for an unregistered ``task_type``.

    It is deliberately *not* part of ``TaskQueueWorkerConfig``: the same value
    has to be used by the enqueue path, which has nothing to do with worker
    policy, and turning the worker off must not stop enqueued rows from being
    stamped with their owner.

    ``app`` is validated where it is read (see ``ConfigModule.task_queue``) —
    it is a scope column, and a value the column cannot hold faithfully would
    file rows under a name the claim filter never matches.
    """

    app: str = DEFAULT_APP


@dataclass(frozen=True)
class TaskDispatchConfig:
    """Task dispatch policy switches (``task_dispatch`` user_config block).

    The deterministic candidate-count rule is the safe default. Set
    ``task_search_skill_enabled: true`` only when the owner Bot task-search
    round-trip should decide the dispatch shape. ``skill_report_enabled``
    selects the unified task result Push/Pull protocol and defaults to true.
    """

    task_search_skill_enabled: bool = False
    skill_report_enabled: bool = True


@dataclass(frozen=True)
class TaskQueueWorkerConfig:
    """In-process distributed-task-queue worker policy.

    Sourced from the ``task_queue_worker`` block of ``user_config``.

    BaaS and Teclaw production handlers are registered. The worker remains
    disabled by default until an environment provisions ``ac_task_queue`` and
    explicitly sets ``enabled: true``.

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
    # Backend card-callback URL for the TC-card React component's fetch POST.
    # Env-aware: pre/prod point at different callback endpoints.
    # Source: YAML ``economy_governance.iframe_callback_url`` (corp overlay) or
    # ``economy_governance.iframe_callback_url_pre`` (pre-env).
    # Empty ⇒ the detailLink carries no callbackUrl (feedback form non-functional).
    iframe_callback_url: str = ""


@dataclass(frozen=True)
class BotConfigManifestConfig:
    """Manifest composition config (the ``user_config.bot_config_manifest``
    block).

    Both fields are consumed by apply's machine parts, each through its own
    pure parser: the guarded fetcher (W2) takes the transport allowlist, the
    content store (W11) takes the blob tree root. Neutral defaults ship with
    the neutral base — a deployment's env overlay decides a mirror or a NAS
    volume.

    Attributes:
        fetch_transport_allowlist: Hosts exempt from the https-only and
            public-only transport rules (exact-host matches, sorted for a
            stable resolution order).
        content_store_dir: The content store's blob root — relative paths
            resolve against the process working directory, ``~`` expands.
        teclaw_platform_managed: The W8 switch (see the field comment).
    """

    fetch_transport_allowlist: tuple[str, ...] = ()
    content_store_dir: str = "./data/manifest_content"
    #: W8: whether teclaw bots take the platform-managed delivery path
    #: (materialise into the bot-data store + index, deliver by artifact with
    #: the ``ownership`` map). Off until the teclaw engine supports the map;
    #: off means the pre-W8 per-file shape. Read only by the delivery
    #: strategy factory.
    teclaw_platform_managed: bool = False
