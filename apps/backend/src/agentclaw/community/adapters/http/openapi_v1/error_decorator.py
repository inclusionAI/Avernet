"""Public HTTP error decorator, separated from the shared error mapping table."""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Awaitable, Callable, TypeVar

from fastapi import Request

from agentclaw.community.adapters.http.error_logging import (
    capture_call_params,
    log_public_error,
    remember_call_params,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope

T = TypeVar("T")


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
            from .responses import mapped_error_response

            response = mapped_error_response(exc, request)
            if response is None:
                raise
            log_public_error(request, exc, status=response.status_code, params=params)
            return response

    return wrapper
