"""Published request and response models for Caller identity configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


class CallerCallType(_DocumentedEnum):
    """Whose identity an MCP uses when it executes."""

    OWNER = "owner"
    CALLER = "caller"

    __descriptions__ = {
        "owner": "Execute with the Bot owner's identity.",
        "caller": "Execute with the identity of the user currently calling the Bot.",
    }


class CallerContext(BaseModel):
    """Caller execution identity configured for one Bot runtime."""

    capability: Literal["caller_identity.v1"] = Field(
        description="Caller identity capability version supported by the Bot."
    )
    stage: RuntimeStage = Field(
        description="Runtime stage whose Caller identity context was requested."
    )
    publish_id: int | None = Field(
        description="Publication addressed by verify/online, or null for draft."
    )
    bot_call_type: CallerCallType = Field(
        description="Aggregate Bot identity: caller when any active MCP uses caller."
    )
    mcp_call_types: dict[str, CallerCallType] = Field(
        description="Explicit Caller identity mode keyed by active MCP server code."
    )
    editable: bool = Field(
        description="Whether the current caller may edit the draft Caller identity."
    )


class McpCallTypeUpdate(BaseModel):
    """Select the execution identity for one active MCP on the draft Bot."""

    model_config = ConfigDict(extra="forbid")

    call_type: CallerCallType = Field(
        description="owner executes as the Bot owner; caller executes as the chat caller."
    )


class McpCallTypeResult(BaseModel):
    """Applied MCP identity and the resulting aggregate Bot identity."""

    server_code: str = Field(description="Updated MCP server code.")
    call_type: CallerCallType = Field(description="Applied execution identity.")
    bot_call_type: CallerCallType = Field(
        description="Aggregate Bot identity after applying the update."
    )


__all__ = [
    "CallerCallType",
    "CallerContext",
    "McpCallTypeResult",
    "McpCallTypeUpdate",
]
