"""Tell a caller on the wire that the address they used is going away.

The published document already says it — every retiring operation carries
``deprecated: true`` — but a document is something an integrator reads once, at
the start, and then generates a client from. These headers reach the client
that is running now, which is the one that has to change.

RFC 9745 for ``Deprecation`` and RFC 8594 for ``Sunset``. The two are **not**
spelled the same way, which is the easy thing to get wrong here:

- ``Deprecation`` is a Structured Fields ``sf-date`` — an ``@`` followed by
  seconds since the Unix epoch (``Deprecation: @1786838400``). The superseded
  ``draft-dalal-deprecation-header`` used an HTTP-date, so an implementation
  written from memory of the draft emits something RFC 9745 parsers reject.
- ``Sunset`` predates that and stays an IMF-fixdate HTTP-date
  (``Sunset: Sun, 15 Aug 2027 00:00:00 GMT``).

Middleware rather than a per-route dependency for one reason: a dependency is
something a new legacy route can be added without. The set is built from the
registrations themselves, so a route that exists is a route that is stamped.

Named ``middleware.py`` to match ``adapters/http/middleware.py``: this is where
anything that wraps the whole public surface belongs, and calling it what it is
keeps it out of the architecture test's reading of "a file under
``adapters/http/`` that drives endpoints".
"""

from __future__ import annotations

from email.utils import format_datetime
from datetime import datetime, timezone

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agentclaw.community.adapters.http.openapi_v1.deprecated import LEGACY_ROUTES

#: When these addresses stop answering. A published promise, so it is a decision
#: rather than a default — twelve months from the specification's approval,
#: agreed at the review gate for
#: ``specs/2026-08-15-openapi-v1-bot-first-addressing``.
#:
#: Removal is still driven by traffic, not by this date: the access log says
#: when an address has no callers left, and that is when it goes. The date is
#: the outer bound a client can plan against, not a countdown.
SUNSET = datetime(2027, 8, 15, tzinfo=timezone.utc)

#: The moment the deprecation took effect, which is when this shipped. Carried
#: as an ``sf-date`` — see the note above on why it is not an HTTP-date.
DEPRECATION = datetime(2026, 8, 15, tzinfo=timezone.utc)

#: The two headers this middleware stamps, in the spelling a browser needs.
#:
#: Exported because CORS has to name them too: neither is a safelisted response
#: header, so cross-origin JavaScript cannot read a header the server sends
#: unless it is also in ``Access-Control-Expose-Headers``. A browser SDK would
#: otherwise see nothing at all — the migration signal reaches every client
#: except the ones most likely to be regenerated from the document. One tuple
#: rather than two lists, so the CORS configuration cannot drift from what is
#: actually sent.
EXPOSED_HEADERS = ("Deprecation", "Sunset")

_DEPRECATION_HEADER = b"deprecation"
_SUNSET_HEADER = b"sunset"

_SENSITIVE_ROUTES = {
    ("POST", "/openapi/v1/bots/{bot_id}/iam-token"),
}
_CACHE_CONTROL_HEADER = b"cache-control"
_CACHE_CONTROL_VALUE = b"no-store, no-cache, must-revalidate"
_PRAGMA_HEADER = b"pragma"
_PRAGMA_VALUE = b"no-cache"


def sf_date(moment: datetime) -> str:
    """*moment* as an RFC 9651 ``sf-date``: ``@`` then whole seconds since the epoch.

    Exported so a test can assert the serialization against the same function
    the middleware uses without re-deriving the format, and so a second caller
    that needs one does not invent a different spelling.
    """
    return f"@{int(moment.timestamp())}"


class DeprecationHeaderMiddleware:
    """Stamp ``Deprecation`` and ``Sunset`` on responses from legacy addresses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._deprecation = sf_date(DEPRECATION).encode()
        self._sunset = format_datetime(SUNSET, usegmt=True).encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def stamped(message: Message) -> None:
            if message["type"] == "http.response.start" and _is_legacy(scope):
                # Appended rather than assigned: the response's own headers are
                # whatever the handler set, and a legacy address answers exactly
                # as it always did apart from these two.
                message.setdefault("headers", [])
                message["headers"].append((_DEPRECATION_HEADER, self._deprecation))
                message["headers"].append((_SUNSET_HEADER, self._sunset))
            await send(message)

        await self.app(scope, receive, stamped)


class SensitiveResponseHeaderMiddleware:
    """Prevent clients and intermediaries from caching credential responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def stamped(message: Message) -> None:
            if message["type"] == "http.response.start" and _matches_route(
                scope, _SENSITIVE_ROUTES
            ):
                headers = message.setdefault("headers", [])
                headers[:] = [
                    (name, value)
                    for name, value in headers
                    if name.lower() not in {_CACHE_CONTROL_HEADER, _PRAGMA_HEADER}
                ]
                headers.append((_CACHE_CONTROL_HEADER, _CACHE_CONTROL_VALUE))
                headers.append((_PRAGMA_HEADER, _PRAGMA_VALUE))
            await send(message)

        await self.app(scope, receive, stamped)


def _is_legacy(scope: Scope) -> bool:
    """Whether the matched route is one of the retiring addresses.

    Read off the *matched route*, not the raw path, so a bot whose id happens to
    spell a legacy segment cannot make its own request look deprecated. The
    router sets this while dispatching, so it is absent on a 404 — which is
    correct: an address that matched nothing is not an address we are retiring.
    """
    return _matches_route(scope, LEGACY_ROUTES)


def _matches_route(scope: Scope, routes: set[tuple[str, str]]) -> bool:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return False
    return (scope.get("method", ""), path) in routes
