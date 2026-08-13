"""Load-test group — ``/openapi/v1/bots/loadtest`` synthetic endpoints.

Two endpoints that do nothing, on purpose. They exist so a load test can measure
what the *path* costs — the gateway's authentication and forwarding, this
service's middleware stack, and the framework's own request handling — without
that number also containing a database round trip, an engine call, or a bot's
state. A run against these is the baseline every other endpoint's number is read
against; a regression that shows up here is in the shared path, and one that
does not is in the endpoint under test.

- ``GET /openapi/v1/bots/loadtest/hello`` answers the constant ``hello world``
  in the surface's standard envelope.
- ``WEBSOCKET /openapi/v1/bots/loadtest/ws/echo`` sends back every frame it
  receives, byte for byte, until the peer disconnects.

**Authenticated like every other operation here.** Both declare
``require_principal``, so an unverified caller is refused before the handler
runs — 401 on the HTTP endpoint, a refused handshake on the socket. That is not
ceremony: a load test measures the path callers actually take, and on this
surface that path includes verifying the gateway's signed principal. Numbers
gathered without it would describe a route nobody can call.

**Not user-scoped, so no ``user_id``.** Neither endpoint reads or writes
anything belonging to anyone, so there is no scope for the parameter to name
(``test_explicit_user_id.py`` records both alongside the four catalogue reads
that are exempt for the same reason). The caller still has to be authenticated;
it just has no user-shaped answer to receive.

Why the socket lives under its own ``ws`` segment rather than at
``…/loadtest/echo``: the gateway resolves a WebSocket by a domain that claims a
path pattern on the socket plane, and the ``bots`` domain serves HTTP only. A
visible ``ws`` subtree is what such a claim can be pinned to — the same shape
``/openapi/v1/bots/messages/ws/**`` already uses — so an HTTP endpoint added
under ``loadtest`` later stays outside it by construction.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)

from .schemas import HelloWorld

router = APIRouter(prefix="/openapi/v1/bots/loadtest", tags=["loadtest"])

#: The one thing this group says. A constant rather than a literal in two
#: places, so the test asserts the same bytes the handler returns.
HELLO_WORLD = "hello world"

# Authenticated, but not user-scoped. Declared on each route rather than
# inherited from ``build_public_router`` for the reason ``check_bot_name`` does
# the same: the guard is visible where the operation is, and
# ``test_public_routes_require_principal`` walks each route's own dependant,
# which a group-level dependency does not appear in.
_AUTH = [Depends(require_principal)]


@router.get("/hello", response_model=Envelope[HelloWorld], dependencies=_AUTH)
@envelope_errors
async def hello_world(request: Request) -> Envelope[HelloWorld]:
    """Answer the constant "hello world".

    Reads nothing and calls nothing, so the response time is the path's, not the
    handler's.
    """
    return envelope(HelloWorld(message=HELLO_WORLD), request)


@router.websocket("/ws/echo", dependencies=_AUTH)
async def echo(websocket: WebSocket) -> None:
    """Send every received frame straight back, until the peer goes away.

    Frames are relayed through the raw ASGI message rather than
    ``receive_text``/``receive_bytes``, because those two commit the socket to
    one frame type and answer the other with a ``RuntimeError``. A load driver
    chooses its own payload shape, and an echo that refuses half of them would
    make the choice for it.

    The disconnect is handled twice over, and both are reachable: a peer that
    closes cleanly delivers a ``websocket.disconnect`` message, while one whose
    transport drops raises :class:`WebSocketDisconnect` out of the send. Either
    way the coroutine returns instead of propagating — a closed socket is how
    this endpoint ends, not a failure to report.
    """
    await websocket.accept()
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is not None:
                await websocket.send_text(text)
            else:
                await websocket.send_bytes(message["bytes"])
    except WebSocketDisconnect:
        return
