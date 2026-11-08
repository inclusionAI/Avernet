from __future__ import annotations

from dataclasses import dataclass, field


# ── yaml-block parsing (the `agentswarm` user_config block) ─────────────
#
# Defensive parsing in the BotCreateWithManifestConfig.from_block tradition:
# a missing key, an unreadable value and a below-minimum one all take the
# default rather than breaking boot — a half-misconfigured knob downgrades
# to its tested default, it never wedges the worker. Budget/cadence knobs
# (poll/lease/slice/timeout/sweep/grace, pending capacity) carry strict
# minimums: a zero poll interval is a hot loop, a zero slice/timeout/sweep
# budget or pending capacity wedges the worker. ``open_seconds`` alone
# still accepts 0 — an immediate-retry breaker is a valid setting, pinned
# at the runtime level by the drain-breaker tests. The bare-default
# contract matters as much: an absent block parses to exactly what
# ``AgentSwarmConfig()`` constructs, so profiles that ship no block keep
# their current behavior (missing LLM stays fail-clear at submit, /tmp
# artifact root, every limit at its default).


def _as_int(block: dict, key: str, default: int, minimum: int = 0,
            exclusive: bool = False) -> int:
    try:
        value = int(block.get(key, default))
    except (TypeError, ValueError):
        return default
    if exclusive and value <= minimum:
        return default
    return value if value >= minimum else default


def _as_float(block: dict, key: str, default: float, minimum: float = 0.0,
              exclusive: bool = False) -> float:
    try:
        value = float(block.get(key, default))
    except (TypeError, ValueError):
        return default
    if exclusive and value <= minimum:
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


def _as_path(block: dict, key: str, default: str) -> str:
    value = block.get(key, default)
    return value if isinstance(value, str) and value.strip() else default


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
        """Parse the ``llm`` yaml block — the SAME block the harness reads.

        ``base_url`` and ``secret_name`` come from the existing llm yaml
        configuration; ``api_key`` carries the secret_name (DI resolves via
        SecretResolver). ``model`` and ``timeout_ms`` read the block's
        per-deployment knobs. None gravitates to the fail-clear 400.
        """
        block = block or {}
        base_url = _as_str(block, "base_url").strip()
        model = _as_str(block, "model").strip() or "glm-5.2"
        if not base_url or not model:
            return None
        secret_name = _as_str(block, "secret_name").strip()
        return cls(
            base_url=base_url,
            api_key=secret_name,
            default_model=model,
            orchestrator_model=_as_str(block, "orchestrator_model").strip() or None,
            timeout_seconds=_as_float(block, "timeout_ms", cls().timeout_seconds * 1000.0, exclusive=True) / 1000.0,
            max_tokens=_as_int(block, "max_tokens", cls().max_tokens, minimum=1),
            max_retries=_as_int(block, "max_retries", cls().max_retries, minimum=0),
        )


