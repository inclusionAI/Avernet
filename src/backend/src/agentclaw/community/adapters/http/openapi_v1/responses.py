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

The mapping *tables* themselves live next door in ``envelope_error_table`` —
this module had reached the 1000-line cap, and the error inventory and the
response machinery are two concerns, not one. They are re-exported here, so
every existing importer is unaffected.
"""

from __future__ import annotations

import inspect
from functools import wraps
from http import HTTPStatus
from typing import (
    Awaitable,
    Callable,
    Mapping,
    TypeVar,
)

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.error_logging import (
    capture_call_params,
    log_public_error,
    remember_call_params,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_ACCEPTED,
    CODE_CREATED,
    CODE_OK,
    Deleted,
    Envelope,
    ErrorEnvelope,
    Page,
)
from agentclaw.community.core.skill_center.errors import (
    SkillOfflineBlockedError,
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.api.bot_config_manifest_service import (
    ManifestValidationError,
)
from agentclaw.community.adapters.http.openapi_v1.envelope_error_table import (
    ENVELOPE_ERRORS,
    ENVELOPE_ERROR_CODES,
    SkillCenterMarketplaceUnavailableError,
    _SKILL_SET_CONFLICT_CODES,
)

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
    return Envelope(
        code=code, message=message, data=data, request_id=_trace_id(request)
    )


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


def is_public_api(request: Request) -> bool:
    """True for requests on the public ``/openapi/v1`` surface.

    The app-level error handlers use this to decide which contract a failure
    belongs to: this surface promises the Envelope on every response, while the
    internal ``/api`` routes keep the ``{"detail": ...}`` shape their existing
    clients already parse. The prefix import is function-local to keep this
    module importable from the package's own ``__init__``.
    """
    from agentclaw.community.adapters.http.openapi_v1 import PUBLIC_API_PREFIX

    return request.url.path.startswith(PUBLIC_API_PREFIX)


def unmapped_error_response(
    http_status: int,
    request: Request,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Envelope for a public failure that reached an app-level handler.

    The message is the standard HTTP reason phrase, never the exception's own
    text: anything landing here was *not* mapped by :data:`ENVELOPE_ERRORS`, so
    its message is internal-facing and may carry identifiers or internal-language
    text that must not reach an external caller.

    ``headers`` carries protocol headers the raised exception attached — the
    ``Allow`` list on a 405, a ``WWW-Authenticate`` challenge on a 401. Those are
    part of the answer, not decoration: a 405 without ``Allow`` tells the caller
    they got it wrong but not what would be right.
    """
    try:
        message = HTTPStatus(http_status).phrase
    except ValueError:  # non-standard status — say nothing specific
        message = "Error"
    return _error_response(http_status, message, request, headers=headers)


def error_response(http_status: int, message: str, request: Request) -> JSONResponse:
    """Build an enveloped error response (``data`` null, 6-digit code).

    Public so pre-handler failures — which never reach ``@envelope_errors`` —
    can answer in the same shape as everything else on this surface.
    """
    return _error_response(http_status, message, request)


# Headers that describe *this* response's body. JSONResponse computes them from
# the envelope it is about to serialize, so forwarding an exception's copies
# would describe the body we discarded — a wrong Content-Length is a broken
# response, not a cosmetic issue.
_BODY_HEADERS: frozenset[str] = frozenset(
    {
        "content-length",
        "content-type",
        "transfer-encoding",
    }
)


def _error_headers(request: Request, extra: Mapping[str, str] | None) -> dict[str, str]:
    """Protocol headers to echo, plus the trace id.

    The trace header is set on success by the tracer middleware; it is repeated
    here so an error response carries it regardless of middleware ordering —
    matching ``request_id`` in the body.
    """
    headers = {k: v for k, v in (extra or {}).items() if k.lower() not in _BODY_HEADERS}
    trace_id = _trace_id(request)
    if trace_id:
        headers.setdefault("X-Trace-ID", trace_id)
    return headers


