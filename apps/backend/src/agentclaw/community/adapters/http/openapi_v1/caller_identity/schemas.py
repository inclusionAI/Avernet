"""Published request and response models for Caller identity configuration."""

from __future__ import annotations

from typing import Literal

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum
from pydantic import BaseModel, ConfigDict, Field


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
        description="Aggregate Bot identity: caller when any active MCP or CLI uses caller."
    )
    mcp_call_types: dict[str, CallerCallType] = Field(
        description="Sparse Caller overrides keyed by MCP server code; missing means owner."
    )
    cli_call_types: dict[str, CallerCallType] = Field(
        description="Sparse Caller overrides keyed by CLI code; missing means owner."
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


class CliCallTypeResult(BaseModel):
    """Applied identity for one AgentPass-authorized CLI."""

    cli_code: str = Field(description="Updated AgentPass CLI code.")
    call_type: CallerCallType = Field(description="Applied CLI execution identity.")
    bot_call_type: CallerCallType = Field(
        description="Aggregate Bot identity after applying the CLI update."
    )


__all__ = [
    "CallerCallType",
    "CallerContext",
    "CliCallTypeResult",
    "McpCallTypeResult",
    "McpCallTypeUpdate",
]


class CallerRefreshTarget(BaseModel):
    """One physical Caller refresh outcome, without credential material."""
    binding_id: int = Field(description="Selected Caller binding identifier.")
    caller_workno: str = Field(description="Caller whose stored authorization was used.")
    status: Literal["succeeded", "skipped", "failed"] = Field(description="Installation outcome.")
    reason: str | None = Field(default=None, description="Safe skip or failure reason.")
    expires_at: str | None = Field(default=None, description="Installed Caller token expiry, or null.")


class CallerRefreshResult(BaseModel):
    """Batch results; partial failures remain explicit and no token is returned."""
    bot_id: str = Field(description="Addressed service Bot.")
    status: Literal["succeeded", "skipped", "partial_failure"] = Field(description="Aggregate refresh outcome.")
    succeeded: int = Field(ge=0, description="Bindings successfully updated.")
    skipped: int = Field(ge=0, description="Bindings skipped by eligibility checks.")
    failed: int = Field(ge=0, description="Bindings whose refresh failed.")
    results: list[CallerRefreshTarget] = Field(description="Per-binding refresh outcomes.")
