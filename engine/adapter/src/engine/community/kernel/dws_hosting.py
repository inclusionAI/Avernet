"""Engine-independent hosting values shared by core and persistence plugins."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=512)]
Timestamp = Annotated[int, Field(ge=0)]


class Value(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class Binding(Value):
    """Trusted identity, established by authorization, never by an incoming event."""

    binding_id: Identifier
    bot_id: Identifier
    owner_id: Identifier
    environment: Identifier
    organization_id: Identifier
    account_id: Identifier
    agent_id: Identifier


class DirectMessage(Value):
    """Normalized incoming text; caller must verify account and direct-chat scope."""

    conversation_id: Identifier
    message_id: Identifier
    sender_id: Identifier
    sender_name: Annotated[str, Field(max_length=200)] = ""
    text: Annotated[str, Field(min_length=1, max_length=16_000)]
    created_at_ms: Timestamp


class BatchPolicy(Value):
    quiet_ms: Annotated[int, Field(gt=0)] = 4_000
    max_wait_ms: Annotated[int, Field(gt=0)] = 15_000
    max_messages: Annotated[int, Field(gt=0, le=100)] = 20
    max_characters: Annotated[int, Field(gt=0, le=100_000)] = 16_000

    @model_validator(mode="after")
    def validate_window(self) -> BatchPolicy:
        if self.quiet_ms > self.max_wait_ms:
            raise ValueError("quiet window exceeds maximum batch window")
        return self

    def deadline(self, first_ms: int, last_ms: int) -> int:
        return min(first_ms + self.max_wait_ms, last_ms + self.quiet_ms)


class Turn(Value):
    """Immutable execution snapshot; rule changes must not rewrite this value."""

    turn_id: Identifier
    binding: Binding
    conversation_id: Identifier
    session_id: Identifier
    binding_epoch: int
    conversation_epoch: int
    rule_version: int
    rule_text: str
    messages: tuple[DirectMessage, ...]


IntakeResult = Literal["queued", "duplicate", "ignored"]
TurnOutcome = Literal["completed", "unknown"]
