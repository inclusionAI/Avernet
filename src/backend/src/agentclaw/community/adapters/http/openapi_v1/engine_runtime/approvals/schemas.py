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
    mode: str = Field(
        description="Current approval mode. A free string rather than the "
        "ApprovalMode enum, deliberately: the engine's read path has no closed "
        "set — it accepts six spellings while advertising three, does not "
        "canonicalise between them, and its local stub answers 'auto'. "
        "Validating a response against the enum would turn that into a 500. "
        "Values you set through this API are always one of the enum's members."
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

    value: ApprovalMode = Field(description="The value to send when setting a mode.")
    description: str = Field(description="What this mode does.")


class ApprovalModeSet(BaseModel):
    """Set-the-mode request body."""

    model_config = ConfigDict(extra="forbid")

    session_key: str = Field(description="Session to change the mode for.")
    mode: ApprovalMode = Field(
        description="The mode to set. Only the three advertised spellings are "
        "accepted; the engine's undocumented aliases are not published, so one "
        "mode never has two public spellings."
    )


__all__ = ["ApprovalModeInfo", "ApprovalModeSet", "ApprovalState"]
