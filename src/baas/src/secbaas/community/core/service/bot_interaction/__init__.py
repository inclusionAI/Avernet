"""Bot interaction core service.

The transport-agnostic contract lives in ``community.api.bot_interaction``;
this module provides the concrete ``DefaultBotInteractionService`` that
``core`` wires into the application container.
"""

from secbaas.community.api.bot_interaction import (
    BotInteractionService,
    InteractionBadRequestError,
    InteractionConflictError,
    InteractionDispatch,
    InteractionNotFoundError,
    InteractionResolveResult,
    InteractionServiceError,
)

from ._service import DefaultBotInteractionService

__all__ = [
    "BotInteractionService",
    "DefaultBotInteractionService",
    "InteractionBadRequestError",
    "InteractionConflictError",
    "InteractionDispatch",
    "InteractionNotFoundError",
    "InteractionResolveResult",
    "InteractionServiceError",
]
