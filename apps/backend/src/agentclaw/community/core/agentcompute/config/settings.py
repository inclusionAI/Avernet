from __future__ import annotations

from dataclasses import dataclass, field


# ── yaml-block parsing (the `agentcompute` user_config block) ─────────────
#
# Defensive parsing in the BotCreateWithManifestConfig.from_block tradition:
# a missing key, an unreadable value and a non-positive one all take the
# default rather than breaking boot — a half-misconfigured knob downgrades
# to its tested default, it never wedges the worker. The a-bare-default
# contract matters as much: an absent block parses to exactly what
# ``AgentComputeConfig()`` constructs, so profiles that ship no block keep
# their current behavior (missing LLM stays fail-clear at submit, /tmp
# artifact root, every limit at its default).


def _as_int(block: dict, key: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(block.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _as_float(block: dict, key: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(block.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _as_bool(block: dict, key: str, default: bool) -> bool:
    value = block.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_str(block: dict, key: str, default: str = "") -> str:
    value = block.get(key, default)
    return value if isinstance(value, str) else default


@dataclass(frozen=True)
class LlmConfig:
    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    orchestrator_model: str | None = None
    timeout_seconds: float = 60.0
    max_tokens: int = 4096
    max_retries: int = 3


    @classmethod
    def from_block(cls, block: dict) -> "LlmConfig | None":
        """Parse the ``agentcompute.llm`` block; ``None`` when unconfigured.

        ``None`` is the fail-clear state the submit gate answers with a 400 —
        an empty ``base_url``/``default_model`` after env expansion means no
        LLM is wired, whether the block is absent or its placeholders came up
        empty. A configured block parses every knob defensively.
        """
        block = block or {}
        base_url = _as_str(block, "base_url").strip()
        model = _as_str(block, "default_model").strip()
        if not base_url or not model:
            return None
        orchestrator_model = _as_str(block, "orchestrator_model").strip()
        key = _as_str(block, "api_key")
        return cls(
            base_url=base_url,
            api_key=key,
            default_model=model,
            orchestrator_model=orchestrator_model or None,
            timeout_seconds=_as_float(block, "timeout_seconds", cls().timeout_seconds, minimum=0.0),
            max_tokens=_as_int(block, "max_tokens", cls().max_tokens, minimum=1),
            max_retries=_as_int(block, "max_retries", cls().max_retries, minimum=0),
        )


@dataclass(frozen=True)
class AgentComputeOrchestrationConfig:
    max_turns: int = 50
    max_total_agents: int = 100
    max_width: int = 8
    max_depth: int = 5
    repair_max: int = 2
    subagent_max_attempts: int = 3
    per_subagent_timeout: float = 120.0
    orchestrator_turn_interval: float = 1.0
    slice_max_seconds: float = 300.0
    slice_interval: float = 1.0
    respawn_max: int = 3
    reclaim_max: int = 5
    lease_seconds: int = 60
    poll_interval: float = 2.0
    poll_jitter: float = 0.5
    batch_size: int = 10

    @classmethod
    def from_block(cls, block: dict) -> "AgentComputeOrchestrationConfig":
        d = cls()
        block = block or {}
        return cls(
            max_turns=_as_int(block, "max_turns", d.max_turns, minimum=1),
            max_total_agents=_as_int(block, "max_total_agents", d.max_total_agents, minimum=1),
            max_width=_as_int(block, "max_width", d.max_width, minimum=1),
            max_depth=_as_int(block, "max_depth", d.max_depth, minimum=1),
            repair_max=_as_int(block, "repair_max", d.repair_max, minimum=0),
            subagent_max_attempts=_as_int(block, "subagent_max_attempts", d.subagent_max_attempts, minimum=1),
            per_subagent_timeout=_as_float(block, "per_subagent_timeout", d.per_subagent_timeout),
            orchestrator_turn_interval=_as_float(block, "orchestrator_turn_interval", d.orchestrator_turn_interval),
            slice_max_seconds=_as_float(block, "slice_max_seconds", d.slice_max_seconds),
            slice_interval=_as_float(block, "slice_interval", d.slice_interval),
            respawn_max=_as_int(block, "respawn_max", d.respawn_max, minimum=0),
            reclaim_max=_as_int(block, "reclaim_max", d.reclaim_max, minimum=0),
            lease_seconds=_as_int(block, "lease_seconds", d.lease_seconds, minimum=1),
            poll_interval=_as_float(block, "poll_interval", d.poll_interval),
            poll_jitter=_as_float(block, "poll_jitter", d.poll_jitter),
            batch_size=_as_int(block, "batch_size", d.batch_size, minimum=1),
        )


@dataclass(frozen=True)
class AgentComputeTenantLimits:
    max_concurrent_runs: int = 8
    max_fanout_width: int = 8

    @classmethod
    def from_block(cls, block: dict) -> "AgentComputeTenantLimits":
        d = cls()
        block = block or {}
        return cls(
            max_concurrent_runs=_as_int(block, "max_concurrent_runs", d.max_concurrent_runs, minimum=1),
            max_fanout_width=_as_int(block, "max_fanout_width", d.max_fanout_width, minimum=1),
        )


@dataclass(frozen=True)
class AgentComputeAdmissionConfig:
    max_global_concurrent_runs: int = 64
    max_pending_jobs: int = 500

    @classmethod
    def from_block(cls, block: dict) -> "AgentComputeAdmissionConfig":
        d = cls()
        block = block or {}
        return cls(
            max_global_concurrent_runs=_as_int(block, "max_global_concurrent_runs", d.max_global_concurrent_runs, minimum=1),
            max_pending_jobs=_as_int(block, "max_pending_jobs", d.max_pending_jobs, minimum=0),
        )


@dataclass(frozen=True)
class AgentComputeDrainConfig:
    grace_seconds: float = 30.0
    stop_claiming_signal: str = "SIGTERM"

    @classmethod
    def from_block(cls, block: dict) -> "AgentComputeDrainConfig":
        d = cls()
        block = block or {}
        return cls(
            grace_seconds=_as_float(block, "grace_seconds", d.grace_seconds),
            stop_claiming_signal=_as_str(block, "stop_claiming_signal", d.stop_claiming_signal),
        )


@dataclass(frozen=True)
class AgentComputeUpstreamBreakerConfig:
    failure_threshold: int = 5
    open_seconds: float = 60.0

    @classmethod
    def from_block(cls, block: dict) -> "AgentComputeUpstreamBreakerConfig":
        d = cls()
        block = block or {}
        return cls(
            failure_threshold=_as_int(block, "failure_threshold", d.failure_threshold, minimum=1),
            open_seconds=_as_float(block, "open_seconds", d.open_seconds),
        )


@dataclass(frozen=True)
class AgentComputeEgressConfig:
    allowed_hosts: tuple[str, ...] = ()
    require_https: bool = False

    @classmethod
    def from_block(cls, block: dict) -> "AgentComputeEgressConfig":
        d = cls()
        block = block or {}
        hosts = block.get("allowed_hosts", list(d.allowed_hosts))
        allowed_hosts = tuple(h for h in hosts if isinstance(h, str) and h.strip()) if isinstance(hosts, (list, tuple)) else d.allowed_hosts
        return cls(
            allowed_hosts=allowed_hosts,
            require_https=_as_bool(block, "require_https", d.require_https),
        )


@dataclass(frozen=True)
class AgentComputeRetentionConfig:
    artifact_ttl_days: int = 7
    sweep_interval_seconds: float = 3600.0

    @classmethod
    def from_block(cls, block: dict) -> "AgentComputeRetentionConfig":
        d = cls()
        block = block or {}
        return cls(
            artifact_ttl_days=_as_int(block, "artifact_ttl_days", d.artifact_ttl_days, minimum=1),
            sweep_interval_seconds=_as_float(block, "sweep_interval_seconds", d.sweep_interval_seconds),
        )


@dataclass(frozen=True)
class AgentComputeConfig:
    llm: LlmConfig | None = None
    orchestration: AgentComputeOrchestrationConfig = field(
        default_factory=AgentComputeOrchestrationConfig
    )
    tenant_limits: AgentComputeTenantLimits = field(
        default_factory=AgentComputeTenantLimits
    )
    admission: AgentComputeAdmissionConfig = field(
        default_factory=AgentComputeAdmissionConfig
    )
    drain: AgentComputeDrainConfig = field(
        default_factory=AgentComputeDrainConfig
    )
    upstream_breaker: AgentComputeUpstreamBreakerConfig = field(
        default_factory=AgentComputeUpstreamBreakerConfig
    )
    retention: AgentComputeRetentionConfig = field(
        default_factory=AgentComputeRetentionConfig
    )
    egress: AgentComputeEgressConfig = field(
        default_factory=AgentComputeEgressConfig
    )
    artifact_store_root: str = "/tmp/agentcompute/artifacts"
    artifact_inline_threshold: int = 16384
    message_retention_tail: int = 50
    enabled: bool = True

    @classmethod
    def from_block(cls, block: dict) -> "AgentComputeConfig":
        """Parse the ``agentcompute`` user_config block.

        An absent or empty block parses to exactly what ``AgentComputeConfig()``
        constructs, so a profile that ships no block keeps the bare-defaults
        contract (missing LLM = fail-clear at submit). Env-expanded blanks
        land the same way: placeholders like ``${AGENTCOMPUTE_LLM_BASE_URL:-}``
        that come up empty leave the LLM unconfigured.
        """
        block = block or {}
        return cls(
            llm=LlmConfig.from_block(block.get("llm")),
            orchestration=AgentComputeOrchestrationConfig.from_block(
                block.get("orchestration")),
            tenant_limits=AgentComputeTenantLimits.from_block(
                block.get("tenant_limits")),
            admission=AgentComputeAdmissionConfig.from_block(
                block.get("admission")),
            drain=AgentComputeDrainConfig.from_block(block.get("drain")),
            upstream_breaker=AgentComputeUpstreamBreakerConfig.from_block(
                block.get("upstream_breaker")),
            retention=AgentComputeRetentionConfig.from_block(
                block.get("retention")),
            egress=AgentComputeEgressConfig.from_block(block.get("egress")),
            artifact_store_root=_as_str(
                block, "artifact_store_root", cls().artifact_store_root),
            artifact_inline_threshold=_as_int(
                block, "artifact_inline_threshold",
                cls().artifact_inline_threshold, minimum=1),
            message_retention_tail=_as_int(
                block, "message_retention_tail",
                cls().message_retention_tail, minimum=0),
            enabled=_as_bool(block, "enabled", True),
        )

