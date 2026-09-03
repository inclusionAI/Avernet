"""HTTP contracts for the human-chat OpenAPI group."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HumanChatConnection(BaseModel):
    """Connection material for one caller-owned human-chat session."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(
        description="The caller-owned session this connection resumes."
    )
    need_poll: bool = Field(
        description="True when the caller-specific runtime is still starting."
    )
    connection: dict[str, Any] | None = Field(
        description="Opaque connection material for the chat client; null while starting."
    )


__all__ = ["HumanChatConnection"]
