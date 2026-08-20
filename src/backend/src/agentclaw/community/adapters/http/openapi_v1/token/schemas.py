"""Published response models for the IAM-token OpenAPI operations."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage


class IamToken(BaseModel):
    """The current user's opaque IAM token for first-party WebSocket auth."""

    iam_token: str = Field(
        description="Opaque IAM credential for the first-party chat WebSocket."
    )


class CallerIdentityReady(BaseModel):
    """Confirmation that Caller identity preparation completed successfully."""

    bot_id: str = Field(description="Bot whose Caller identity was prepared.")
    stage: RuntimeStage = Field(
        description="Bot runtime stage prepared for Caller authentication."
    )
    ready: bool = Field(
        default=True,
        description="Always true when Caller identity preparation succeeded.",
    )


__all__ = ["CallerIdentityReady", "IamToken"]
