"""Published response models for the IAM-token OpenAPI operations."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentclaw.community.core.caller_identity.contracts import CallerIdentityStage


class IamToken(BaseModel):
    """The current user's opaque IAM token for first-party WebSocket auth."""

    iam_token: str = Field(
        description="Opaque IAM credential for the first-party chat WebSocket."
    )


class CallerIdentityReady(BaseModel):
    """Confirmation that Caller identity preparation completed successfully."""

    bot_id: str
    stage: CallerIdentityStage
    ready: bool = True


__all__ = ["CallerIdentityReady", "IamToken"]
