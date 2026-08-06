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

import inspect
from functools import wraps
from http import HTTPStatus
from json import JSONDecodeError
from typing import Awaitable, Callable, Mapping, TypeVar

from fastapi import Request
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
    ConnInfoBuildError,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineBotTypeNotSupportedError,
    EngineHistoryDepthExceededError,
    EngineCapabilityUnsupportedError,
    EngineDeviceNotReadyError,
    EngineResourceNotFoundError,
    EngineRuntimeError,
    EngineUpstreamError,
)
from agentclaw.community.core.gateway_principal import PrincipalVerificationError
from agentclaw.community.core.mcp.errors import (
    McpConfigValueError,
    McpHeadersInvalidError,
    McpMarketUnavailableError,
    McpServerNotFoundError,
    McpSyncFailedError,
)
from agentclaw.community.core.resources.service import (
    DuplicateResourceError,
    FileTooLargeError,
    ResourceNotFoundError,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillDuplicateError,
    LocalSkillActiveError,
    LocalSkillEditPausedError,
    LocalSkillInvalidPackageError,
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillOwnerAmbiguousError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
    LocalSkillTooLargeError,
)
from agentclaw.community.core.service_bot.services.publish_exceptions import (
    BotNotFoundError as PublishBotNotFoundError,
    BotPublishServiceError,
    PublishNotDeletableError,
    PublishNotFoundError,
)
from agentclaw.community.core.services.identity import (
    InvalidIdentityEntityTypeError,
    InvalidIdentityFileTypeError,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
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


# Domain error → (HTTP status, fixed public message). Only the specific leaf
# errors are listed; anything unmapped propagates to the app's existing 500
# handler. Messages are fixed (never ``str(exc)``) so that (a) internal
# identifiers and internal-language text never leak to external callers, and
# (b) the two 404-mapped errors are byte-for-byte identical — a caller cannot
# tell "exists but not yours/other tenant" from "does not exist".
ENVELOPE_ERRORS: dict[type[Exception], tuple[int, str]] = {
    MissingPrincipalError: (401, "Unauthorized"),
    # Byte-identical to the line above, deliberately. "You sent no principal" and
    # "your principal did not verify" must be indistinguishable, or the response
    # tells a forger whether their signature was the part that failed. The seam
    # in ``dependencies.py`` already funnels both into MissingPrincipalError; this
    # entry covers a handler that calls ``verify_principal_token`` directly, so
    # the error cannot escape the envelope as a 500.
    PrincipalVerificationError: (401, "Unauthorized"),
    BotNotFoundError: (404, "Not found"),
    BotPermissionError: (404, "Not found"),
    BotNameExistsError: (409, "Bot name already exists"),
    BotNameInvalidError: (400, "Invalid bot name"),
    BotLimitExceededError: (409, "Bot creation limit reached"),
    DeviceLimitError: (409, "Device limit reached"),
    BotInvalidLifecycleStateError: (
        409,
        "Bot is not in a valid state for this operation",
    ),
    BotOperationNotAllowedError: (409, "Operation not supported for this bot"),
    ClusterMismatchError: (400, "engine and cluster_name do not match"),
    UnsupportedEngineError: (400, "Unsupported engine"),
    PassportError: (502, "Authorization service error"),
    # Engine-config failures. None of these is a BotServiceError, so the base
    # mapping below does not cover them and they would otherwise escape the
    # envelope. They are also plain RuntimeError *siblings*, not a hierarchy, so
    # each documented propagation path out of EngineConfigService needs its own
    # entry — mapping one does not cover the others.
    DeviceNotBoundError: (409, "Bot has no active device"),
    # The binding row names a device provider the resolver does not know: bad
    # data on our side, never something the caller can correct.
    UnknownProviderError: (500, "Device binding is misconfigured"),
    # The connection-info build called the underlying device service and it
    # failed — an upstream dependency problem, hence 502 like the other
    # downstream-service mappings.
    ConnInfoBuildError: (502, "Device service error"),
    # The passport service answered with nothing at all — upstream problem, not
    # a caller mistake, and not an unhandled crash.
    AuthStatusUnavailableError: (502, "Authorization service error"),
    JSONDecodeError: (500, "Malformed engine configuration"),
    # Resources domain errors — ValueError subclasses raised by the slim
    # core/resources/service.py. Mapped here so the openapi_v1 resources router
    # lets them propagate to @envelope_errors instead of hand-translating with
    # str(exc), which would leak internal ids/paths to external callers.
    DuplicateResourceError: (409, "Resource already exists"),
    ResourceNotFoundError: (404, "Not found"),
    LocalSkillNotFoundError: (404, "Not found"),
    LocalSkillOwnerAmbiguousError: (409, "Ambiguous Local Skill owner"),
    LocalSkillInvalidPackageError: (400, "Invalid Skill package"),
    LocalSkillNotReadyError: (409, "Bot is not ready"),
    LocalSkillActiveError: (409, "Skill is active"),
    LocalSkillDuplicateError: (409, "Local Skill already exists"),
    LocalSkillTooLargeError: (413, "Skill package is too large"),
    LocalSkillStorageError: (502, "Skill storage operation failed"),
    LocalSkillRuntimeSyncError: (502, "Skill runtime synchronization failed"),
    LocalSkillEditPausedError: (409, "Skill layout is being updated"),
    FileTooLargeError: (413, "File too large for preview"),
    # Identity domain errors — ValueError subclasses raised by IdentityService
    # validate_entity_type / validate_file_type.
    InvalidIdentityEntityTypeError: (400, "Invalid entity type"),
    InvalidIdentityFileTypeError: (400, "Invalid file type"),
    # ── Engine-runtime (Track C) ──────────────────────────────────────────
    # Ordering inside this block is load-bearing: ``EngineRuntimeError`` is the
    # base of the four ``Engine*`` errors below it and is listed AFTER them.
    # Lookup returns on the first isinstance match in insertion order, so a base
    # placed first would swallow every leaf under it — the trap recorded in the
    # Track B gotchas.
    #
    # The three ``DeviceAdapter*`` errors are *siblings*, not a hierarchy
    # (``TimeoutError`` and two independent ``ValueError`` subclasses —
    # ``plugin_api/device_adapter_transport.py``), so each needs its own entry
    # and their relative order does not matter. Do not assume otherwise: a
    # comment here previously claimed EndpointNotFound subclassed HTTPStatus,
    # which is false and would have justified a wrong "fix" to the ordering.
    #
    # The two 501s are distinct answers to distinct questions and must not be
    # merged: one is "your bot's engine does not offer this", answerable from
    # the capabilities endpoint; the other is "this operation is not offered for
    # your bot's type", which capabilities cannot tell you.
    EngineBotTypeNotSupportedError: (
        501,
        "Not supported for this bot type",
    ),
    EngineCapabilityUnsupportedError: (
        501,
        "Not supported by this bot's engine; see the engine capabilities endpoint",
    ),
    # Retryable: cold, dormant or restarting. Distinct from 404 (the bot IS the
    # caller's) and from 500 (nothing is broken).
    EngineDeviceNotReadyError: (409, "Bot device is not ready"),
    # An out-of-range page argument, so it joins the 422 FastAPI already returns
    # for page_size > 100 rather than inventing a status. Needs a mapped entry
    # rather than a bare HTTPException: app-level handlers replace an unmapped
    # message with the bare HTTP reason phrase, and "Unprocessable Entity" would
    # not tell the caller a depth limit is what they hit.
    EngineHistoryDepthExceededError: (
        422,
        "Requested page is deeper than the message history this endpoint serves",
    ),
    # Byte-identical to the other 404s above, so an engine-side missing resource
    # cannot be distinguished from a bot that is not the caller's.
    EngineResourceNotFoundError: (404, "Not found"),
    EngineUpstreamError: (502, "Engine service error"),
    # Base of the four above — LAST of its group.
    EngineRuntimeError: (502, "Engine service error"),
    # Transport errors that reach a handler without the relay translating them
    # (e.g. a future caller using the transport directly). The relay already
    # converts the first two; these are the backstop.
    DeviceAdapterTimeoutError: (504, "Engine request timed out"),
    DeviceAdapterEndpointNotFoundError: (404, "Not found"),
    # A sibling of the entry above, not its base — see the block comment. Order
    # between these two is therefore free.
    DeviceAdapterHTTPStatusError: (502, "Engine service error"),
    # MCP category (Track B). These share no base with BotServiceError, so their
    # order among themselves is free — but they must sit above the BotServiceError
    # fallback like everything else. Fixed public messages: the header-validation
    # and value errors carry internal-language text (the validator answers in
    # Chinese), which is exactly what the fixed-message rule keeps from leaking.
    # 404 message is byte-identical to the bots not-found so existence can't be
    # probed. The two upstream failures are 502 (downstream problem), matching how
    # PassportError / ConnInfoBuildError are mapped above.
    McpServerNotFoundError: (404, "Not found"),
    McpHeadersInvalidError: (400, "Invalid MCP headers"),
    McpConfigValueError: (400, "Invalid MCP configuration"),
    McpSyncFailedError: (502, "Device sync failed"),
    McpMarketUnavailableError: (502, "MCP service error"),
    # Service-bot publish domain, reached when a ``service`` bot is deleted
    # through its publish lifecycle. Its own hierarchy — ``BotPublishServiceError``
    # derives from ``Exception``, NOT from ``BotServiceError`` — so the base
    # fallback below does not cover it, and without these entries every one of
    # them would escape the envelope as a 500.
    #
    # Order inside this block is load-bearing for the same reason as the
    # engine-runtime block: the three leaves subclass ``BotPublishServiceError``,
    # which is therefore listed last of the four.
    #
    # Both 404s are byte-identical to the bots not-found above: "this bot has no
    # publication" and "it is not yours" must not be distinguishable, or the
    # response probes for existence across owners.
    PublishNotFoundError: (404, "Not found"),
    PublishBotNotFoundError: (404, "Not found"),
    # Not 500 and not 404: the publication exists and is the caller's, but it is
    # past the point where deletion is allowed (published, or already released).
    # That is the same "your bot is not in a state for this" answer 409 carries
    # everywhere else on this surface.
    PublishNotDeletableError: (
        409,
        "Bot is not in a valid state for this operation",
    ),
    # Base of the three above — LAST of its group. Raised for missing
    # dependencies and persistence failures, which are ours, not the caller's.
    BotPublishServiceError: (500, "Internal error"),
    # Base class LAST: the bot mappings above subclass BotServiceError (the
    # resources, identity, MCP, and engine-runtime entries are separate
    # hierarchies that never match a bot error), and
    # the lookup returns on the first isinstance match in insertion order, so the
    # specific mappings still win. Services raise the bare base for device,
    # persistence, and downstream failures — without this the decorator would
    # re-raise and the app's catch-all would answer with {"detail": ...}, which
    # is not an Envelope and breaks the public contract.
    BotServiceError: (500, "Internal error"),
}

# Most public categories retain the ordinary ``xxx000`` business code.  A
# small, explicit override table lets a category expose a stable actionable
# subcode without changing any existing public response.
ENVELOPE_ERROR_CODES: dict[type[Exception], int] = {
    LocalSkillOwnerAmbiguousError: 409104,
    LocalSkillInvalidPackageError: 400101,
    LocalSkillNotReadyError: 409101,
    LocalSkillActiveError: 409102,
    LocalSkillDuplicateError: 409103,
    LocalSkillTooLargeError: 413101,
    LocalSkillStorageError: 502101,
    LocalSkillRuntimeSyncError: 502102,
}


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
) -> JSONResponse:
    # ``ErrorEnvelope``, not ``Envelope``: it is the model every route documents
    # for failures (``ERROR_RESPONSES``), and since ``Envelope`` gained the
    # optional ``warning`` field the two shapes are no longer identical. Building
    # the documented model keeps the wire and the published schema in step — an
    # error body has no partial payload to caveat, so ``warning`` has no meaning
    # here.
    body = ErrorEnvelope(
        code=code if code is not None else http_status * 1000,
        message=message,
        data=None,
        request_id=_trace_id(request),
    )
    return JSONResponse(
        status_code=http_status,
        content=body.model_dump(),
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
            log_public_error(
                request, exc, status=response.status_code, params=params
            )
            return response

    return wrapper


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
    for error_type, (http_status, message) in ENVELOPE_ERRORS.items():
        if isinstance(exc, error_type):
            return _error_response(
                http_status,
                message,
                request,
                code=ENVELOPE_ERROR_CODES.get(error_type),
            )
    return None
