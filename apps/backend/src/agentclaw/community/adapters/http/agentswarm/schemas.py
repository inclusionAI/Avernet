from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

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


class SubmitRunDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(max_length=8000)
    agents: list[AgentSpecDTO] = Field(min_length=1, max_length=64)
    provider: ProviderKind | None = None
    model: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=190)


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