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
    """Result of recycling an active personal managed-cloud Bot."""
