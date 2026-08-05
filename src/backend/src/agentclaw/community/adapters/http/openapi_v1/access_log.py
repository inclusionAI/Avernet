"""One log line per ``/openapi/v1`` request — the public API's AOP seam.

Before this existed the public surface was silent on success. Every failure had
a line (``dependencies.py`` logs a rejected principal, ``app.py`` logs every
mapped and unmapped error), but a request that *worked* left no trace at all, so
the first question asked of a live incident — *which tenant, and which caller,
made this call?* — had no answer anywhere in the logs. Uvicorn's access line
carries method, path and status and knows nothing about identity; the tenant is
resolved in middleware and consumed by the ORM guard without ever being written
down.

This middleware is the one place that writes it down. It wraps the whole public
surface rather than each handler, for the reason any cross-cutting concern is a
middleware: a handler that forgets to log is invisible, and there are ~40 public
handlers across nine routers.

What one line carries, and why each field earns its place:

``method`` / ``path`` / ``route``
    ``path`` is the URL as called; ``route`` is the template that matched
    (``/openapi/v1/bots/{bot_id}``), which is what makes lines aggregatable —
    a thousand bot ids collapse to one route. ``route`` is absent on a 404,
    which is itself the signal that nothing matched.
``status`` / ``duration_ms``
    The outcome and its cost. ``status=-`` with an ``error=`` field means the
    request left this layer as an exception; the response was synthesised above
    us by ``ServerErrorMiddleware``'s handler, which logs the traceback.
``tenant`` / ``caller``
    The point of the exercise. ``tenant`` is the data-isolation key the request
    ran under — the same value ``AvernetTenantMiddleware`` bound and every
    service read was confined to — and ``caller`` names each identity in the
    verified set (``user:u-42+app:7``). A request whose principal did not verify
    logs ``tenant=- caller=-``: it is about to be answered ``401``, and *why* it
    failed is already on its own line from the verifier.
``trace_id`` / ``request_id``
    The correlation handles. ``trace_id`` ties this line to the rest of the
    request's logs and to the ``X-Trace-ID`` the client got back; ``request_id``
    is the caller's own ``X-Request-ID``, which is what a client can quote in a
    bug report.
``client`` / ``ua``
    Which peer and which client build. Behind the gateway the peer is the
    gateway, so ``ua`` is usually the more informative of the two.
``query``
    Redacted (see :func:`redact_query`). Query values are the one field here
    that can carry a credential, so no value whose parameter name looks like a
    secret is ever formatted into a line.

Deliberately **not** logged: request and response bodies. They carry bot
configuration, skill payloads and user content, they are unbounded, and nothing
about "which tenant called this" needs them. A body is a debugging tool for one
endpoint at a time, not a standing cost on every request.
"""

from __future__ import annotations

import re
import time

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agentclaw.community.adapters.http.openapi_v1 import PUBLIC_API_PREFIX
from agentclaw.community.adapters.http.openapi_v1.dependencies import resolve_caller
from agentclaw.community.core.gateway_principal import (
    AccessKeyPrincipal,
    AppPrincipal,
    BotPrincipal,
    GatewayPrincipal,
    UserPrincipal,
    VerifiedCaller,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: What an absent value reads as. A fixed placeholder rather than an omitted
#: field so every line has the same shape and a missing value is visible as a
#: missing value, not as a field that happened not to be emitted.
_ABSENT = "-"

#: Substrings that mark a query parameter as carrying a secret, matched against
#: the parameter *name*, case-insensitively. Kept in step with the gateway's own
#: ``_log_redaction`` list; not imported from it, because the gateway is a
#: separate distribution the backend does not depend on.
_CREDENTIAL_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "signature",
    "api_key",
    "apikey",
    "access_key",
)

_REDACTED = "<redacted>"

_CREDENTIAL_PARAM = re.compile(
    r"(^|&)([^=&]*(?:" + "|".join(_CREDENTIAL_HINTS) + r")[^=&]*=)[^&]*",
    re.IGNORECASE,
)

