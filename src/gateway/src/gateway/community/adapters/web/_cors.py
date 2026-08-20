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

The middleware is installed **outermost** (added last in ``create_app``), so a
preflight is answered here and never reaches the forward route at all. The
edge being the CORS authority also means an upstream's own CORS headers must
not travel back through the gateway — two ``Access-Control-Allow-Origin``
values in one response is an error a browser reports the same way as none.
:data:`CORS_RESPONSE_HEADERS` is what the forwarder strips to keep that true.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.middleware.cors import CORSMiddleware

if TYPE_CHECKING:
    from fastapi import FastAPI

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


def _combined_origin_regex(patterns: list[str]) -> str | None:
    """The configured patterns as one alternation, or ``None`` for an empty list.

    Starlette takes a single regex and matches it with ``fullmatch``; the config
    takes a list, because an environment's origins are several unrelated shapes
    and one line per shape is what a reviewer can read. Each pattern is wrapped
    in a non-capturing group so an alternation *inside* a pattern cannot swallow
    the ones after it, and so ``fullmatch`` still anchors each alternative.
    """
    if not patterns:
        return None
    return "|".join(f"(?:{pattern})" for pattern in patterns)


def install_cors(app: FastAPI, cors: CorsConfig) -> None:
    """Attach the edge CORS middleware, driven by ``user_config.cors``.

    ``allow_credentials=True`` because a browser call through this edge carries
    the session cookie or the ``Authorization`` header the gateway authenticates
    with — and it forbids a ``"*"`` origin, so an origin is admitted only by
    being listed in ``allow_origins`` or matching one of ``allow_origin_regex``.

    Methods and request headers are unrestricted (``"*"``): the gateway forwards
    every method into every domain and does not know which headers an upstream
    operation takes, so narrowing either here would refuse calls the upstream
    accepts. What a caller is *allowed to do* is the authenticator's and the
    upstream's decision, on the real request; CORS decides only which origin's
    JavaScript may read the answer.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors.allow_origins),
        allow_origin_regex=_combined_origin_regex(list(cors.allow_origin_regex)),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=list(EXPOSED_HEADERS),
    )
