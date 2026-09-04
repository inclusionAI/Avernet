"""Engine management router HTTP schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EngineSwitchRequest(BaseModel):
    engine: str
    force: bool = False


class EngineRestartRequest(BaseModel):
    force: bool = False


class ActiveSessionEntry(BaseModel):
    """One active chat run surfaced by the Active Session query."""

    session_id: str
    run_id: str
    state: Literal["running", "completed", "failed", "aborted"]
    started_at: datetime
    updated_at: datetime
    agent_id: str | None = None


class ActiveSessionQueryResponse(BaseModel):
    """Response for ``GET /api/engine/active-sessions``.

    ``query_status`` describes the query itself; ``verdict`` is the business
    conclusion and is only ``clear``/``active`` when ``query_status=ok``.
    """

    query_status: Literal["ok", "unsupported", "timeout", "error"]
    verdict: Literal["clear", "active", "unknown"]
    engine: str
    checked_at: datetime
    count: int = 0
    sessions: list[ActiveSessionEntry] = Field(default_factory=list)
    reason: str | None = None


__all__ = [
    "EngineSwitchRequest",
    "EngineRestartRequest",
    "ActiveSessionEntry",
    "ActiveSessionQueryResponse",
]
