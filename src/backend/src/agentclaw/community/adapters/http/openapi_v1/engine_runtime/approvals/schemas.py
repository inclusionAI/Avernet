"""Request/response models for the approvals group."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    ApprovalMode,
)


class ApprovalState(BaseModel):
    """The approval mode in force for a session."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_key": "session:2d20edc1:user:165137",
                "mode": "on-miss",
            }
        }
    )

    session_key: str = Field(description="Session the mode applies to.")
    # Typed `str`, not ApprovalMode: the engine's read path has no closed set.
    mode: str = Field(
        description="Current approval mode. A mode you set through this API is "
        "always one of the documented values; a mode set by other means may "
        "report differently."
    )


class ApprovalModeInfo(BaseModel):
    """One selectable approval mode, with its meaning."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "value": "on-miss",
                "description": "Ask only when the bot's policy cannot decide "
                "on its own.",
            }
        }
    )

    value: ApprovalMode = Field(description="The value to send when setting this mode.")
    description: str = Field(description="What this mode does.")


class ApprovalModeSet(BaseModel):
    """Set-the-mode request body."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "session_key": "session:2d20edc1:user:165137",
                "mode": "on-miss",
            }
        },
    )

    session_key: str = Field(description="Session to change the mode for.")
    mode: ApprovalMode = Field(description="The mode to set.")


__all__ = ["ApprovalModeInfo", "ApprovalModeSet", "ApprovalState"]
