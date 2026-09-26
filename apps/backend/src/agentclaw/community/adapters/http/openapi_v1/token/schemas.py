"""Published response models for the IAM-token OpenAPI operations."""

from __future__ import annotations

from pydantic import BaseModel, Field

class IamToken(BaseModel):
    """The current user's opaque IAM token for first-party WebSocket auth."""

    iam_token: str = Field(
        description="Opaque IAM credential for the first-party chat WebSocket."
    )
__all__ = ["IamToken"]
