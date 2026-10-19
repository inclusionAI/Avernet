"""Typed HTTP contract for dispatching a Relay successor."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskDispatchRequestDTO(BaseModel):
    """Dispatch a persisted Relay successor from its current origin generation."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    origin_node_id: str = Field(min_length=1)
    relay_token: str = Field(min_length=1)

    @field_validator(
        "task_id", "target_node_id", "origin_node_id", "relay_token", mode="after"
    )
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value


class TaskDispatchResultDTO(BaseModel):
    """Minimal dispatch response; the authoritative ``TaskNode`` stays internal."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    node_id: str = Field(min_length=1)
