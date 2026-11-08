"""Normalized DWS connection values; no CLI or credential payloads cross this port."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from engine.community.kernel.dws_hosting import (
    Binding,
    DirectMessage,
    Identifier,
    Value,
)


class DwsError(RuntimeError):
    """Stable error code only: never propagate CLI stderr or private bodies."""


class AuthorizationStatus(Value):
    authenticated: bool
    organization_id: str = ""
    user_id: str = ""


class AuthorizationChallenge(Value):
    verification_url: str
    user_code: str


class ListenerNotice(Value):
    kind: Literal["ready", "conversation"]
    conversation_id: str = ""
    message_id: str = ""
    created_at_ms: int = 0


class ConnectionHealth(Value):
    connected: bool
    generation: str


class MessageActivity(Value):
    conversation_id: Identifier
    message_id: Identifier
    sender_id: Identifier
    created_at_ms: Annotated[int, Field(ge=0)]
    ai_generated: bool = False


class HistoryResult(Value):
    messages: tuple[DirectMessage, ...]
    observed_ids: tuple[Identifier, ...] = ()
    unsupported_count: Annotated[int, Field(ge=0)] = 0
    activities: tuple[MessageActivity, ...] = ()
    activity_complete: bool = False


class RecoveryWindow(Value):
    binding: Binding
    epoch: int
    start_ms: int
    end_ms: int
    previous_until_ms: int
    target_ms: int
    attempt_id: str
    hint_limit: int


class HintWindow(Value):
    binding: Binding
    epoch: int
    conversation_id: Identifier
    start_ms: int
    end_ms: int
    hint_limit: int


class IntakePolicy(Value):
    overlap_ms: Annotated[int, Field(gt=0)] = 30_000
    window_ms: Annotated[int, Field(ge=60_000)] = 300_000
    poll_seconds: Annotated[float, Field(gt=0)] = 30.0
    ready_timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    retry_seconds: Annotated[float, Field(gt=0)] = 2.0
    max_retry_seconds: Annotated[float, Field(gt=0)] = 60.0

    @model_validator(mode="after")
    def valid_windows(self):
        if (
            self.window_ms <= self.overlap_ms
            or self.max_retry_seconds < self.retry_seconds
        ):
            raise ValueError("invalid intake windows")
        return self


class ConnectionIdentity(Value):
    """Provisioned by trusted owner/organization identity mapping, not event data."""

    organization_id: Identifier
    user_id: Identifier
    self_open_id: Identifier
