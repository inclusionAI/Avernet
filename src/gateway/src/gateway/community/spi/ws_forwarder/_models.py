"""Framework-neutral models for the WebSocketForwarder SPI.

The delivery adapter snapshots an inbound handshake into a
:class:`WebSocketForwardRequest` and relays frames through a
:class:`~gateway.community.spi.ws_forwarder.WebSocketUpstream`; neither type
depends on a web framework or on the client library that dials the upstream, so
flavors are interchangeable and the adapter never imports one.

:class:`WebSocketClosedError` exists for the same reason: a closed upstream has to
reach the adapter as a close code and a reason, not as the client library's own
exception type.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Handshake headers the client library composes for itself. Forwarding the
#: caller's copies would either be ignored or make the upstream handshake
#: contradict itself (two ``Sec-WebSocket-Key`` values, a subprotocol offer the
#: library did not make). Dropped alongside the hop-by-hop set, which already
#: covers ``connection`` and ``upgrade``.
WEBSOCKET_HANDSHAKE_HEADERS = frozenset(
    {
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
        "sec-websocket-accept",
    }
)


@dataclass(frozen=True)
class WebSocketForwardRequest:
    """A handshake to open against an upstream, addressed by absolute URL.

    ``subprotocols`` is what the client offered; the flavor offers the same set
    upstream so the negotiated value can be echoed back verbatim rather than
    guessed.
    """

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    subprotocols: tuple[str, ...] = ()


class WebSocketClosedError(Exception):
    """The upstream socket closed.

    ``code`` and ``reason`` are the peer's, so the adapter can carry them across
    to the client. ``1005`` (no status received) and ``1006`` (abnormal closure)
    are reported as the peer's state even though neither may be *sent* in a
    close frame — translating them is the adapter's job, not the flavor's.
    """

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(f"upstream websocket closed: {code} {reason}".rstrip())
        self.code = code
        self.reason = reason