@dataclass(frozen=True)
class AgentSwarmOrchestrationConfig:
    max_turns: int = 50
    max_total_agents: int = 100
    max_width: int = 8
    max_depth: int = 5
    repair_max: int = 2
    agent_max_attempts: int = 3
    per_attempt_timeout: float = 300.0
    setup_timeout: float = 300.0
    orchestrator_turn_interval: float = 1.0
    slice_max_seconds: float = 900.0
    slice_interval: float = 1.0
    respawn_max: int = 3
    reclaim_max: int = 5
    lease_seconds: int = 60
    poll_interval: float = 2.0
    poll_jitter: float = 0.5
    batch_size: int = 10
    wall_clock_seconds: float = 3600.0
    no_progress_threshold: int = 3
    # Simulator-only fault injection: artificial delay inside each
    # subagent's setup() — llm and bot kind alike — defaulting to zero
    # everywhere that does not configure it (prod/pre never set the key).
    subagent_setup_delay_seconds: float = 0.0

    @classmethod
    def from_block(cls, block: dict) -> "AgentSwarmOrchestrationConfig":
        d = cls()
        block = block or {}
        cfg = cls(
            max_turns=_as_int(block, "max_turns", d.max_turns, minimum=1),
            max_total_agents=_as_int(block, "max_total_agents", d.max_total_agents, minimum=1),
            max_width=_as_int(block, "max_width", d.max_width, minimum=1),
            max_depth=_as_int(block, "max_depth", d.max_depth, minimum=1),
            repair_max=_as_int(block, "repair_max", d.repair_max, minimum=0),
            agent_max_attempts=_as_int(block, "agent_max_attempts", d.agent_max_attempts, minimum=1),
            per_attempt_timeout=_as_float(block, "per_attempt_timeout", d.per_attempt_timeout),
            setup_timeout=_as_float(block, "setup_timeout", d.setup_timeout),
            orchestrator_turn_interval=_as_float(block, "orchestrator_turn_interval", d.orchestrator_turn_interval),
            slice_max_seconds=_as_float(block, "slice_max_seconds", d.slice_max_seconds, exclusive=True),
            slice_interval=_as_float(block, "slice_interval", d.slice_interval, exclusive=True),
            respawn_max=_as_int(block, "respawn_max", d.respawn_max, minimum=0),
            reclaim_max=_as_int(block, "reclaim_max", d.reclaim_max, minimum=0),
            lease_seconds=_as_int(block, "lease_seconds", d.lease_seconds, minimum=1),
            poll_interval=_as_float(block, "poll_interval", d.poll_interval, exclusive=True),
            poll_jitter=_as_float(block, "poll_jitter", d.poll_jitter),
            batch_size=_as_int(block, "batch_size", d.batch_size, minimum=1),
            # Termination knobs (the TerminationEvaluator's budget): zero
            # wedges every run — an instant WALL_CLOCK / NO_PROGRESS at the
            # first evaluate — so both downgrade like other budget knobs.
            wall_clock_seconds=_as_float(
                block, "wall_clock_seconds", d.wall_clock_seconds, exclusive=True),
            no_progress_threshold=_as_int(
                block, "no_progress_threshold", d.no_progress_threshold, minimum=1),
            subagent_setup_delay_seconds=_as_float(
                block, "subagent_setup_delay_seconds", d.subagent_setup_delay_seconds),
        )
        # A single attempt is setup() then run_task(); if their budgets do
        # not fit inside one slice, the slice cancels the attempt mid-phase,
        # no completion is produced, and the agent wedges non-terminal
        # forever. Fail clear at config load rather than wedge at runtime.
        if (cfg.setup_timeout + cfg.per_attempt_timeout) > cfg.slice_max_seconds:
            raise ValueError(
                "orchestration.setup_timeout + per_attempt_timeout "
                f"({cfg.setup_timeout} + {cfg.per_attempt_timeout} = "
                f"{cfg.setup_timeout + cfg.per_attempt_timeout}s) exceeds "
                f"slice_max_seconds ({cfg.slice_max_seconds}s): an attempt "
                "cannot finish inside a slice and every agent would wedge."
            )
        # The worker renews a held lease every lease_seconds/3 on a dedicated
        # wall-clock thread (starvation-proof), so the lease need not outlast
        # a whole slice. Very small leases still leave no room for a single
        # renewal round-trip plus jitter, and a lapsed lease hands the dispatch to
        # a peer's reclaim — fail clear at config load.
        if cfg.lease_seconds < 30:
            raise ValueError(
                f"orchestration.lease_seconds ({cfg.lease_seconds}) too low; "
                "the heartbeat renews every lease/3 and a lost lease reclaims "
                "the dispatch — use >= 30"
            )
        return cfg


@dataclass(frozen=True)
class AgentSwarmTenantLimits:
    max_concurrent_runs: int = 8

    @classmethod
    def from_block(cls, block: dict) -> "AgentSwarmTenantLimits":
        d = cls()
        block = block or {}
        return cls(
            max_concurrent_runs=_as_int(block, "max_concurrent_runs", d.max_concurrent_runs, minimum=1),
        )


