"""Tell a caller on the wire that the address they used is going away.

The published document already says it — every retiring operation carries
``deprecated: true`` — but a document is something an integrator reads once, at
the start, and then generates a client from. These headers reach the client
that is running now, which is the one that has to change.

RFC 9745 for ``Deprecation`` and RFC 8594 for ``Sunset``, both as HTTP dates.

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

#: RFC 8594 wants an HTTP date, and RFC 9745 wants ``Deprecation`` to carry one
#: too — the moment the deprecation took effect, which is when this shipped.
DEPRECATION = datetime(2026, 8, 15, tzinfo=timezone.utc)

_DEPRECATION_HEADER = b"deprecation"
_SUNSET_HEADER = b"sunset"


class DeprecationHeaderMiddleware:
    """Stamp ``Deprecation`` and ``Sunset`` on responses from legacy addresses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._deprecation = format_datetime(DEPRECATION, usegmt=True).encode()
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


def _is_legacy(scope: Scope) -> bool:
    """Whether the matched route is one of the retiring addresses.

    Read off the *matched route*, not the raw path, so a bot whose id happens to
    spell a legacy segment cannot make its own request look deprecated. The
    router sets this while dispatching, so it is absent on a 404 — which is
    correct: an address that matched nothing is not an address we are retiring.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return False
    return (scope.get("method", ""), path) in LEGACY_ROUTES
