"""HTTP schemas for best-effort owned-Bot grant reconciliation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BotGrantReconciliationRequestDTO(BaseModel):
    """Optionally narrow reconciliation to one Bot owned by the caller."""

    model_config = ConfigDict(extra="forbid")

    bot_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional product Bot ID owned by the authenticated Human; omit to "
            "reconcile every live Bot currently owned by that Human."
        ),
    )


class BotGrantReconciliationAcceptedDTO(BaseModel):
    """Acknowledgement that the in-process compensation was scheduled."""

    accepted: bool = Field(description="Whether the background compensation was accepted.")


__all__ = [
    "BotGrantReconciliationAcceptedDTO",
    "BotGrantReconciliationRequestDTO",
]
