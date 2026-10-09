from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LlmConfig:
    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    orchestrator_model: str | None = None
    timeout_seconds: float = 60.0
    max_tokens: int = 4096
    max_retries: int = 3


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


@dataclass(frozen=True)
class AgentComputeAvernetConfig:
    backend_base_url: str = "http://127.0.0.1:8000"
    bot_create_path: str = "/openapi/v1/bots/with-manifest"
    bot_status_path: str = "/openapi/v1/bots/{bot_id}/status"
    bot_delete_path: str = "/openapi/v1/bots/{bot_id}"
    http_timeout: float = 60.0
    http_retries: int = 2


@dataclass(frozen=True)
class AgentComputeTenantLimits:
    max_concurrent_runs: int = 8
    max_fanout_width: int = 8


@dataclass(frozen=True)
class AgentComputeConfig:
    llm: LlmConfig | None = None
    orchestration: AgentComputeOrchestrationConfig = field(
        default_factory=AgentComputeOrchestrationConfig
    )
    avernet: AgentComputeAvernetConfig = field(
        default_factory=AgentComputeAvernetConfig
    )
    tenant_limits: AgentComputeTenantLimits = field(
        default_factory=AgentComputeTenantLimits
    )
    artifact_store_root: str = "/tmp/agentcompute/artifacts"
    artifact_inline_threshold: int = 16384
    message_retention_tail: int = 50
    enabled: bool = True