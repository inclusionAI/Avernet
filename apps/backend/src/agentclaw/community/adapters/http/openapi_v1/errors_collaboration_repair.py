"""HTTP mapping for collaboration-repair failures and its published responses.

Two exception families reach this adapter, and the kernel deliberately shares
one code string between them. The mapping therefore keys on **family plus raise
site**, not on the code alone:

- ``CollaborationRepairFactory.open`` refuses an unavailable deployment with
  ``collaboration_sync_unavailable`` *before* a service exists. That is a
  capability the deployment does not have, so it is ``503``.
- ``service.repair`` raises the same code when its existence probe cannot reach
  the delivery side. The service exists and the request was accepted, so an
  upstream failure is ``502``.

``owner_changed`` is a conflict with live state, ``repair_deadline_exceeded`` is
a timeout, and every facts-read failure other than a missing local Bot is a
conflict with the facts this request was built from. A missing Bot is the same
masked ``404`` the rest of the surface returns, byte for byte.

An unclassified code inside either family, and every exception outside them,
falls through to the application's existing ``500`` path — this module never
turns an unknown failure into a success.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.error_logging import log_public_error
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_ERROR_RESPONSES,
    ErrorEnvelope,
    error_example,
)
from agentclaw.community.adapters.http.openapi_v1.response_envelope import (
    error_response_for,
)
from agentclaw.community.kernel.collaboration_repair import (
    RepairFactsError,
    RepairFactsErrorCode,
    RepairPreconditionError,
    RepairPreconditionErrorCode,
)

#: The shared code's status when the deployment cannot offer repair at all.
_UNAVAILABLE_AT_OPEN = (503, "Collaboration repair is unavailable")

#: The same code after the factory opened: the delivery side did not answer.
_UNAVAILABLE_DURING_REPAIR = (502, "Collaboration sync is unavailable")

_INTERNAL_ERROR = (500, "Internal error")

PRECONDITION_STATUS: dict[str, tuple[int, str]] = {
    RepairPreconditionErrorCode.COLLABORATION_BOT_NOT_FOUND: (404, "Not found"),
    RepairPreconditionErrorCode.OWNER_CHANGED: (
        409,
        "Bot ownership changed; the repair command no longer names a stable target",
    ),
    RepairPreconditionErrorCode.REPAIR_DEADLINE_EXCEEDED: (
        504,
        "Collaboration repair timed out",
    ),
}

FACTS_STATUS: dict[str, tuple[int, str]] = {
    RepairFactsErrorCode.COLLABORATION_BOT_NOT_FOUND: (404, "Not found"),
    RepairFactsErrorCode.COLLABORATION_BOT_IDENTITY_UNRESOLVED: (
        409,
        "Bot collaboration identity is not resolvable",
    ),
    RepairFactsErrorCode.COLLABORATION_AVATAR_UNREADABLE: (
        409,
        "Bot profile facts could not be read",
    ),
    RepairFactsErrorCode.COLLABORATION_FACTS_READ_ERROR: (
        409,
        "Bot collaboration facts could not be read",
    ),
}

#: The extras this operation publishes on top of the user-scoped table.
#:
#: ``200`` carries the report model and says out loud that a success status is
#: not a statement about every item. ``504`` is the deadline family only this
#: operation can produce; the standard 401/403/404/409/502/503 entries come
#: from ``USER_SCOPED_ERROR_RESPONSES``.
COLLABORATION_REPAIR_RESPONSES: dict[int | str, dict[str, object]] = {
    **USER_SCOPED_ERROR_RESPONSES,
    200: {
        "description": (
            "The repair ran. A 200 does not mean every component succeeded: "
            "read data.status and each item's status/error_code."
        ),
    },
    504: {
        "model": ErrorEnvelope,
        "description": "The repair deadline was exhausted before the work finished.",
        **error_example(504, "Collaboration repair timed out"),
    },
}


def precondition_status(
    exc: RepairPreconditionError, *, service_opened: bool
) -> tuple[int, str]:
    """Status for one start precondition, disambiguated by where it was raised."""
    if exc.code == RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE:
        return _UNAVAILABLE_DURING_REPAIR if service_opened else _UNAVAILABLE_AT_OPEN
    return PRECONDITION_STATUS.get(exc.code, _INTERNAL_ERROR)


def facts_status(exc: RepairFactsError) -> tuple[int, str]:
    """Status for one facts-read failure; every non-missing code is a conflict."""
    return FACTS_STATUS.get(exc.code, _INTERNAL_ERROR)


def repair_error_response(
    exc: RepairPreconditionError | RepairFactsError,
    request: Request,
    *,
    service_opened: bool,
) -> JSONResponse:
    """The enveloped response for a mapped repair failure.

    The surface's own error log gets the same redacted record ``@envelope_errors``
    would write: returning a response from the handler bypasses that decorator's
    except branch, and the Core repair service logs no failures by design.
    """
    if isinstance(exc, RepairPreconditionError):
        status, message = precondition_status(exc, service_opened=service_opened)
    else:
        status, message = facts_status(exc)
    log_public_error(request, exc, status=status)
    return error_response_for(status, message, request)


def body_not_accepted(request: Request) -> JSONResponse:
    """The repair takes no body; any payload is refused before work starts."""
    return error_response_for(400, "Request body is not accepted", request)
