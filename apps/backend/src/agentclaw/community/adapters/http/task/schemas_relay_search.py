"""Typed HTTP contract for Relay candidate search."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TaskSearchRequestDTO(BaseModel):
    """Search candidates for the current network-admitted Relay baton."""

    query: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    node_id: str = Field(..., min_length=1)
    relay_token: str = Field(..., min_length=1)


class BotCandidateDTO(BaseModel):
    """HTTP projection of a Relay bot candidate."""

    bot_id: str
    name: str
    description: str = ""
    score: float | None = None


class TaskSearchResultDTO(BaseModel):
    """Search response for the current Relay baton."""

    candidates: list[BotCandidateDTO] = Field(default_factory=list)
