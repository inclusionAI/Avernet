"""Browser CORS for the gateway's own edge.

A browser calling ``https://<gateway>/openapi/v1/...`` from a page served by a
different origin sends a **preflight** first: ``OPTIONS``, no cookie, no
``Authorization`` header — the browser strips credentials from that request by
design — and it reads only the ``Access-Control-*`` headers of the answer. If
the answer carries no ``Access-Control-Allow-Origin``, the real request is never
sent and the page sees "blocked by CORS policy" rather than any status the
gateway returned.

Which is why this lives at the gateway rather than in an upstream:

* The preflight cannot authenticate. Everything the catch-all forward does —
  domain resolution, ``Authenticator.authenticate``, signing a principal — is
  work a credential-less request cannot pass on any route whose
  ``route_security`` names a required identity, and a 401 carries no
  ``Access-Control-Allow-Origin``.
* The address a browser is configured with is the *gateway's*. An upstream's
  own allow-list governs callers that reach that component directly; it cannot
  speak for an origin pair it never sees, and it differs per upstream, so which
  endpoints a browser could reach would depend on which component happens to
  serve them.

The middleware is installed **outermost** among the app's own middleware (added
last in ``create_app``), so a preflight is answered here and never reaches the
forward route at all. The edge being the CORS authority also means an upstream's
own CORS headers must not travel back through the gateway — two
``Access-Control-Allow-Origin`` values in one response is an error a browser
reports the same way as none. :data:`CORS_RESPONSE_HEADERS` is what the
forwarder strips to keep that true.

One response is generated *outside* every middleware a FastAPI app installs: the
500 that Starlette's ``ServerErrorMiddleware`` writes when an exception escapes
the stack (``build_middleware_stack`` puts it outermost, above ``add_middleware``
entries). Without a header it cannot get from here, a browser reports that 500 as
a CORS failure and the page never learns the request even reached the gateway —
so ``install_cors`` also registers the handler that answers it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.responses import Response

    from gateway.community.config import CorsConfig

#: Response headers cross-origin JavaScript may read beyond the CORS-safelisted
#: set. The backend stamps ``Deprecation`` / ``Sunset`` on legacy addresses
#: (``adapters/http/openapi_v1/middleware.py``) and exposes them itself; through
#: the gateway that exposure has to be re-declared here, because the edge owns
#: every ``Access-Control-*`` header the browser ends up seeing. Not a
#: deployment choice — the surface either lets a browser SDK learn that its
#: address is retiring, or it does not — so it is not in ``CorsConfig``.
EXPOSED_HEADERS = ("Deprecation", "Sunset")

#: The response headers whose value the edge decides. Stripped from every
#: upstream response by the forwarder, so the middleware's copy is the only one
#: on the wire; a browser rejects a response carrying two of them exactly as it
#: rejects one carrying none.
CORS_RESPONSE_HEADERS = frozenset(
    {
        "access-control-allow-origin",
        "access-control-allow-credentials",
        "access-control-allow-methods",
        "access-control-allow-headers",
        "access-control-expose-headers",
        "access-control-max-age",
        "access-control-allow-private-network",
    }
)


class _OriginAllowList:
    """The configured origins, as one question: may this origin read a response?

    Each regex is compiled and matched **on its own**. Starlette takes a single
    pattern string, and the obvious way to satisfy it — joining the configured
    list into one alternation — silently changes what an operator wrote: a
    pattern that opens with a global inline flag (``(?i)https://...``, legal and
    useful for a host match) is only legal at the very start of an expression,
    so wrapping or concatenating it raises ``re.error: global flags not at the
    start``. That error would surface when Starlette builds the middleware
    stack, i.e. the gateway would refuse to serve at all. Matching each pattern
    separately keeps every entry's semantics its own.

    ``fullmatch`` rather than ``match``, exactly as Starlette does: an origin
    that merely *starts* with an allowed one — ``https://ui.example.com.evil.test``
    against ``https://[a-z]+\\.example\\.com`` — is a different origin.
    """

    def __init__(self, origins: list[str], patterns: list[str]) -> None:
        self._origins = frozenset(origins)
        self._patterns = tuple(re.compile(pattern) for pattern in patterns)

    def allows(self, origin: str) -> bool:
        if origin in self._origins:
            return True
        return any(pattern.fullmatch(origin) for pattern in self._patterns)


class _AllowListCORSMiddleware(CORSMiddleware):
    """Starlette's CORS middleware, asking :class:`_OriginAllowList` instead.

    Only the origin *decision* is replaced; preflight handling, header mirroring
    and the response-header stamping stay Starlette's. ``is_allowed_origin`` is
    the single seam both the preflight path and the simple-response path call,
    so overriding it is enough for the list and the regexes to agree everywhere.
    """

    def __init__(
        self, app: ASGIApp, *, allow_list: _OriginAllowList, **kwargs: Any
    ) -> None:
        self._allow_list = allow_list
        super().__init__(app, **kwargs)

    def is_allowed_origin(self, origin: str) -> bool:
        return self._allow_list.allows(origin)


def _install_server_error_handler(app: FastAPI, allow_list: _OriginAllowList) -> None:
    """Give the outermost 500 the two headers a browser needs to read it.

    ``ServerErrorMiddleware`` sits above every ``add_middleware`` entry, so the
    response it writes for an escaped exception never passes through the CORS
    middleware. The body stays what Starlette would have sent; the headers are
    what turns an opaque "CORS error" in the console into a legible 500.
    """

    async def server_error(request: Request, exc: Exception) -> Response:
        response = PlainTextResponse("Internal Server Error", status_code=500)
        origin = request.headers.get("origin")
        if origin and allow_list.allows(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        return response

    app.add_exception_handler(Exception, server_error)


def install_cors(app: FastAPI, cors: CorsConfig) -> None:
    """Attach the edge CORS middleware, driven by ``user_config.cors``.

    ``allow_credentials=True`` because a browser call through this edge carries
    the session cookie or the ``Authorization`` header the gateway authenticates
    with — and it is why ``CorsConfig`` refuses a ``"*"`` origin: with
    credentials enabled Starlette answers a wildcard by echoing whichever origin
    asked, so ``"*"`` would not fail loudly, it would quietly admit every site
    on the internet to credentialed calls.

    Methods and request headers are unrestricted (``"*"``): the gateway forwards
    every method into every domain and does not know which headers an upstream
    operation takes, so narrowing either here would refuse calls the upstream
    accepts. What a caller is *allowed to do* is the authenticator's and the
    upstream's decision, on the real request; CORS decides only which origin's
    JavaScript may read the answer.
    """
    allow_list = _OriginAllowList(
        list(cors.allow_origins), list(cors.allow_origin_regex)
    )
    app.add_middleware(
        # ``add_middleware``'s factory protocol describes a class whose only
        # extra arguments are the ones it forwards; a subclass taking a keyword
        # of its own does not satisfy it, though it is the same ASGI app at
        # runtime. The alternative — smuggling the allow-list in through a
        # module global — would be worse than the ignore.
        _AllowListCORSMiddleware,  # type: ignore[arg-type]
        allow_list=allow_list,
        allow_origins=list(cors.allow_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=list(EXPOSED_HEADERS),
    )
    _install_server_error_handler(app, allow_list)
