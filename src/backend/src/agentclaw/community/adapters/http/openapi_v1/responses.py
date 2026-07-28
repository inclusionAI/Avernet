"""Response construction for the public ``/openapi/v1`` API.

Every public route returns the same :class:`Envelope` shape — on success and on
the known failure cases alike — so external callers parse one structure
everywhere. This module owns:

- the envelope/page builders that stamp the request's trace id into
  ``request_id`` (mirroring the ``X-Trace-ID`` response header), and
- the domain-error → envelope mapping plus the :func:`envelope_errors` decorator
  that turns a raised domain error into an enveloped error response with the
  right HTTP status, leaving unmapped exceptions to the app's 500 handler.

Handlers therefore never build an :class:`Envelope` by hand for errors; they call
a builder on success and let the decorator handle the mapped failures.
"""

from __future__ import annotations

from functools import wraps
from json import JSONDecodeError
from typing import Awaitable, Callable, TypeVar

from fastapi import Request
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_ACCEPTED,
    CODE_CREATED,
    CODE_OK,
    Deleted,
    Envelope,
    Page,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    ClusterMismatchError,
    MissingPrincipalError,
    UnsupportedEngineError,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
    BotLimitExceededError,
    BotNameExistsError,
    BotNameInvalidError,
    BotNotFoundError,
    BotOperationNotAllowedError,
    BotPermissionError,
    BotServiceError,
    DeviceLimitError,
)
from agentclaw.community.core.bot_management.create_flow import (
    AuthStatusUnavailableError,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
)
from agentclaw.community.plugin_api.passport import PassportError

T = TypeVar("T")


def _trace_id(request: Request) -> str:
    """Trace id for ``request_id``; empty when the tracer middleware didn't run."""
    return getattr(request.state, "trace_id", "") or ""


def envelope(
    data: T,
    request: Request,
    *,
    code: int = CODE_OK,
    message: str = "OK",
) -> Envelope[T]:
    """Wrap ``data`` in the standard success envelope."""
    return Envelope(code=code, message=message, data=data, request_id=_trace_id(request))


def page(total: int, items: list[T], request: Request) -> Envelope[Page[T]]:
    """Wrap a page of ``items`` in the standard envelope."""
    return envelope(Page(total=total, items=items), request)


def created(data: T, request: Request) -> Envelope[T]:
    """201 success envelope."""
    return envelope(data, request, code=CODE_CREATED, message="Created")


def accepted(data: T, request: Request) -> Envelope[T]:
    """202 success envelope (e.g. bot creation pending user authorization)."""
    return envelope(data, request, code=CODE_ACCEPTED, message="Accepted")


def deleted(request: Request) -> Envelope[Deleted]:
    """Standard delete-success envelope."""
    return envelope(Deleted(), request)


# Domain error → (HTTP status, fixed public message). Only the specific leaf
# errors are listed; anything unmapped propagates to the app's existing 500
# handler. Messages are fixed (never ``str(exc)``) so that (a) internal
# identifiers and internal-language text never leak to external callers, and
# (b) the two 404-mapped errors are byte-for-byte identical — a caller cannot
# tell "exists but not yours/other tenant" from "does not exist".
ENVELOPE_ERRORS: dict[type[Exception], tuple[int, str]] = {
    MissingPrincipalError: (401, "Unauthorized"),
    BotNotFoundError: (404, "Not found"),
    BotPermissionError: (404, "Not found"),
    BotNameExistsError: (409, "Bot name already exists"),
    BotNameInvalidError: (400, "Invalid bot name"),
    BotLimitExceededError: (409, "Bot creation limit reached"),
    DeviceLimitError: (409, "Device limit reached"),
    BotInvalidLifecycleStateError: (409, "Bot is not in a valid state for this operation"),
    BotOperationNotAllowedError: (409, "Operation not supported for this bot"),
    ClusterMismatchError: (400, "engine and cluster_name do not match"),
    UnsupportedEngineError: (400, "Unsupported engine"),
    PassportError: (502, "Authorization service error"),
    # Engine-config failures. Neither is a BotServiceError, so the base mapping
    # below does not cover them and they would otherwise escape the envelope.
    DeviceNotBoundError: (409, "Bot has no active device"),
    # The passport service answered with nothing at all — upstream problem, not
    # a caller mistake, and not an unhandled crash.
    AuthStatusUnavailableError: (502, "Authorization service error"),
    JSONDecodeError: (500, "Malformed engine configuration"),
    # Base class LAST: every mapping above is a subclass of BotServiceError, and
    # the lookup returns on the first isinstance match in insertion order, so the
    # specific mappings still win. Services raise the bare base for device,
    # persistence, and downstream failures — without this the decorator would
    # re-raise and the app's catch-all would answer with {"detail": ...}, which
    # is not an Envelope and breaks the public contract.
    BotServiceError: (500, "Internal error"),
}


def error_response(http_status: int, message: str, request: Request) -> JSONResponse:
    """Build an enveloped error response (``data`` null, 6-digit code).

    Public so pre-handler failures — which never reach ``@envelope_errors`` —
    can answer in the same shape as everything else on this surface.
    """
    return _error_response(http_status, message, request)


def _error_response(http_status: int, message: str, request: Request) -> JSONResponse:
    body = Envelope(
        code=http_status * 1000,
        message=message,
        data=None,
        request_id=_trace_id(request),
    )
    return JSONResponse(status_code=http_status, content=body.model_dump())


def _find_request(args: tuple, kwargs: dict) -> Request | None:
    candidate = kwargs.get("request")
    if isinstance(candidate, Request):
        return candidate
    for value in args:
        if isinstance(value, Request):
            return value
    return None


def envelope_errors(
    fn: Callable[..., Awaitable[Envelope[T]]],
) -> Callable[..., Awaitable[object]]:
    """Map the domain errors in :data:`ENVELOPE_ERRORS` to enveloped responses.

    The wrapped handler must take a ``request: Request`` parameter (used for the
    error envelope's ``request_id``). Unmapped exceptions are re-raised so the
    app's 500 handler still owns them.
    """

    @wraps(fn)
    async def wrapper(*args: object, **kwargs: object) -> object:
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — re-raised unless mapped
            for error_type, (http_status, message) in ENVELOPE_ERRORS.items():
                if isinstance(exc, error_type):
                    request = _find_request(args, kwargs)
                    if request is None:
                        raise
                    return _error_response(http_status, message, request)
            raise

    return wrapper
