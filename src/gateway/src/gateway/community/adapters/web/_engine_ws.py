"""Engine-socket entrypoint — the `/engine` WebSocket relay.

The backend's public connection endpoint publishes a finished socket address
under this prefix; this is what serves it:

1. resolve the upstream from the root-anchored ``/engine`` route (unconfigured
   or off-prefix → refuse the handshake; the gateway relays only into the
   configured engine proxy, never as an open socket proxy);
2. authenticate — the same route-security table the HTTP plane uses, which is
   where this prefix's exemption is *declared* rather than implied by the code
   path serving it;
3. open the upstream socket **before** accepting the client, so a hop we cannot
   reach refuses the handshake instead of leaving a caller holding an accepted
   socket the gateway cannot serve;
4. relay frames both ways, unchanged, until a side closes.

The gateway imposes **no idle deadline** on a relayed socket. The credential in
the query string is checked once, by the hop behind the gateway, at the
handshake; the socket is designed to outlive that credential's expiry, so a read
timeout here would tear down healthy connections. Any L7 hop a deployment puts
in *front* of the gateway has to hold the same two properties — pass the Upgrade
through, and impose no read timeout on this prefix.
"""

from __future__ import annotations

import asyncio
from typing import Any

from starlette.websockets import WebSocket

from gateway.community.logger import get_logger
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import Principal, PrincipalType
from gateway.community.spi.forwarder import strip_hop_by_hop
from gateway.community.spi.principal_signer import PrincipalSigner
from gateway.community.spi.ws_forwarder import (
    WEBSOCKET_HANDSHAKE_HEADERS,
    WebSocketClosedError,
    WebSocketForwardRequest,
    WebSocketUpstream,
)

from ._forward import _INBOUND_STRIP, _PRINCIPAL_HEADER, _bundle

logger = get_logger("engine_ws")

#: Path the WebSocket route is mounted on. The ``{rest}`` wildcard is the target,
#: the engine path and nothing else — the rewrite is anchored at the root of the
#: gateway's host, which is why the backend refuses a gateway base url carrying a
#: path component.
ENGINE_WS_ROUTE = "/engine/{full_path:path}"

#: A WebSocket handshake is an HTTP ``GET``; route security is resolved for it
#: the same way it is for any other request.
_HANDSHAKE_METHOD = "GET"

# Refusals, all of them *before* the client is accepted. A close code is not
# transmitted on a handshake that never completed — a real client sees an HTTP
# 403 for all three — so these distinguish causes in our own logs and tests
# rather than forming a client-facing contract.
_CLOSE_NO_ROUTE = 4404
_CLOSE_UNAUTHENTICATED = 4401
_CLOSE_INTERNAL = 4500
_CLOSE_UPSTREAM_UNAVAILABLE = 4502

#: Codes a peer may report but no endpoint may put in a close frame (RFC 6455
#: §7.4.1), mapped to the nearest code that may be sent.
_UNSENDABLE_CLOSE_CODES = {1005: 1000, 1006: 1011}

#: A close reason is at most 123 bytes on the wire (RFC 6455 §5.5.1: a control
#: frame's payload is 125 bytes, two of which are the code).
_MAX_CLOSE_REASON_BYTES = 123


