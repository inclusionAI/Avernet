"""Published response models for the IAM-token OpenAPI operations."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


class CallerIdentityStage(_DocumentedEnum):
    """Bot runtime stage targeted by Caller identity preparation."""

    DRAFT = "draft"
    VERIFY = "verify"
    ONLINE = "online"

    __descriptions__ = {
        "draft": "The Bot's editable workspace runtime.",
        "verify": "The pre-production runtime of a service Bot.",
        "online": "The live published runtime of a service Bot.",
    }


class IamToken(BaseModel):
    """The current user's opaque IAM token for first-party WebSocket auth."""

    iam_token: str = Field(
        description="Opaque IAM credential for the first-party chat WebSocket."
    )


class CallerIdentityReady(BaseModel):
    """Confirmation that Caller identity preparation completed successfully."""

    bot_id: str = Field(description="Bot whose Caller identity was prepared.")
    stage: CallerIdentityStage = Field(
        description="Bot runtime stage prepared for Caller authentication."
    )
    ready: bool = Field(
        default=True,
        description="Always true when Caller identity preparation succeeded.",
    )


__all__ = ["CallerIdentityReady", "CallerIdentityStage", "IamToken"]
