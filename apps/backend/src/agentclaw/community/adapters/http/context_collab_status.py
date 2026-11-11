"""HTTP statuses for the context collab domain errors (BBS spec §6).

Kept beside ``app.py``'s ``_DOMAIN_ERROR_STATUS_MAP`` and merged into it,
because that module is already at the size cap. The public
``/openapi/v1`` routes answer these through ``ENVELOPE_ERRORS`` with the same
statuses (``openapi_v1/responses.py``); this table covers the app-level
backstop for the internal ``/api/v1`` routes, where an unmapped subclass
would otherwise fall through to the 500 catch-all.
"""
from __future__ import annotations

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabParentEventError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.errors import DomainError

CONTEXT_COLLAB_ERROR_STATUS: dict[type[DomainError], int] = {
    ContextCollabNotFoundError: 404,
    ContextCollabVersionConflict: 409,
    ContextCollabValidationError: 422,
    ContextCollabParentEventError: 422,
}

__all__ = ["CONTEXT_COLLAB_ERROR_STATUS"]
