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

    resource_type: Literal["SKILL", "MCP", "RUNTIME"] = Field(
        description="The affected capability domain."
    )
    code: str = Field(description="Stable, machine-readable Runtime observation code.")
    reason: str = Field(description="Safe human-readable explanation without infrastructure details.")
    status: Literal["PENDING", "DEGRADED"] = Field(
        description="Whether the observation is transient or requires user intervention."
    )
    retryable: bool = Field(description="Whether a later projection attempt may resolve the issue.")
    resource_id: str | None = Field(default=None, description="Stable capability identifier when available.")
    name: str | None = Field(default=None, description="Safe display name of the affected capability.")
    corpus: str | None = Field(default=None, description="Skill corpus such as LOCAL, REPO, or CENTER.")
    requested_action: str | None = Field(default=None, description="Requested Runtime action when the engine reported one.")
    observed_entry_type: str | None = Field(default=None, description="Safe classification of the existing active entry.")
    expected_entry_type: str | None = Field(default=None, description="Expected active entry classification.")
    logical_location: str | None = Field(default=None, description="Logical Runtime location without an absolute filesystem path.")
    suggested_action: str | None = Field(default=None, description="Safe next action suggested to the caller.")


class RuntimeProjectionResult(BaseModel):
    """Observed Runtime outcome; never a replacement for Installation."""

    status: Literal["CONVERGED", "PENDING", "DEGRADED", "SKIPPED"] = Field(
        description="Aggregate Runtime observation for this command."
    )
    components: dict[str, Literal["CONVERGED", "PENDING", "DEGRADED", "SKIPPED"]] = Field(
        default_factory=dict,
        description="Per-domain Runtime outcomes when the engine can report them.",
    )
    pending_count: int = Field(default=0, description="Number of retryable pending observations.")
    degraded_count: int = Field(default=0, description="Number of non-destructive degraded observations.")
    issues: list[RuntimeProjectionIssue] = Field(default_factory=list, description="Per-capability safe diagnostic details.")
    reason: str | None = Field(default=None, description="Reason for a skipped or aggregate Runtime observation.")


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
