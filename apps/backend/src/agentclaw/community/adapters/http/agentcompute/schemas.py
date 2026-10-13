from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentclaw.community.core.agentcompute.domain.models import AgentKind


class AgentSpecDTO(BaseModel):
    name: str
    role: str
    instructions: str = ""
    kind: AgentKind = AgentKind.LLM
    model: str | None = None
    metadata: dict[str, str] = {}


class SubmitRunDTO(BaseModel):
    goal: str
    agents: list[AgentSpecDTO] = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    idempotency_key: str | None = None


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