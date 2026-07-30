"""Request/response models for the sessions group."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    MessageRole,
)


class Message(BaseModel):
    """One message in a session."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message_id": "msg-1",
                "session_id": "session:2d20edc1:user:165137",
                "role": "assistant",
                "content": "Done — the report is in reports/q3.md.",
                "gmt_create": "2026-07-30T09:12:04+00:00",
            }
        }
    )

    message_id: str = Field(description="Identifier of this message.")
    session_id: str = Field(description="Session this message belongs to.")
    role: MessageRole = Field(description="Who or what produced the message.")
    content: str = Field(description="Message body.")
    gmt_create: str = Field(
        description="When the message was created (ISO 8601); empty if the "
        "engine did not report a timestamp."
    )


class Session(BaseModel):
    """A conversation session on the bot's device."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "session:2d20edc1:user:165137",
                "title": "Quarterly report",
                "agent_id": "main",
                "model": "openai/gpt-5.3",
                "permission_mode": "on-miss",
                "cwd": "/workspace",
                "runtime": "",
                "message_count": 12,
                "gmt_create": "2026-07-30T09:00:00+00:00",
                "gmt_modified": "2026-07-30T09:12:04+00:00",
            }
        }
    )

    session_id: str = Field(
        description="Session identifier. Use this value verbatim in the path of "
        "the per-session endpoints — do not re-encode it."
    )
    title: str = Field(description="Human-readable session title.")
    agent_id: str = Field(description="Agent the session belongs to; may be empty.")
    model: str = Field(description="Model the session is using; may be empty.")
    permission_mode: str = Field(
        description="Approval mode in force for this session. A free string, not "
        "the ApprovalMode enum: the engine's read path has no closed set — its "
        "local stub answers 'auto', outside every documented value."
    )
    cwd: str = Field(description="Working directory on the device; may be empty.")
    runtime: str = Field(description="Engine-specific runtime label; may be empty.")
    message_count: int = Field(description="Number of messages in the session.")
    gmt_create: str = Field(description="Creation time (ISO 8601); may be empty.")
    gmt_modified: str = Field(description="Last-modified time (ISO 8601); may be empty.")


class SessionCreate(BaseModel):
    """Create-a-session request body.

    ``user_id`` and ``engine`` are deliberately absent and rejected: the caller
    is the authenticated principal and the engine is the bot's active one.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, description="Optional session title.")
    agent_id: str | None = Field(
        default=None, description="Optional agent to attach the session to."
    )
    model: str | None = Field(
        default=None, description="Optional model for the session."
    )


class SessionUpdate(BaseModel):
    """Partial update. Omitted fields are left unchanged."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, description="New session title.")
    model: str | None = Field(default=None, description="New model.")
    cwd: str | None = Field(default=None, description="New working directory.")


__all__ = ["Message", "Session", "SessionCreate", "SessionUpdate"]
