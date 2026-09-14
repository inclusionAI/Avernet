"""Engine management router HTTP schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class EngineSwitchRequest(BaseModel):
    engine: str
    force: bool = False


class EngineRestartRequest(BaseModel):
    force: bool = False


class ActiveSessionEntry(BaseModel):
    """A single active session/run entry surfaced by GET
    /api/engine/active-sessions."""

    session_id: str
    run_id: str | None = None
    agent_id: str | None = None
    started_at: str
    updated_at: str
    last_state: str = "running"
    last_event_at: str | None = None


class ActiveSessionsResponseData(BaseModel):
    """The `data` payload of the active-sessions response.

    `query_status` reports the query itself (`ok` / `unsupported` /
    `timeout` / `error`). `verdict` is the business conclusion and is only
    `clear` or `active` when `query_status = ok`; in every other case it must
    be `unknown` (timeout, error, unsupported, or incomplete data).
    """

    query_status: Literal["ok", "unsupported", "timeout", "error"]
    verdict: Literal["clear", "active", "unknown"]
    engine: str
    checked_at: str
    active_session_count: int
    sessions: list[ActiveSessionEntry] = []
    incomplete: bool | None = None
    error_message: str | None = None


class ActiveSessionsResponse(BaseModel):
    success: bool = True
    data: ActiveSessionsResponseData


__all__ = [
    "ActiveSessionEntry",
    "ActiveSessionsResponseData",
    "ActiveSessionsResponse",
    "EngineRestartRequest",
    "EngineSwitchRequest",
]
