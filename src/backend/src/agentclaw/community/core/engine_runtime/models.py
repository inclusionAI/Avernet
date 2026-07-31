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

    The engine's ``warning`` (its caveat for a capability it declares as
    supported-with-a-limitation) is deliberately **not** carried. It is logged
    server-side and goes no further — see ``relay._normalise``.
    """

    data: Any = None
    total: int | None = None


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
    #: ``ac_bots`` primary key of the row ownership was just proven against.
    #: Internal, never published — it exists because ``bot_id`` is **not**
    #: unique across owners (no unique constraint on the column, and
    #: ``create_bot_for_others`` gives every user a bot called ``default``), so
    #: any second query keyed on ``bot_id`` alone could select a different
    #: owner's row. This is the discriminator that keeps the service-bot
    #: publish lookup on the bot the caller actually owns.
    bot_pk: int = 0


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
