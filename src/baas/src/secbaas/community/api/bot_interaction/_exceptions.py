"""Interaction service error hierarchy (transport-agnostic contract).

Defined in the ``api`` layer so adapters can raise/catch them without
importing the concrete ``core.service`` implementation.
"""

from __future__ import annotations


class InteractionServiceError(Exception):
    """Base interaction service error."""

    code = "INTERACTION_ERROR"


class InteractionBadRequestError(InteractionServiceError):
    """Caller supplied an invalid/unsupported resolution."""

    code = "BAD_REQUEST"


class InteractionNotFoundError(InteractionServiceError):
    """No interaction row matched the supplied identity."""

    code = "NOT_FOUND"


class InteractionConflictError(InteractionServiceError):
    """Interaction is in a state that rejects the requested transition."""

    code = "CONFLICT"
