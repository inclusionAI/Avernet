"""Outbound WebSocket transport — the ``websockets``-backed duplex relay.

The other half of :mod:`._engine_ws`: that module terminates the client's
socket, this one opens the upstream's. Both are transport, so both live in the
web adapter — a socket library belongs in the layer whose job is speaking
protocols, and Rule 7 exists to keep exactly this out of core.

Not a plugin. ``plugins/`` means an edition-swappable implementation of a plugin
contract, and this has one implementation: there is no corp variant of "dial a
WebSocket". The :class:`~gateway.community.spi.ws_forwarder.WebSocketForwarder`
Protocol is kept regardless — it is what lets the composition root hand the web
adapter a typed collaborator and lets tests relay against a stub instead of a
live socket.

Relays frames unchanged in both directions so the gateway is transparent: a
text frame arrives as text, a binary frame as binary, and neither is inspected,
re-framed, or size-capped.

``websockets`` is also what makes the gateway's *own* server accept an Upgrade —
uvicorn refuses the handshake outright unless a WebSocket implementation is
installed — so the same dependency serves both ends of the relay.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import cast

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Subprotocol

from gateway.community.spi.ws_forwarder import (
    WebSocketClosedError,
    WebSocketForwarder,
    WebSocketForwardRequest,
    WebSocketUpstream,
)

#: Bounds the **handshake** only. There is deliberately no receive deadline
#: anywhere below: the credential on this socket is checked once, at the
#: handshake, and the socket is designed to outlive its expiry, so an idle
#: deadline would tear down healthy connections on a timer.
_HANDSHAKE_TIMEOUT_SECONDS = 10.0


def _closed(exc: ConnectionClosed) -> WebSocketClosedError:
    """The library's close, in the SPI's terms.

    Translated at this seam so the caller relays a close code and a reason
    without importing this library's exception hierarchy. The peer's own close
    frame wins over ours; neither having arrived is 1006, which the caller
    translates before putting it on the wire.
    """
    close = exc.rcvd or exc.sent
    code = close.code if close is not None else 1006
    reason = close.reason if close is not None else ""
    return WebSocketClosedError(code, reason)


class _WebsocketsUpstream(WebSocketUpstream):
    """One open ``websockets`` connection, behind the SPI's duplex surface."""

    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection

    @property
    def subprotocol(self) -> str:
        return self._connection.subprotocol or ""

    async def send(self, message: str | bytes) -> None:
        # Both directions translate, because either can be the one that notices.
        # A client sending while the upstream is closing races the receive pump,
        # and if the send wins, an untranslated exception reaches a caller that
        # cannot recognise it — the peer's real close code is then lost and the
        # relay reports a gateway fault instead.
        try:
            await self._connection.send(message)
        except ConnectionClosed as exc:
            raise _closed(exc) from exc

    async def receive(self) -> str | bytes:
        try:
            return await self._connection.recv()
        except ConnectionClosed as exc:
            raise _closed(exc) from exc

    async def close(self, code: int, reason: str) -> None:
        await self._connection.close(code, reason)


class WebsocketsForwarder(WebSocketForwarder):
    """A ``websockets``-backed :class:`WebSocketForwarder`.

    Stateless: each handshake opens its own connection, since a socket is held
    for the life of one client rather than pooled across callers.
    """

    @asynccontextmanager
    async def connect(
        self, request: WebSocketForwardRequest
    ) -> AsyncIterator[WebSocketUpstream]:
        async with connect(
            request.url,
            additional_headers=request.headers,
            subprotocols=cast(Sequence[Subprotocol], list(request.subprotocols))
            or None,
            open_timeout=_HANDSHAKE_TIMEOUT_SECONDS,
            # Transparent to frame size. The library's 1 MiB default would close
            # a large chat frame with 1009 as though the peer had misbehaved.
            max_size=None,
            # The upstream is named by the gateway's own configuration and is an
            # internal hop. Left to itself this library reads ``HTTPS_PROXY`` /
            # ``ALL_PROXY`` from the process environment and would silently
            # re-route it; picking an upstream is configuration's job, not the
            # ambient environment's.
            proxy=None,
            # Protocol-level keepalive, which is dead-peer detection and NOT a
            # read deadline: a quiet-but-alive socket answers the ping and stays
            # open indefinitely. Left at the library's defaults.
        ) as connection:
            yield _WebsocketsUpstream(connection)