@dataclass(frozen=True)
class AgentSwarmAdmissionConfig:
    max_global_concurrent_runs: int = 64
    max_pending_dispatches: int = 500

    @classmethod
    def from_block(cls, block: dict) -> "AgentSwarmAdmissionConfig":
        d = cls()
        block = block or {}
        return cls(
            max_global_concurrent_runs=_as_int(block, "max_global_concurrent_runs", d.max_global_concurrent_runs, minimum=1),
            max_pending_dispatches=_as_int(block, "max_pending_dispatches", d.max_pending_dispatches, minimum=1),
        )


@dataclass(frozen=True)
class AgentSwarmDrainConfig:
    grace_seconds: float = 30.0
    stop_claiming_signal: str = "SIGTERM"

    @classmethod
    def from_block(cls, block: dict) -> "AgentSwarmDrainConfig":
        d = cls()
        block = block or {}
        return cls(
            grace_seconds=_as_float(block, "grace_seconds", d.grace_seconds, exclusive=True),
            stop_claiming_signal=_as_str(block, "stop_claiming_signal", d.stop_claiming_signal),
        )


@dataclass(frozen=True)
class AgentSwarmUpstreamBreakerConfig:
    failure_threshold: int = 5
    open_seconds: float = 60.0

    @classmethod
    def from_block(cls, block: dict) -> "AgentSwarmUpstreamBreakerConfig":
        d = cls()
        block = block or {}
        return cls(
            failure_threshold=_as_int(block, "failure_threshold", d.failure_threshold, minimum=1),
            open_seconds=_as_float(block, "open_seconds", d.open_seconds),
        )


@dataclass(frozen=True)
class AgentSwarmMaintenanceConfig:
    """The ``agentswarm.maintenance`` block — worker-hosted maintenance loops.

    The orphan reaper (async-run-and-resume D-C) is the block's tenant: a
    jittered sweep at ``reaper_interval_seconds`` cadence that re-enqueues
    a fresh READY dispatch for queue-orphans and forces fenced TIMED_OUT on
    runs past their wall-clock deadline. ``enabled`` is the single-knob
    rollback — false stops the loop without touching the worker or the
    queue itself.
    """

    enabled: bool = True
    reaper_interval_seconds: float = 300.0

    @classmethod
    def from_block(cls, block: dict) -> "AgentSwarmMaintenanceConfig":
        d = cls()
        block = block or {}
        return cls(
            enabled=_as_bool(block, "enabled", d.enabled),
            reaper_interval_seconds=_as_float(
                block, "reaper_interval_seconds",
                d.reaper_interval_seconds, exclusive=True),
        )


@dataclass(frozen=True)
class AgentSwarmExecutorConfig:
    """The ``agentswarm.executor`` block — platform-bridge concurrency.

    ``max_platform_workers`` bounds the module-scoped thread pool that
    carries the sync ``BotPlatform`` legs (create/status/delete/chat) off
    the event loop (async-run-and-resume D-B): the pool caps how many
    platform calls one pod may have in flight, below and independently of
    ``max_width`` agent slots.
    """

    max_platform_workers: int = 4

    @classmethod
    def from_block(cls, block: dict) -> "AgentSwarmExecutorConfig":
        d = cls()
        block = block or {}
        return cls(
            max_platform_workers=_as_int(
                block, "max_platform_workers",
                d.max_platform_workers, minimum=1),
        )


@dataclass(frozen=True)
class AgentSwarmEgressConfig:
    allowed_hosts: tuple[str, ...] = ()
    require_https: bool = False

    @classmethod
    def from_block(cls, block: dict) -> "AgentSwarmEgressConfig":
        d = cls()
        block = block or {}
        hosts = block.get("allowed_hosts", list(d.allowed_hosts))
        allowed_hosts = tuple(h for h in hosts if isinstance(h, str) and h.strip()) if isinstance(hosts, (list, tuple)) else d.allowed_hosts
        return cls(
            allowed_hosts=allowed_hosts,
            require_https=_as_bool(block, "require_https", d.require_https),
        )


