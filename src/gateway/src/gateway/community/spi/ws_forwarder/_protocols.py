"""WebSocketForwarder Protocol — relay a socket to an upstream, both ways.

Kept separate from the HTTP :class:`~gateway.community.spi.forwarder.Forwarder`
because the lifecycles differ: one request yields one response, whereas a socket
carries frames in both directions until a side closes it. Extending the HTTP
Protocol would make every flavor grow a method it cannot implement.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from ._models import WebSocketForwardRequest


class WebSocketUpstream(Protocol):
    """One open upstream socket, readable and writable until it closes."""

    @property
    def subprotocol(self) -> str:
        """The negotiated subprotocol, or ``""`` when none was.

        A string rather than an optional so no caller branches on a sentinel;
        the adapter echoes this back to the client on accept.
        """
        ...

    async def send(self, message: str | bytes) -> None:
        """Send one frame — ``str`` as text, ``bytes`` as binary."""
        ...

    async def receive(self) -> str | bytes:
        """The next frame, or raise ``WebSocketClosedError`` when the peer closed."""
        ...

    async def close(self, code: int, reason: str) -> None:
        """Close the upstream, carrying the client's code and reason across."""
        ...


class WebSocketForwarder(Protocol):
    """Opens upstream sockets on behalf of a client handshake.

    ``connect`` is an async context manager so the upstream is held open while
    the caller relays frames and released on exit::

        async with ws_forwarder.connect(request) as upstream:
            await upstream.send("hello")

    It is opened **before** the client handshake is accepted, so an upstream
    that cannot be reached refuses the handshake rather than leaving a client
    holding an accepted socket the gateway cannot serve.

    Implementations:
    - WebsocketsForwarder: ``websockets``-backed (open-source default).
    """

    def connect(
        self, request: WebSocketForwardRequest
    ) -> AbstractAsyncContextManager[WebSocketUpstream]:
        """Open the upstream socket named by ``request.url``."""
        ...
