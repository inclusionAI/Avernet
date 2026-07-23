"""Strict request and response schemas for Caller identity APIs."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentclaw.community.api.caller_identity_service import (
    CallerIdentityStage,
    McpCallType,
)


class CallerContextQuery(BaseModel):
    """Exact stage selector for one authenticated Caller context read."""

    model_config = ConfigDict(extra="forbid")

    stage: CallerIdentityStage
    publish_id: Annotated[int | None, Field(gt=0)] = None
    entity_id: str | None = None

    @model_validator(mode="after")
    def validate_stage_scope(self) -> Self:
        """Reject ambiguous draft and published-stage requests."""
        if self.stage is CallerIdentityStage.DRAFT:
            if self.publish_id is not None:
                raise ValueError("draft stage does not accept publish_id")
            return self
        if self.publish_id is None:
            raise ValueError("verify and online stages require publish_id")
        return self


class UpdateMcpCallTypeRequest(BaseModel):
    """Owner-controlled draft update with an optional collaboration lock epoch."""

    model_config = ConfigDict(extra="forbid")

    call_type: McpCallType
    lock_epoch: Annotated[int | None, Field(gt=0, strict=True)] = None


class UpdateMcpCallTypeQuery(BaseModel):
    """Compatibility query fields for the authenticated draft update."""

    model_config = ConfigDict(extra="forbid")

    # Accepted only because gateway callers already append this opaque value.
    # Authentication remains exclusively bound to ``get_current_user``.
    ctoken: str | None = None
    entity_id: str | None = None


class CallerContextResponse(BaseModel):
    """Low-sensitivity opt-in Caller identity context."""

    model_config = ConfigDict(extra="forbid")

    capability: Literal["caller_identity.v1"]
    stage: CallerIdentityStage
    publish_id: int | None
    bot_call_type: McpCallType
    mcp_call_types: dict[str, McpCallType]
    editable: bool


class UpdateMcpCallTypeResponse(BaseModel):
    """Low-sensitivity result of one draft MCP identity update."""

    model_config = ConfigDict(extra="forbid")

    server_code: str
    call_type: McpCallType
    bot_call_type: McpCallType


__all__ = [
    "CallerContextQuery",
    "CallerContextResponse",
    "UpdateMcpCallTypeQuery",
    "UpdateMcpCallTypeRequest",
    "UpdateMcpCallTypeResponse",
]
