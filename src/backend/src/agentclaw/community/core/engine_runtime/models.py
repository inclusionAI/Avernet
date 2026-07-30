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
        limited: True when the engine served this with a declared limitation,
            so the payload may be incomplete. Deliberately a **flag, not the
            engine's message**: those strings are internal engineering text
            ("teamclaw-aicoding-relay has no explicit sessions.create…") and
            some are not English ("通过 mcporter 命令启动"), and this surface
            promises fixed English messages that leak no internals. The adapter
            renders the public wording; core carries only the fact.
    """

    data: Any = None
    total: int | None = None
    limited: bool = False


@dataclass(frozen=True)
class BotFacts:
    """The only bot fields a runtime handler needs.

    Deliberately narrow. ``BotService.get_bot`` returns the full record with
    ``device_binding`` attached — ``device_id``, ``device_provider``,
    ``device_props`` — and handing that to public handlers puts device topology
    one ``envelope(bot)`` away from an external caller, on the surface built to
    stop publishing exactly that. Widen this only with a reason.
    """

    bot_id: str
    bot_type: str
    active_engine: str


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


__all__ = ["BotFacts", "ConnectionResult", "EngineResult", "SocketInfo"]
