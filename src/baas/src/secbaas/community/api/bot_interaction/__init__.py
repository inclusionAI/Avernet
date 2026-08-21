"""Bot interaction API contract definitions."""

from __future__ import annotations

from ._exceptions import (
    InteractionBadRequestError,
    InteractionConflictError,
    InteractionNotFoundError,
    InteractionServiceError,
)
from ._models import (
    InteractionDispatch,
    InteractionRequestedResult,
    InteractionResolution,
    InteractionResolvedResult,
    InteractionResolveResult,
)
from ._protocols import BotInteractionService

__all__ = [
    "BotInteractionService",
    "InteractionBadRequestError",
    "InteractionConflictError",
    "InteractionDispatch",
    "InteractionNotFoundError",
    "InteractionRequestedResult",
    "InteractionResolution",
    "InteractionResolveResult",
    "InteractionResolvedResult",
    "InteractionServiceError",
]
