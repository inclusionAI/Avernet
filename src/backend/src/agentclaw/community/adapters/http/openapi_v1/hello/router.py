"""Hello group — ``GET /openapi/v1/bots/hello``, the surface's smoke test.

One endpoint that takes no input and answers a fixed greeting. It exists so a
caller can prove the whole path works — gateway forwarding, the principal
header, the response envelope — before wiring a call that also depends on a bot,
a device or a downstream service. It reads nothing and touches no service, which
is the point: a failure here is the transport or the caller's credentials, never
the domain.

The path sits under ``/openapi/v1/bots/`` like every other non-bots group, so
the gateway's existing ``bots`` domain forwards it to this service with no
routing or ``route_security`` change. Like the rest of the surface it requires
an authenticated user principal — "no input" is about the request, not about the
credentials.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.responses import envelope

from .schemas import Hello

router = APIRouter(prefix="/openapi/v1/bots/hello", tags=["hello"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]

# Fixed, and exported so a test asserts against the same constant the handler
# returns rather than a second copy of the string.
HELLO_MESSAGE = "Hello, World!"


@router.get("", response_model=Envelope[Hello])
async def hello(principal: PrincipalDep, request: Request) -> Envelope[Hello]:
    """Return a fixed greeting.

    No path, query or body parameters. ``request`` is not an input either — the
    envelope builder reads the trace id off it for ``request_id``.
    """
    # No ``@envelope_errors``: the decorator maps domain errors raised *inside* a
    # handler, and this one raises none. The only failure this route can produce
    # is the 401 from the auth dependency, which the app-level handler already
    # answers in the envelope.
    return envelope(Hello(message=HELLO_MESSAGE), request)
