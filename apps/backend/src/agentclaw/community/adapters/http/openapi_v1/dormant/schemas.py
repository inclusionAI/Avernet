"""Response contracts for public dormant lifecycle operations."""

from pydantic import BaseModel, Field


class BotLifecycleResult(BaseModel):
    """Current state after a lifecycle request is accepted or completed."""

    bot_id: str = Field(description="Bot addressed by the request.")
    owner_id: str = Field(description="Owner of the addressed Bot.")
    status: str = Field(description="Current Bot lifecycle status.")
    changed: bool = Field(
        description="Whether this request changed or started changing the Bot state."
    )


class BotActivateResult(BaseModel):
    """Backward-compatible result for personal Bot activation.

    owner_id and changed are always populated by the current server,
    but stay optional in the published schema because this operation predates
    them and making new response properties required would break generated
    clients built from the earlier contract.
    """

    bot_id: str = Field(description="Bot addressed by the request.")
    owner_id: str | None = Field(
        default=None, description="Owner of the addressed Bot."
    )
    status: str = Field(description="Current Bot lifecycle status.")
    changed: bool | None = Field(
        default=None,
        description="Whether this request changed or started changing the Bot state.",
    )

    message: str | None = Field(
        default=None,
        description="Additional reactivation detail, when available.",
    )


class BotRecycleResult(BotLifecycleResult):
    """Engine-selected result: coding admission or default synchronous completion."""

    operation_id: str | None = Field(
        default=None,
        description="Coding recycle handle; absent for synchronous or already-recycled results.",
    )
    phase: str | None = Field(
        default=None,
        description="Current recycle operation phase, not Bot runtime status.",
    )


class BotRecycleOperation(BaseModel):
    """Public projection of a single current/latest recycle; never raw ext."""

    bot_id: str = Field(description="Bot addressed by this operation.")
    owner_id: str = Field(description="Owner of the addressed Bot.")
    operation_id: str = Field(
        description="Current/latest recycle operation identifier."
    )
    bot_status: str = Field(
        description="Current Bot runtime lifecycle status, independent of operation phase."
    )
    phase: str = Field(
        description="QUEUED, BACKING_UP, RELEASING, SUCCEEDED, FAILED or NEEDS_CONFIRMATION."
    )
    error_code: str | None = Field(
        default=None, description="Safe operation error code, if any."
    )
    message: str | None = Field(
        default=None, description="Sanitized operation error detail, if any."
    )
    warning: dict[str, str] | None = Field(
        default=None,
        description="Safe code/message warning for best-effort backup or Passport failure.",
    )