@dataclass(frozen=True)
class AgentSwarmRetentionConfig:
    artifact_ttl_days: int = 7
    sweep_interval_seconds: float = 3600.0

    @classmethod
    def from_block(cls, block: dict) -> "AgentSwarmRetentionConfig":
        d = cls()
        block = block or {}
        return cls(
            artifact_ttl_days=_as_int(block, "artifact_ttl_days", d.artifact_ttl_days, minimum=1),
            sweep_interval_seconds=_as_float(block, "sweep_interval_seconds", d.sweep_interval_seconds, exclusive=True),
        )


@dataclass(frozen=True)
class AgentSwarmConfig:
    llm: LlmConfig | None = None
    orchestration: AgentSwarmOrchestrationConfig = field(
        default_factory=AgentSwarmOrchestrationConfig
    )
    tenant_limits: AgentSwarmTenantLimits = field(
        default_factory=AgentSwarmTenantLimits
    )
    admission: AgentSwarmAdmissionConfig = field(
        default_factory=AgentSwarmAdmissionConfig
    )
    drain: AgentSwarmDrainConfig = field(
        default_factory=AgentSwarmDrainConfig
    )
    upstream_breaker: AgentSwarmUpstreamBreakerConfig = field(
        default_factory=AgentSwarmUpstreamBreakerConfig
    )
    maintenance: AgentSwarmMaintenanceConfig = field(
        default_factory=AgentSwarmMaintenanceConfig
    )
    executor: AgentSwarmExecutorConfig = field(
        default_factory=AgentSwarmExecutorConfig
    )
    retention: AgentSwarmRetentionConfig = field(
        default_factory=AgentSwarmRetentionConfig
    )
    egress: AgentSwarmEgressConfig = field(
        default_factory=AgentSwarmEgressConfig
    )
    artifact_store_root: str = "/tmp/agentswarm/artifacts"
    artifact_inline_threshold: int = 16384
    message_retention_tail: int = 50
    enabled: bool = True

    @classmethod
    def from_block(cls, block: dict) -> "AgentSwarmConfig":
        """Parse the ``agentswarm`` user_config block.

        An absent or empty block parses to exactly what ``AgentSwarmConfig()``
        constructs, so a profile that ships no block keeps the bare-defaults
        contract (missing LLM = fail-clear at submit). Env-expanded blanks
        land the same way: placeholders like ``${AGENTSWARM_LLM_BASE_URL:-}``
        that come up empty leave the LLM unconfigured.
        """
        block = block or {}
        return cls(
            llm=LlmConfig.from_block(block.get("llm")),
            orchestration=AgentSwarmOrchestrationConfig.from_block(
                block.get("orchestration")),
            tenant_limits=AgentSwarmTenantLimits.from_block(
                block.get("tenant_limits")),
            admission=AgentSwarmAdmissionConfig.from_block(
                block.get("admission")),
            drain=AgentSwarmDrainConfig.from_block(block.get("drain")),
            upstream_breaker=AgentSwarmUpstreamBreakerConfig.from_block(
                block.get("upstream_breaker")),
            maintenance=AgentSwarmMaintenanceConfig.from_block(
                block.get("maintenance")),
            executor=AgentSwarmExecutorConfig.from_block(
                block.get("executor")),
            retention=AgentSwarmRetentionConfig.from_block(
                block.get("retention")),
            egress=AgentSwarmEgressConfig.from_block(block.get("egress")),
            artifact_store_root=_as_path(
                block, "artifact_store_root", cls().artifact_store_root),
            artifact_inline_threshold=_as_int(
                block, "artifact_inline_threshold",
                cls().artifact_inline_threshold, minimum=1),
            message_retention_tail=_as_int(
                block, "message_retention_tail",
                cls().message_retention_tail, minimum=0),
            enabled=_as_bool(block, "enabled", True),
        )