#: Cap on the ``ua`` field. A User-Agent is caller-controlled and unbounded; a
#: line is a log line, not a place to store whatever a client chooses to send.
_MAX_UA = 120

#: Cap on the redacted query. Same reasoning as ``_MAX_UA`` — the redaction
#: makes a query safe to print, not short.
_MAX_QUERY = 512


def redact_query(raw: str) -> str:
    """Replace the value of every credential-looking parameter in *raw*.

    Rewrites the value and keeps the name, so a line still records *which*
    parameters were sent — enough to reproduce a call — without copying a live
    credential into the log.
    """
    return _CREDENTIAL_PARAM.sub(rf"\1\2{_REDACTED}", raw)


def _principal_label(principal: GatewayPrincipal) -> str:
    """``<type>:<id>`` for one identity, using the id that identifies it.

    Each member of the union answers "who" with a different field, and the
    match is exhaustive over the union rather than a ``getattr`` sweep: a new
    principal kind should show up here as a missing branch at review time, not
    as a silent ``unknown`` in production.
    """
    if isinstance(principal, UserPrincipal):
        return f"user:{principal.subject.id}"
    if isinstance(principal, AppPrincipal):
        return f"app:{principal.app.app_id}"
    if isinstance(principal, BotPrincipal):
        return f"bot:{principal.bot.bot_uuid}"
    if isinstance(principal, AccessKeyPrincipal):
        # The access key's stable id, not the token it was presented with — the
        # verifier never projects that credential onto our DTOs at all.
        return f"access_key:{principal.access_key.access_key}"
    return f"{principal.type}:{_ABSENT}"


def caller_label(caller: VerifiedCaller) -> str:
    """Every identity the verified set carries, e.g. ``user:u-42+app:7``.

    The whole set, not just the user: a route that requires both a user and a
    registered app is answering for two identities, and an incident about one of
    them is unresolvable from the other.
    """
    return "+".join(_principal_label(p) for p in caller.principals) or _ABSENT


def _kv(key: str, value: object) -> str:
    """``key=value``, quoted when the value would otherwise break the line."""
    text = str(value)
    if not text:
        return f"{key}={_ABSENT}"
    if any(character in text for character in ' "\n\r\t'):
        escaped = text.replace('"', '\\"').replace("\n", " ").replace("\r", " ")
        return f'{key}="{escaped}"'
    return f"{key}={text}"


def _header(scope: Scope, name: str) -> str:
    """A request header by (lowercase) name, or ``""``."""
    target = name.encode("latin-1")
    for key, value in scope.get("headers", ()):
        if key.lower() == target:
            return value.decode("latin-1", "replace")
    return ""


