"""Retry for outbound HTTP calls that fail at the transport level.

Two things make a naive ``except httpx.TimeoutException`` retry wrong in this
codebase, and both shape this module's design.

**Classification is by symptom, not by exception type.** In production the
``httpx`` send path is wrapped by an out-of-repo tracer (``sofa_tracer``) that
re-raises low-level transport failures as an opaque exception type this repo can
neither import nor subclass-match — its message is only ``"Error in httpx send
hook"``. A wrapped ``ConnectTimeout`` is therefore *not* caught by
``except httpx.TimeoutException``. What survives the wrapping is the shape of
the object: a request that got an HTTP answer carries a ``response``, and a
connection that died before an answer does not. :func:`is_transport_failure`
reads that shape, so it works identically whether or not the wrapper is present.

**Diagnostics must unwrap the cause chain.** The wrapper keeps the original
error on ``__cause__`` / ``__context__``, but formatting only the wrapper yields
a log line that names nothing — which is exactly how the incident that motivated
this module was first reported. :func:`describe_exception` walks one level of
the chain so a failure log says whether it was a read timeout, a connect
timeout, or a reset connection.

Retry is **opt-in per call site**, not built into the transport: only the caller
knows whether repeating its request is safe. Adopt it for idempotent requests
only — never for uploads or lifecycle mutations.

A completed 4xx/5xx response is an answer, not a transport failure: the thunk
returns it normally and this module hands it back untouched. Deciding what to do
with a status belongs to the caller.

One asymmetry is worth stating plainly, because it is a policy rather than the
absence of one: if a caller's thunk *raises* on status — ``raise_for_status()``
inside the thunk, say — then an exception carrying a 4xx is not retried, while
an exception carrying a 5xx **is**. That follows from classifying by "did this
carry a client-error response", and it is the desirable default (a 5xx is often
transient, a 4xx never is). Callers that do not want 5xx retried should keep
status handling outside the thunk, as ``BaasService.get_http_info`` does.
"""
from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

from agentclaw.community.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "DEFAULT_ATTEMPTS",
    "DEFAULT_BACKOFF_SECONDS",
    "client_error_status",
    "describe_exception",
    "is_transport_failure",
    "retry_transport_call",
]

T = TypeVar("T")

#: One original attempt plus one retry. A single blip is what this absorbs;
#: more attempts would multiply the caller's worst-case deadline without
#: evidence that a second retry helps.
DEFAULT_ATTEMPTS = 2

#: Base pause before a retry, in seconds. Jittered — see ``_JITTER_FRACTION``.
DEFAULT_BACKOFF_SECONDS = 0.1

#: Fraction of the base delay added as random jitter. The failures this absorbs
#: arrive in synchronized clusters across unrelated callers, so an un-jittered
#: backoff would re-converge every one of them on the same tick.
_JITTER_FRACTION = 0.5


def client_error_status(exc: BaseException) -> int | None:
    """Return the 4xx status carried on ``exc.response``, else ``None``.

    ``None`` is a meaningful answer, not a missing one: it means "this exception
    carries no client-error response", which is precisely how a connection-level
    failure presents — including one re-typed by the send-hook wrapper.

    A 4xx means the peer answered and rejected the request; repeating it
    verbatim cannot succeed. 5xx and 2xx return ``None`` because this function
    exists to identify *non-retryable* answers, and status-based retry policy
    belongs to the caller.
    """
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if isinstance(status, int) and 400 <= status < 500:
        return status
    return None


def is_transport_failure(exc: BaseException) -> bool:
    """True when ``exc`` shows no sign of having received an HTTP answer."""
    return client_error_status(exc) is None


