from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentclaw.community.core.agentswarm.domain.models import (
    AgentKind, ProviderKind,
)


class AgentSpecDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=128)
    role: str = Field(max_length=20000)
    instructions: str = Field(default="", max_length=20000)
    kind: AgentKind = AgentKind.LLM
    model: str | None = None
    engine: str | None = None
    bot_type: str | None = None
    identity_file: str | None = None
    metadata: dict[
        Annotated[str, Field(max_length=200)],
        Annotated[str, Field(max_length=2000)],
    ] = Field(default_factory=dict, max_length=32)
    config_manifest: str = Field(
        default="", max_length=200_000,
        description=(
            "Full five-category bot_config_manifest YAML document for "
            "this agent (mcp/resources/skills/cli_tools/identity + "
            "sources + script), exactly as POST /openapi/v1/bots/"
            "with-manifest accepts it. When present, the swarm "
            "provisions the agent's bot from this document verbatim; "
            "the platform's preflight validates its grammar."
        ),
    )


class RunOptionsDTO(BaseModel):
    """Per-request operational overrides — values absent from the request
    fall back to the deployment's `agentswarm.orchestration.*` config.

    Keys mirror the yaml config keys so operators think in one
    vocabulary. Honored at runtime, clamped to deployment authority:
    model_name (provider default when unset), max_width and
    max_attempts (tighten-only), per_attempt_timeout_seconds and
    slice_max_seconds (tighten-only), and setup_timeout_seconds —
    which may raise up to the deployment's slice room
    (slice_max_seconds minus per_attempt_timeout), so slow-env
    provisioning can be re-budgeted from the request alone.
    max_turns and max_total_agents are persisted with the run but do
    not override the deployment's orchestrator budgets."""

    model_config = ConfigDict(extra="forbid")

    model_name: str | None = Field(
        default=None, max_length=128,
        description="Model for agents that don't specify their own.")
    slice_max_seconds: float | None = Field(
        default=None, gt=0, le=3600,
        description="Orchestrator deadline per slice (seconds).")
    per_attempt_timeout_seconds: float | None = Field(
        default=None, gt=0, le=600,
        description="Upper bound for one attempt task (seconds).")
    setup_timeout_seconds: float | None = Field(
        default=None, gt=0, le=900,
        description="Upper bound for one agent's provisioning phase "
        "(spawn + authorization + READY, seconds). Parked time waiting "
        "on human consent never counts against it. Falls to the "
        "deployment's setup_timeout; a requested value wins up to the "
        "deployment's slice room (slice_max_seconds minus "
        "per_attempt_timeout).")
    max_turns: int | None = Field(
        default=None, ge=1, le=50,
        description="Orchestrator decision-turn budget.")
    max_total_agents: int | None = Field(
        default=None, ge=1, le=64,
        description="Total spawn ceiling for the run.")
    max_width: int | None = Field(
        default=None, ge=1, le=16,
        description="Max concurrently in-flight agents (e.g. 10 tasks "
        "with max_width=3: only 3 run at a time, the rest queue).")
    max_attempts: int | None = Field(
        default=None, ge=1, le=3,
        description="Per-agent retry cap for this run (fresh-context attempts). "
        "Omitted falls to the deployment default (3). Non-retryable failures "
        "(e.g. a space quota ceiling) stop at the first attempt regardless.")


class SubmitRunDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str = Field(min_length=1, max_length=64)
    goal: str = Field(max_length=8000)
    agents: list[AgentSpecDTO] = Field(min_length=1, max_length=64)
    provider: ProviderKind | None = None
    model: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=190)
    options: RunOptionsDTO | None = None


class SubmitRunResponseDTO(BaseModel):
    run_id: str


class RunSummaryDTO(BaseModel):
    run_id: str
    status: str
    termination_reason: str | None = None
    final_answer: Any = None
    error: str | None = None
    started_at: int | None = None
    ended_at: int | None = None


class RunListDTO(BaseModel):
    items: list[RunSummaryDTO]
    total: int
    page: int
    page_size: int


class AgentResultDetailDTO(BaseModel):
    payload: Any | None = None
    artifact_key: str | None = None
    routed_at: int


class BotAuthorizationDTO(BaseModel):
    """Pending human-authorization gate for one agent's bot (additive).

    Present on an agent exactly while its bot sits at the human gate
    (``state=AWAITING_AUTHORIZATION``): ``iframe_url``/``redirect_url``
    are the tcauthmng consent surfaces, ``deadline`` the window horizon as
    the ISO-8601 spelling the creation-job payload itself carries —
    empty strings when the payload cannot answer a fact.
    ``application_id`` is the passport application's identifier so a
    consumer can address the decision surface from this read alone.
    """
    state: str
    iframe_url: str
    redirect_url: str
    deadline: str
    application_id: str


class AgentAttemptDetailDTO(BaseModel):
    claim_epoch: int
    slice_retry_index: int
    executor_kind: str
    executor_id: str
    executor_name: str
    binding_state: str
    result_status: str | None = None
    result_message: str | None = None
    started_at: int | None = None


class AgentMessageDetailDTO(BaseModel):
    message_id: str
    agent_id: str
    seq: int
    role: str
    content: Any | None = None
    decision: Any | None = None
    token_usage: Any | None = None
    artifact_key: str | None = None
    created_at: int


class AgentCatalogSpecDTO(BaseModel):
    name: str
    role: str | None = None
    kind: str
    model: str | None = None
    engine: str | None = None


class AgentDetailDTO(BaseModel):
    agent_id: str
    name: str
    role: str | None = None
    kind: str
    status: str
    parent_agent_id: str | None = None
    depth: int
    spawn_round: int
    idempotency_key: str
    attempts: int
    bot_id: str | None = None
    bot_state: str | None = None
    error: str | None = None
    output: Any | None = None
    started_at: int | None = None
    ended_at: int | None = None
    result: AgentResultDetailDTO | None = None
    attempt_ledger: list[AgentAttemptDetailDTO] = Field(default_factory=list)
    messages: list[AgentMessageDetailDTO] = Field(default_factory=list)
    authorization: BotAuthorizationDTO | None = None


class RoundDetailDTO(BaseModel):
    round_id: str
    stage_index: int
    orchestrator_steps: int
    slowest_attempt_steps: int
    cohort_agent_ids: list[str]
    started_at: int
    ended_at: int | None = None


class GuardEventDetailDTO(BaseModel):
    event_id: str
    agent_id: str | None = None
    kind: str
    details: Any | None = None
    created_at: int


class RunDetailDTO(RunSummaryDTO):
    tenant: str
    goal: str
    provider: str
    agent_catalog: list[AgentCatalogSpecDTO] = Field(default_factory=list)
    agents: list[AgentDetailDTO] = Field(default_factory=list)
    rounds: list[RoundDetailDTO] = Field(default_factory=list)
    guards: list[GuardEventDetailDTO] = Field(default_factory=list)


class CancelResponseDTO(BaseModel):
    run_id: str
    status: str


class StreamEventDTO(BaseModel):
    event: str
    run_id: str
    data: dict[str, Any] = {}


class PageDTO(BaseModel):
    page: int = 1
    page_size: int = 20
    status: str | None = None