"""Shared public DTOs for committed Desired State and Runtime observation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DesiredStateResult(BaseModel):
    """The durable result of one idempotent Bot capability command."""

    changed: bool = Field(description="Whether this command changed Desired State.")
    status: Literal["COMMITTED", "UNCHANGED"] = Field(
        description="COMMITTED after a durable Desired State write; UNCHANGED for an idempotent no-op."
    )


class RuntimeProjectionIssue(BaseModel):
    """One safe, actionable runtime observation without infrastructure paths."""

    resource_type: Literal["SKILL", "MCP", "RUNTIME"]
    code: str
    reason: str
    status: Literal["PENDING", "DEGRADED"]
    retryable: bool
    resource_id: str | None = None
    name: str | None = None
    corpus: str | None = None
    requested_action: str | None = None
    observed_entry_type: str | None = None
    expected_entry_type: str | None = None
    logical_location: str | None = None
    suggested_action: str | None = None


class RuntimeProjectionResult(BaseModel):
    """Observed Runtime outcome; never a replacement for Installation."""

    status: Literal["CONVERGED", "PENDING", "DEGRADED", "SKIPPED"]
    components: dict[str, Literal["CONVERGED", "PENDING", "DEGRADED", "SKIPPED"]] = Field(
        default_factory=dict,
        description="Per-domain Runtime outcomes when the engine can report them.",
    )
    pending_count: int = 0
    degraded_count: int = 0
    issues: list[RuntimeProjectionIssue] = Field(default_factory=list)
    reason: str | None = None


def desired_state_from(result: dict) -> DesiredStateResult:
    changed = bool(result.get("changed"))
    return DesiredStateResult(
        changed=changed,
        status="COMMITTED" if changed else "UNCHANGED",
    )


def runtime_projection_from(result: dict) -> RuntimeProjectionResult:
    raw = result.get("runtime_projection") or {
        "status": "SKIPPED",
        "reason": "RUNTIME_RESULT_NOT_AVAILABLE",
    }
    return RuntimeProjectionResult.model_validate(raw)


__all__ = [
    "DesiredStateResult",
    "RuntimeProjectionIssue",
    "RuntimeProjectionResult",
    "desired_state_from",
    "runtime_projection_from",
]