def _client(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else ""


def _query(scope: Scope) -> str:
    """The request's query string, redacted and capped."""
    raw = scope.get("query_string", b"").decode("latin-1", "replace")
    return redact_query(raw)[:_MAX_QUERY]


def _route(scope: Scope) -> str:
    """The matched route template, or ``""`` when nothing matched.

    ``scope["route"]`` is set by the router while dispatching, so it is only
    readable *after* the downstream app has run — which is why the completion
    line carries it and the start line does not.
    """
    return getattr(scope.get("route"), "path", "")


def _identity_fields(scope: Scope) -> list[str]:
    """``tenant`` and ``caller`` for this request, from the verified principal.

    Reads through :func:`resolve_caller`, which is cached on the request scope:
    ``AvernetTenantMiddleware`` already resolved the caller before the route
    ran, so this is a dictionary lookup and the line cannot disagree with the
    tenant the request actually executed under.
    """
    caller = resolve_caller(Request(scope))
    if caller is None:
        return [_kv("tenant", _ABSENT), _kv("caller", _ABSENT)]
    return [_kv("tenant", caller.tenant), _kv("caller", caller_label(caller))]


class PublicApiAccessLogMiddleware:
    """Emit one INFO line per completed ``/openapi/v1`` request.

    A pure ASGI middleware, not ``BaseHTTPMiddleware``, for the same reason
    ``AvernetTenantMiddleware`` is: no child task, so the timer, the response
    status and the resolved caller are all read in the coroutine that awaited
    the downstream app, and an exception propagates unchanged rather than
    through a task boundary.

    Installed *outside* every middleware whose work it reports on (see
    ``install_middleware``), so the status it records is the one that goes on
    the wire — including a status produced by an exception handler — and the
    trace id and caller those inner layers stashed on the scope are already
    there when it reads them.

    Scoped to the public prefix. The internal ``/api`` surface has its own
    clients, its own logging, and no principal to name; widening this to every
    path would multiply log volume for nothing this line can say.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(
            PUBLIC_API_PREFIX
        ):
            await self.app(scope, receive, send)
            return

        # A request that never finishes never reaches the completion line, so
        # the arrival is recorded too — at DEBUG, because in the ordinary case
        # it is the completion line that carries everything and doubling the
        # volume to say "it started" is not worth an operator's attention.
        logger.debug("openapi_request_start %s", " ".join(self._request_fields(scope)))

        started = time.perf_counter()
        status = 0

        async def _send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:
            # The response is synthesised above us by ServerErrorMiddleware's
            # handler, which logs the traceback; this line exists so the request
            # still appears in the access log, with the identity fields that the
            # traceback does not carry. Re-raised unchanged — deciding what a
            # failed request answers is not this layer's business.
            self._log_completion(
                scope, status=0, started=started, error=type(exc).__name__
            )
            raise
        self._log_completion(scope, status=status, started=started, error="")

    def _request_fields(self, scope: Scope) -> list[str]:
        """The fields known before the downstream app runs."""
        return [
            _kv("method", scope.get("method", "")),
            _kv("path", scope.get("path", "")),
            _kv("query", _query(scope)),
            _kv("client", _client(scope)),
            _kv("ua", _header(scope, "user-agent")[:_MAX_UA]),
            _kv("request_id", _header(scope, "x-request-id")),
        ]

    def _log_completion(
        self, scope: Scope, *, status: int, started: float, error: str
    ) -> None:
        """Write the line — and never be the reason a request fails.

        This runs *after* the response has been sent, so an exception escaping
        here would reach ``ServerErrorMiddleware`` with the response already
        started: the caller would get a truncated body for a request that
        actually succeeded. An access log is worth exactly nothing at that
        price, so a failure to describe a request is swallowed into a line of
        its own rather than allowed to change the request's outcome.
        """
        duration_ms = (time.perf_counter() - started) * 1000
        try:
            state = scope.get("state") or {}
            # Self-sufficient on purpose: the start line above is DEBUG and off
            # in every normal deployment, so this line repeats what it said
            # rather than being readable only next to it.
            fields = [
                _kv("method", scope.get("method", "")),
                _kv("path", scope.get("path", "")),
                _kv("route", _route(scope)),
                _kv("status", status or _ABSENT),
                _kv("duration_ms", f"{duration_ms:.1f}"),
                *_identity_fields(scope),
                _kv("query", _query(scope)),
                _kv("trace_id", state.get("trace_id") or ""),
                _kv("request_id", _header(scope, "x-request-id")),
                _kv("client", _client(scope)),
                _kv("ua", _header(scope, "user-agent")[:_MAX_UA]),
            ]
            if error:
                fields.append(_kv("error", error))
        except Exception:
            # A distinct prefix, not a degraded ``openapi_access`` line: a query
            # counting access lines must not silently count failures as though
            # they described a request.
            logger.exception(
                "openapi_access_failed to describe %s %s",
                scope.get("method", ""),
                scope.get("path", ""),
            )
            return
        logger.info("openapi_access %s", " ".join(fields))