class _ClientClosedError(Exception):
    """The client closed its side; carries the code and reason to relay on."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(f"client websocket closed: {code} {reason}".rstrip())
        self.code = code
        self.reason = reason


async def forward_websocket(websocket: WebSocket) -> None:
    """Resolve → authenticate → dial upstream → accept → relay both ways."""
    state = websocket.app.state
    route = getattr(state, "engine_route", None)
    if route is None:
        # A deployment that fronts no engine proxy serves no socket. The
        # community build's normal state, and the mirror of the backend's
        # neutral-empty gateway block.
        await _refuse(websocket, _CLOSE_NO_ROUTE, "no engine route configured")
        return

    path = _raw_path(websocket)
    query = websocket.scope.get("query_string", b"").decode("latin-1")
    url = route.upstream_url(path, query)
    if url is None:
        await _refuse(websocket, _CLOSE_NO_ROUTE, "no route for path")
        return

    try:
        identities = await state.authenticator.authenticate(
            _HANDSHAKE_METHOD, path, _bundle(websocket)
        )
    except AuthError as exc:
        await _refuse(websocket, _CLOSE_UNAUTHENTICATED, str(exc))
        return

    try:
        headers = await _upstream_headers(
            websocket,
            identities,
            signer=state.principal_signer,
            audience=route.server.name,
        )
    except Exception:
        logger.exception("principal signing failed")
        await _refuse(websocket, _CLOSE_INTERNAL, "principal signing failed")
        return

    request = WebSocketForwardRequest(
        url=url,
        headers=headers,
        subprotocols=tuple(websocket.scope.get("subprotocols") or ()),
    )
    try:
        cm = state.ws_forwarder.connect(request)
        upstream = await cm.__aenter__()
    except Exception:
        logger.warning("engine upstream unavailable", exc_info=True)
        await _refuse(websocket, _CLOSE_UPSTREAM_UNAVAILABLE, "upstream unavailable")
        return

    try:
        # Echoed, not guessed: the upstream has already negotiated, so the
        # client is told exactly what the socket behind it agreed to.
        await websocket.accept(subprotocol=upstream.subprotocol or None)
        await _relay(websocket, upstream)
    finally:
        await cm.__aexit__(None, None, None)


def _raw_path(websocket: WebSocket) -> str:
    """The request path **as it arrived**, still percent-encoded.

    ``websocket.url.path`` is percent-*decoded*, so relaying it would re-encode
    the routing target (``ARCA_x@0:20003``) and any encoded segment of the engine
    path into something the hop behind the gateway never published. Everything
    past the prefix has to travel verbatim, so the raw path is what is sliced.
    The decoded path is the fallback for a server that sets no ``raw_path``.
    """
    raw = websocket.scope.get("raw_path")
    if isinstance(raw, bytes):
        # Split defensively: some servers include the query string in raw_path.
        return raw.split(b"?", 1)[0].decode("latin-1")
    return str(websocket.scope.get("path", ""))


async def _upstream_headers(
    websocket: WebSocket,
    identities: dict[PrincipalType, Principal],
    *,
    signer: PrincipalSigner,
    audience: str,
) -> dict[str, str]:
    """The client's handshake headers, cleaned and identity-stamped.

    Dropped: hop-by-hop (which covers ``connection`` and ``upgrade``), the
    handshake headers the client library composes for itself, ``host`` (the
    library sets it from the upstream URL) and any caller-supplied
    ``X-Avernet-Principal`` (forgery guard) — the same strip list the HTTP path
    applies, plus the WebSocket-specific ones.

    Identities are signed on exactly as they are for a forwarded request, so a
    deployment that *does* require one on this prefix conveys it. Under the
    shipped exemption the set is empty and no header is added.
    """
    headers = {
        key: value
        for key, value in strip_hop_by_hop(dict(websocket.headers)).items()
        if key.lower() not in _INBOUND_STRIP
        and key.lower() not in WEBSOCKET_HANDSHAKE_HEADERS
    }
    if not identities:
        return headers
    token = await signer.sign(identities, audience=audience)
    return {**headers, _PRINCIPAL_HEADER: token}


async def _refuse(websocket: WebSocket, code: int, reason: str) -> None:
    """Reject the handshake, never having accepted it."""
    logger.info("engine socket refused (%s): %s", code, reason)
    await websocket.close(code=code, reason=_truncate_reason(reason))


async def _relay(websocket: WebSocket, upstream: WebSocketUpstream) -> None:
    """Pump frames both ways until one side closes, then close the other.

    Both directions run concurrently and the first to finish decides the close.
    The survivor is cancelled *and awaited* before returning, so no task outlives
    the upstream context manager that is about to exit.
    """
    tasks = [
        asyncio.create_task(_client_to_upstream(websocket, upstream)),
        asyncio.create_task(_upstream_to_client(websocket, upstream)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        # Awaited, and every result consumed: an unretrieved exception on the
        # loser would be reported later against whatever is running then.
        await asyncio.gather(*tasks, return_exceptions=True)

    # Both directions can finish together (each side closing on the other's
    # close); scanning in order makes which one decides the close deterministic.
    error = next(
        (
            task.exception()
            for task in tasks
            if task.done() and not task.cancelled() and task.exception() is not None
        ),
        None,
    )
    await _close_both(websocket, upstream, error)


async def _client_to_upstream(
    websocket: WebSocket, upstream: WebSocketUpstream
) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise _ClientClosedError(
                int(message.get("code", 1000)), str(message.get("reason") or "")
            )
        text = message.get("text")
        if text is not None:
            await upstream.send(text)
            continue
        data = message.get("bytes")
        if data is not None:
            await upstream.send(data)


async def _upstream_to_client(
    websocket: WebSocket, upstream: WebSocketUpstream
) -> None:
    while True:
        frame = await upstream.receive()
        if isinstance(frame, str):
            await websocket.send_text(frame)
        else:
            await websocket.send_bytes(frame)


async def _close_both(
    websocket: WebSocket, upstream: WebSocketUpstream, error: BaseException | None
) -> None:
    """Close whichever side is still open, carrying the closer's code across."""
    if isinstance(error, _ClientClosedError):
        code, reason = _sendable(error.code, error.reason)
        await _quietly(upstream.close(code, reason))
        return
    if isinstance(error, WebSocketClosedError):
        code, reason = _sendable(error.code, error.reason)
        await _quietly(websocket.close(code=code, reason=reason))
        return
    if error is not None:
        logger.warning("engine socket relay failed", exc_info=error)
    # A relay that ends without either side reporting a close is a gateway-side
    # fault; 1011 says so rather than implying a clean goodbye.
    await _quietly(websocket.close(code=1011, reason="relay ended"))
    await _quietly(upstream.close(1011, "relay ended"))


async def _quietly(coroutine: Any) -> None:
    """Await a close that may find its socket already gone."""
    try:
        await coroutine
    except Exception:
        logger.debug("engine socket close failed", exc_info=True)


def _sendable(code: int, reason: str) -> tuple[int, str]:
    """A close code and reason this endpoint is allowed to put on the wire."""
    return _UNSENDABLE_CLOSE_CODES.get(code, code), _truncate_reason(reason)


def _truncate_reason(reason: str) -> str:
    encoded = reason.encode("utf-8")
    if len(encoded) <= _MAX_CLOSE_REASON_BYTES:
        return reason
    return encoded[:_MAX_CLOSE_REASON_BYTES].decode("utf-8", errors="ignore")
