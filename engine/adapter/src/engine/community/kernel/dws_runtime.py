"""Hosting runtime limits and safe operational projections (additive v1)."""

from typing import Annotated, Literal

from pydantic import Field

from engine.community.kernel.dws_hosting import Identifier, Value


class RuntimePolicy(Value):
    tick_seconds: Annotated[float, Field(gt=0, le=60)] = 1.0
    work_limit: Annotated[int, Field(gt=0, le=100)] = 20
    retention_ms: Annotated[int, Field(ge=86_400_000)] = 30 * 86_400_000
    max_inbox: Annotated[int, Field(gt=0)] = 100_000
    max_pending: Annotated[int, Field(gt=0)] = 1000
    max_hints: Annotated[int, Field(gt=0)] = 10_000
    max_database_bytes: Annotated[int, Field(gt=0)] = 256 * 1024 * 1024
    min_free_bytes: Annotated[int, Field(ge=0)] = 64 * 1024 * 1024


class RuntimeStatus(Value):
    enabled: bool
    intake_ready: bool
    pending_batches: int
    unknown_executions: int
    unknown_deliveries: int


class PendingTurn(Value):
    turn_id: Identifier
    has_execution: bool


class Resolution(Value):
    kind: Literal["execution", "delivery"]
    target_id: Identifier
    evidence: Annotated[str, Field(min_length=1, max_length=1000)]


class HostingIssue(Value):
    kind: Literal["execution", "delivery"]
    target_id: Identifier
    turn_id: Identifier
    conversation_id: Identifier
    state: Literal["unknown", "manual"]


class HostingCapacityError(RuntimeError):
    """Stable code only; stop workers without dropping queued messages."""