def _safe_getattr(obj: object, name: str) -> object | None:
    """``getattr`` that survives a property raising something other than AttributeError.

    ``httpx.HTTPError.request`` is a property that raises ``RuntimeError`` when
    no request was ever attached, and plain ``getattr(obj, name, None)`` only
    swallows ``AttributeError``. Since this module's formatter runs *inside*
    ``except`` blocks, an exception escaping it would replace the very failure
    being reported — so every attribute read here is total.
    """
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def describe_exception(exc: BaseException) -> str:
    """Format ``exc`` with its underlying cause and request URL.

    Produces ``"Type: msg"``, extended with ``" | caused by Type: msg"`` when a
    cause is attached and ``" | request=<url>"`` when the exception carries a
    request. Without the cause, a wrapped transport failure logs as the
    wrapper's own opaque message and names nothing useful.

    Never raises. It is called from inside ``except`` blocks, where raising
    would discard the original exception.
    """
    try:
        parts: list[str] = [f"{type(exc).__name__}: {exc}"]
    except Exception:  # pragma: no cover - a __str__ that raises
        return f"{type(exc).__name__}: <unprintable>"

    # ``__suppress_context__`` means the author wrote ``raise X from None`` and
    # deliberately hid the context; naming it anyway would surface something
    # they chose to bury.
    cause = exc.__cause__
    if cause is None and not exc.__suppress_context__:
        cause = exc.__context__
    if cause is not None and cause is not exc:
        parts.append(f"caused by {type(cause).__name__}: {cause}")

    req = _safe_getattr(exc, "request")
    if req is not None:
        url = _safe_getattr(req, "url")
        if url is not None:
            parts.append(f"request={url}")
    return " | ".join(parts)


def _backoff_delay(backoff_seconds: float) -> float:
    """Return ``backoff_seconds`` plus up to ``_JITTER_FRACTION`` of it."""
    return backoff_seconds + random.uniform(0.0, backoff_seconds * _JITTER_FRACTION)


def retry_transport_call(
    call: Callable[[], T],
    *,
    operation: str,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> T:
    """Run ``call``, retrying it when it fails at the transport level.

    Returns ``call()``'s value unchanged. A completed HTTP response — including
    a 4xx or 5xx one — is a *success* here: the thunk returns normally and the
    value comes straight back, never retried.

    Retries only when the raised exception carries no client-error response
    (see :func:`is_transport_failure`). The final failure is re-raised
    unchanged, so ``except`` clauses and cause chains at the call site keep
    working exactly as they did before adoption.

    Args:
        call: The operation to run. Must be safe to repeat — the caller is
            responsible for that judgement.
        operation: Name used in log lines, e.g. ``"BaasService.get_http_info"``.
        attempts: Total attempts including the first. Must be >= 1.
        backoff_seconds: Base pause before a retry; jittered.

    Raises:
        ValueError: If ``attempts`` is less than 1, or ``backoff_seconds`` is
            negative — programming errors rather than runtime states, so they
            fail loudly up front. A negative backoff would otherwise reach
            ``time.sleep`` *inside* the ``except`` block and replace the
            transport failure being reported with a ``ValueError``.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    if backoff_seconds < 0:
        raise ValueError(f"backoff_seconds must be >= 0, got {backoff_seconds}")

    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            return call()
        except Exception as exc:
            elapsed_ms = (time.monotonic() - started) * 1000
            if attempt >= attempts or not is_transport_failure(exc):
                logger.error(
                    "[%s] failed after %d/%d attempts in %.0fms: %s",
                    operation,
                    attempt,
                    attempts,
                    elapsed_ms,
                    describe_exception(exc),
                )
                raise
            delay = _backoff_delay(backoff_seconds)
            logger.warning(
                "[%s] transport failure on attempt %d/%d after %.0fms, "
                "retrying in %.2fs: %s",
                operation,
                attempt,
                attempts,
                elapsed_ms,
                delay,
                describe_exception(exc),
            )
            time.sleep(delay)

    # Unreachable: the loop either returns or raises on its final attempt.
    raise AssertionError(f"retry_transport_call exhausted without raising: {operation}")
