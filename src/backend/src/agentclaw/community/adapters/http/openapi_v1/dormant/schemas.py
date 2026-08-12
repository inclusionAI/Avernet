"""Request/response models for the dormant Bot activation group."""
from __future__ import annotations

from pydantic import BaseModel


class BotActivateResult(BaseModel):
    """Result of activating a recycled personal cloud Bot.

    Mirrors the ``{status, message}`` dict ``ActivateBotService.activate``
    returns: a synchronous ``REACTIVATING`` loading state while the Passport
    unfreeze + ``start_bot`` flow runs in the background.
    """

    bot_id: str
    status: str
    message: str | None = None