def _error_response(
    http_status: int,
    message: str,
    request: Request,
    *,
    headers: Mapping[str, str] | None = None,
    code: int | None = None,
    data: object | None = None,
) -> JSONResponse:
    # ``ErrorEnvelope``, not ``Envelope``: it is the model every route documents
    resolved_code = code if code is not None else http_status * 1000
    request_id = _trace_id(request)
    if data is None:
        content = ErrorEnvelope(
            code=resolved_code, message=message, data=None, request_id=request_id
        ).model_dump()
    else:
        # P2-OFF-002 documents Envelope[SkillOfflineImpact], not ErrorEnvelope.
        content = dict(
            code=resolved_code, message=message,
            data=jsonable_encoder(data), request_id=request_id,
        )
    return JSONResponse(
        status_code=http_status,
        content=content,
        headers=_error_headers(request, headers),
    )


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

    Every failure is also logged here, with its traceback and the arguments the
    handler was called with. This is the only frame that has both: the public
    response carries a fixed message by design, so without this the sole record
    of a mapped failure was the status code on the access log. Capture is lazy —
    a successful request pays nothing — and the parameters are stashed on the
    request for the unmapped case, where ``app.py`` logs further out.
    """
    # Resolved once, at import: ``fn`` is the undecorated handler, so the bind
    # in the except-branch recovers real parameter names for positional args.
    signature = inspect.signature(fn)

    @wraps(fn)
    async def wrapper(*args: object, **kwargs: object) -> object:
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — re-raised unless mapped
            request = _find_request(args, kwargs)
            if request is None:
                raise
            params = capture_call_params(signature, args, kwargs)
            # Stashed before the mapping decision: an unmapped error is
            # re-raised out of this frame, and the handler that catches it can
            # no longer see the arguments.
            remember_call_params(request, params)
            response = mapped_error_response(exc, request)
            if response is None:
                raise
            log_public_error(request, exc, status=response.status_code, params=params)
            return response

    return wrapper


def _error_data(exc: Exception) -> object | None:
    """The ``data`` block a failure carries, or ``None`` for the usual case.

    Almost every error on this surface answers with a fixed message and a null
    ``data`` — the message is contract, and anything caller- or
    internal-specific stays out of it. Two failures are genuinely different:
    they have a *structured* answer the caller needs in order to act, and it is
    derived entirely from what that caller sent or already knows.

    Named exception types rather than a duck-typed ``payload`` attribute, so
    that admitting a third one is a deliberate line in this function instead of
    something a new exception class can grant itself.
    """
    if isinstance(exc, SkillOfflineBlockedError):
        return exc.impact
    if isinstance(exc, ManifestValidationError):
        # The all-or-nothing refusal. The fixed message says a document was
        # rejected; this says which entries and why, in the caller's own terms.
        return exc.as_payload()
    return None


def mapped_error_response(exc: Exception, request: Request) -> JSONResponse | None:
    """The enveloped response for ``exc``, or ``None`` if it is not mapped.

    Shared by :func:`envelope_errors` and the app-level backstop in ``app.py``,
    so one table decides an error's public status and message no matter *where*
    it was raised. That matters because a handler decorator only sees failures
    inside the handler: a mapped error raised in a **dependency** — the auth seam
    being the one every public route has — is raised before the handler runs and
    would otherwise be answered as a 500.

    Returns on the first ``isinstance`` match in insertion order, so a specific
    leaf listed before its base class still wins.
    """
    if isinstance(exc, SkillSetControlPlaneConflictError):
        code, message = _SKILL_SET_CONFLICT_CODES.get(
            str(exc), (409000, "SkillSet state conflicts with this operation")
        )
        return _error_response(409, message, request, code=code)
    for error_type, (http_status, message) in ENVELOPE_ERRORS.items():
        if isinstance(exc, error_type):
            return _error_response(
                http_status,
                message,
                request,
                code=ENVELOPE_ERROR_CODES.get(error_type),
                data=_error_data(exc),
            )
    return None
