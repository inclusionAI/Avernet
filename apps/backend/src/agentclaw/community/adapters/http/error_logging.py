"""Diagnostics for HTTP errors the adapter layer converts into responses.

Every error the HTTP boundary answers with — a domain error mapped to an
Envelope by ``openapi_v1.responses.envelope_errors``, a ``DomainError`` mapped
by ``app.py``, an exception nobody anticipated — used to leave behind either
nothing at all or a single line naming the exception type. Neither is enough to
debug a production report: the type says *what* failed, not *where* it was
raised or *what the caller asked for*, and the public surface deliberately
returns a fixed message, so the response body carries no diagnosis either.

This module supplies the two missing halves and nothing else:

- **the traceback** — logged with the error, so the raise site is recoverable;
- **the call parameters** — the arguments the handler was actually invoked
  with, captured at the moment the exception unwinds through the decorator that
  converts it.

Capture is lazy: nothing here runs on a successful request. The summarizer is
deliberately conservative — it keeps data-shaped values, redacts anything whose
name looks like a credential, truncates long strings and collections, and
drops injected services rather than trying to render them.

Nothing in here may raise. A logging path that throws turns a mapped 404 into
an unhandled 500, so :func:`log_public_error` and :func:`params_suffix` are
guarded; a failure to describe an error must never replace the error.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Mapping, Sequence, Set
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel
from starlette.background import BackgroundTasks
from starlette.datastructures import Headers, QueryParams, URL, UploadFile
from starlette.requests import HTTPConnection
from starlette.responses import Response

from agentclaw.community.log import get_logger

logger = get_logger()

# What a redacted value renders as. Fixed string, no length hint: the length of
# a secret is itself a fact about the secret.
REDACTED = "***redacted***"

# Substring match against the *parameter or field name*, lowercased. Names are
# matched, never values — a value-based heuristic would both miss credentials
# that do not look like one and redact ordinary ids that happen to.
_SENSITIVE_NAME_PARTS: tuple[str, ...] = (
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "authorization",
    "auth_header",
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
    "private_key",
    "privatekey",
    "signature",
    "cookie",
    "session",
)

# Budgets. A log line is a diagnosis, not a payload dump: a request body with a
# 200KB skill package or a 500-item list must not be reproduced in the log file.
_MAX_STR_CHARS = 200
_MAX_ITEMS = 20
_MAX_MAPPING_KEYS = 30
_MAX_DEPTH = 4
_MAX_RENDERED_CHARS = 2000

# Where a handler's captured parameters wait for an app-level handler. Starlette
# backs ``request.state`` with the ASGI scope dict by reference, so what the
# decorator stores inside the route is what the exception handler reads after
# the exception has unwound past it.
_PARAMS_STATE_ATTR = "avernet_error_call_params"

# Transport objects that must never be walked, even though some of them *are*
# mappings. ``Request`` implements ``Mapping`` over its ASGI scope, so the
# generic mapping branch below would happily render the whole scope — the raw
# header list (``Authorization``, the signed principal token), the app object,
# the DI injector. ``Headers`` has the same problem one level down. None of them
# carries handler input the summarizer does not already get from the named
# parameters, so they are opaque by type rather than filtered by key.
_TRANSPORT_TYPES: tuple[type, ...] = (
    HTTPConnection,  # Request and WebSocket
    Response,
    Headers,
    QueryParams,
    URL,
    BackgroundTasks,
)


class _Opaque:
    """Stand-in for a value the summarizer will not render.

    Injected services, repositories, plugin handles, ``Request`` itself: values
    that carry no request data and whose ``repr`` can be enormous or can touch
    live connections. Nested occurrences render as ``<BotService>``; top-level
    ones are dropped entirely by :func:`capture_call_params`, because a line
    listing five injected dependencies buries the two arguments that matter.
    """

    __slots__ = ("type_name",)

    def __init__(self, value: object) -> None:
        self.type_name = type(value).__name__

    def __repr__(self) -> str:
        return f"<{self.type_name}>"


def _is_sensitive(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _SENSITIVE_NAME_PARTS)


def _truncate(text: str) -> str:
    if len(text) <= _MAX_STR_CHARS:
        return text
    return f"{text[:_MAX_STR_CHARS]}…(+{len(text) - _MAX_STR_CHARS} chars)"


def _summarize(value: Any, depth: int = 0) -> Any:
    """A log-safe, size-bounded projection of ``value``.

    Returns primitives as themselves and everything else as a bounded structure
    of primitives, so the caller can ``repr`` the result without risking a huge
    line or a side effect from someone's custom ``__repr__``.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, _TRANSPORT_TYPES):
        return _Opaque(value)
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Never the content: it is either binary noise or an upload payload.
        return f"<{type(value).__name__} {len(bytes(value))} bytes>"
    if isinstance(value, Enum):
        return _summarize(value.value, depth)
    if isinstance(value, (UUID, Decimal, datetime, date, time)):
        return str(value)

    if depth >= _MAX_DEPTH:
        return _Opaque(value)

    # Pydantic request bodies — the single most useful thing on this line.
    # Matched by type, never by ``hasattr(value, "model_dump")``: duck-typing
    # here means *calling* an attribute off an arbitrary object, and a test
    # double answers every attribute — the duck-typed version called
    # ``model_dump()`` on every injected AsyncMock and left un-awaited
    # coroutines behind it.
    if isinstance(value, BaseModel):
        try:
            dumped = value.model_dump()
        except Exception:  # noqa: BLE001 — a body we cannot dump is not a reason to lose the log
            return _Opaque(value)
        if isinstance(dumped, Mapping):
            return _summarize_mapping(dumped, depth)
        return _summarize(dumped, depth + 1)

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            fields = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
        except Exception:  # noqa: BLE001 — same reasoning as the model_dump guard
            return _Opaque(value)
        return _summarize_mapping(fields, depth)

    if isinstance(value, Mapping):
        return _summarize_mapping(value, depth)

    # Uploads: identity only, never the stream. By type for the same reason as
    # the model branch above — a mock answers ``filename`` too.
    if isinstance(value, UploadFile):
        return f"<upload filename={value.filename!r}>"

    if isinstance(value, (list, tuple, Set)) or (
        isinstance(value, Sequence) and not isinstance(value, str)
    ):
        items = list(value)
        head = [_summarize(item, depth + 1) for item in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            head.append(f"…(+{len(items) - _MAX_ITEMS} more)")
        return head

    return _Opaque(value)


def _summarize_mapping(mapping: Mapping[Any, Any], depth: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for index, (key, item) in enumerate(mapping.items()):
        name = str(key)
        if index >= _MAX_MAPPING_KEYS:
            out["…"] = f"(+{len(mapping) - _MAX_MAPPING_KEYS} more keys)"
            break
        out[name] = REDACTED if _is_sensitive(name) else _summarize(item, depth + 1)
    return out


def capture_call_params(
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    """The handler's arguments, by name, projected into log-safe values.

    ``signature`` is the *undecorated* handler's, so positional arguments get
    their real names. Values that are not request data — the ``Request``, the
    injected services every public handler declares — are dropped rather than
    listed as ``<BotService>``, which would push the arguments that matter off
    the end of the line.
    """
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError:
        # Signature and call disagree (shouldn't happen — FastAPI built the
        # call from this signature). Fall back to whatever was passed by name.
        bound_arguments: Mapping[str, Any] = dict(kwargs)
    else:
        bound_arguments = bound.arguments

    params: dict[str, Any] = {}
    for name, value in bound_arguments.items():
        if _is_sensitive(name):
            params[name] = REDACTED
            continue
        summarized = _summarize(value)
        if isinstance(summarized, _Opaque):
            continue
        params[name] = summarized
    return params


def remember_call_params(request: Request, params: Mapping[str, Any]) -> None:
    """Stash captured parameters for the app-level handler further out.

    An exception the route decorator does not map is re-raised and answered by
    ``app.py``, which is far past the frame that knew the arguments. Storing
    them on the request scope is what lets that handler log the same detail.
    """
    try:
        setattr(request.state, _PARAMS_STATE_ATTR, dict(params))
    except Exception:  # noqa: BLE001 — diagnostics must not break the response
        pass


def recall_call_params(request: Request) -> dict[str, Any]:
    """Parameters stashed by :func:`remember_call_params`, or ``{}``."""
    try:
        stored = getattr(request.state, _PARAMS_STATE_ATTR, None)
    except Exception:  # noqa: BLE001 — see remember_call_params
        return {}
    return dict(stored) if isinstance(stored, Mapping) else {}


def format_call_params(params: Mapping[str, Any]) -> str:
    """Render captured parameters as one bounded ``k=v, k=v`` string."""
    if not params:
        return ""
    rendered = ", ".join(f"{name}={value!r}" for name, value in params.items())
    if len(rendered) > _MAX_RENDERED_CHARS:
        rendered = f"{rendered[:_MAX_RENDERED_CHARS]}…(truncated)"
    return rendered


def params_suffix(request: Request) -> str:
    """`` params={...}`` for the request's stashed parameters, else ``""``.

    Appended to the app-level handlers' existing log lines so their message
    format is unchanged when nothing was captured.
    """
    try:
        rendered = format_call_params(recall_call_params(request))
    except Exception:  # noqa: BLE001 — diagnostics must not break the response
        return ""
    return f" params={{{rendered}}}" if rendered else ""


def _scope_str(request: Request, key: str) -> str:
    try:
        return str(request.scope.get(key, "") or "")
    except Exception:  # noqa: BLE001 — see log_public_error
        return ""


def _route_path(request: Request) -> str:
    """The matched route *template* (``/openapi/v1/bots/{bot_id}``).

    Logged next to the concrete path because it is what groups occurrences of
    the same failure across different ids.
    """
    try:
        return str(getattr(request.scope.get("route"), "path", "") or "")
    except Exception:  # noqa: BLE001 — see log_public_error
        return ""


def log_public_error(
    request: Request,
    exc: BaseException,
    *,
    status: int,
    params: Mapping[str, Any] | None = None,
) -> None:
    """Log an error the public surface converted into an enveloped response.

    Level follows the status: ``5xx`` is our failure (``error``), ``4xx`` is the
    caller's (``warning``). Both carry the traceback. That is deliberate for
    4xx too — the errors reaching this path are raised *inside* a handler by a
    service, so the traceback is a short chain of our own frames pointing at the
    check that rejected the request, and knowing which of a handler's four
    ``NotFound`` raises fired is the whole question when a caller reports a 404
    they did not expect. Routine unauthenticated traffic does not come through
    here: the auth seam raises in a dependency, which ``app.py`` answers and
    logs without a traceback.
    """
    try:
        if params is None:
            params = recall_call_params(request)
        log = logger.error if status >= 500 else logger.warning
        log(
            "[Public %s] %s on %s %s route=%s params={%s}: %s",
            status,
            type(exc).__name__,
            _scope_str(request, "method"),
            _scope_str(request, "path"),
            _route_path(request) or "-",
            format_call_params(params),
            exc,
            exc_info=exc,
        )
    except Exception:  # noqa: BLE001
        # A broken log line must never turn a mapped 404 into an unhandled 500.
        # Nothing to report it *to* — the logger is the thing that just failed.
        pass
