"""Engine-runtime value objects — the relay's typed outputs.

Deliberately transport-agnostic: the relay returns these, and the public
adapter maps them to ``Envelope`` / ``Page``. Nothing here knows about HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EngineResult:
    """One normalised engine response.

    The engine's own envelope is ``{success, data, message, warning, total}``
    (``src/engine/.../api/response.py``). ``success`` is consumed by the relay —
    a false value raises rather than reaching a caller — so only the three
    fields a caller can act on survive here.

    Fields:
        data: the engine's ``data`` payload, whatever shape that group uses.
            ``None`` when the engine returned no payload.
        total: the engine's ``total`` when it reports one. Most list routes do
            **not**, hence ``None`` rather than ``0`` — "unknown" and "empty"
            are different, and a caller-visible ``total`` must not invent a
            number.
        warning: the engine's caveat for a capability it declares as
            supported-with-a-limitation. Empty when there is none.
    """

    data: Any = None
    total: int | None = None
    warning: str = ""


@dataclass(frozen=True)
class SocketInfo:
    """One WebSocket a caller may open against a bot.

    ``url`` is complete and opaque — the caller opens it verbatim. Nothing here
    exposes the proxypass target, the connection type, or a bare token; that
    hand-off is exactly what the public surface replaces.
    """

    kind: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectionResult:
    """The sockets a bot's active engine actually serves.

    ``sockets`` is a list rather than a map keyed by kind so the public schema
    generates a real typed enum on ``SocketInfo.kind`` in every client
    generator; an enum-keyed object degrades to an untyped map in most.

    ``expires_at`` is an ISO 8601 UTC instant. Mandatory: without it a caller
    cannot tell a stale connection from a broken one, and would retry the socket
    instead of re-fetching the credential.
    """

    engine: str
    expires_at: str
    sockets: list[SocketInfo] = field(default_factory=list)


__all__ = ["ConnectionResult", "EngineResult", "SocketInfo"]
