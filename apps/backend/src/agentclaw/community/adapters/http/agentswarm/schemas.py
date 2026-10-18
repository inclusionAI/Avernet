from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentclaw.community.core.agentswarm.domain.models import (
    AgentKind, ProviderKind,
)


class AgentSpecDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=128)
    role: str = Field(max_length=256)
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

    Keys mirror the yaml config keys so operators think in one vocabulary.
    All values are upper-bounded by the deployment config: a caller can
    tighten a budget (shorter timeout, fewer turns) but never loosen it
    past what the deployment allows (the max in `_validated` clamps to
    the configured value)."""

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