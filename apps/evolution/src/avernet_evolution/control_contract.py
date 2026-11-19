"""Version 1 control request DTOs shared by ingress and independent service.

These are protocol contracts, not scheduling, login or platform implementations.
Identity fields are forbidden; the system principal and resolved Bot supply them.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BindingsBody(BaseModel):
    """Replace this Bot's complete strategy bindings using an If-Match precondition."""

    model_config = ConfigDict(extra="forbid")
    bindings: list[dict[str, Any]] = Field(
        max_length=100,
        description="Complete binding list; each record is validated by the Evolution binding contract.",
    )


class RunBody(BaseModel):
    """Start a run from an existing binding; the header supplies its idempotency key."""

    model_config = ConfigDict(extra="forbid")
    binding_id: str = Field(min_length=1, max_length=200, description="ID of this Bot's configured binding.")
    params: dict[str, Any] | None = Field(
        default=None,
        description="Optional per-run strategy parameters, validated against the registered schema.",
    )
    budget: dict[str, Any] | None = Field(
        default=None, description="Optional per-run budget override; cannot exceed the binding ceiling."
    )


class CancelBody(BaseModel):
    """Request cancellation without deleting the run or its execution evidence."""

    model_config = ConfigDict(extra="forbid")
    reason: str = Field(
        default="user requested cancellation",
        max_length=500,
        description="Auditable cancellation reason; must not include credentials or private conversation text.",
    )
