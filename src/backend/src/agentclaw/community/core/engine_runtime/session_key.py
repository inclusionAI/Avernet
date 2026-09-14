"""Session-key wire forms — how one engine wants a session id in a URL path.

The public surface takes a session id **verbatim** (``SessionIdPath``) and most
engines take it back the same way: a colon is legal in a path segment
(RFC 3986), so ``agent:default:default:c_01M2F9…:user:272471`` routes as-is and
the engine's own routes read it unchanged.

``teclaw`` does not. Its runtime is reached through the agentclaw proxy, which
refuses a path segment carrying those colons with a ``400 Bad Request`` before
the request ever reaches the engine::

    GET …/proxypass/TECLAW_b_01M2F8…@4:20003/api/sessions/agent:default:default:c_01M2F9…:user:272471/messages
    "HTTP/1.1 400 Bad Request"

So a teclaw session id has to travel base64-encoded. That is **one engine's
rule**, not a branch every handler should carry, so it lives here as a
strategy rather than as an ``if`` at each call site: the registry answers with
its default codec for every engine that has not registered anything — the
session id exactly as the caller passed it, which is what every engine but
teclaw wants — and ``teclaw`` registers :class:`Base64SessionKeyCodec`. An
engine that later needs its own rule registers a codec; no handler changes.

Which codecs exist and which engine each is bound to is the **composition
root's** call, not this module's: ``EngineRuntimeModule`` builds the registry
and injects it. Nothing here is process-wide state.

The encoded form is the one the engine reverses in ``decode_session_key``
(``src/engine/src/engine/community/shared/utils.py``): URL-safe base64 with the
padding stripped. URL-safe, because its alphabet (``A-Za-z0-9-_``) is already
safe in a path segment — standard base64's ``/`` would split the segment in
two — and unpadded, because that is the form the engine's decoder pads back
itself. The engine applies that decoder on every session route, so an encoded
id is accepted wherever a raw one is.
"""

from __future__ import annotations

import base64
from abc import abstractmethod
from typing import Protocol

from agentclaw.community.core.bot_management.engines.registry import (
    normalize_engine_type,
)

#: The engine whose bots run in a teclaw container, in the registry key form.
#: The canonical creation-time definition is
#: ``TeclawProvisionService.is_teclaw`` / ``DEFAULT_TECLAW_ENGINE_TYPES``
#: (``core/bot_management/services/teclaw_provision_service.py``); this is the
#: registration key for that engine's codec, not a second definition of what
#: teclaw *is*.
TECLAW_ENGINE_TYPE = "teclaw"


class SessionKeyCodec(Protocol):
    """Turn a public session id into the form one engine's routes accept.

    Implementations **inherit** this rather than satisfying it structurally, so
    a codec that never implements :meth:`encode` fails at construction instead
    of at the first forward, and the registry's contract has one declaration.

    Deliberately **not** ``@runtime_checkable``: that decorator exists to allow
    ``isinstance``/``issubclass`` against the Protocol *structurally*, which is
    the duck typing this contract is meant to close — and nothing needs it,
    since every codec here is a nominal subclass.
    """

    @abstractmethod
    def encode(self, session_key: str) -> str:
        """Return the wire form of ``session_key`` for this engine."""
        ...


class PassThroughSessionKeyCodec(SessionKeyCodec):
    """The engine takes the session id verbatim — every engine but teclaw.

    Deliberately identity rather than percent-encoding: the ids these engines
    answer with are already routed as-is today, and re-encoding them here would
    change the bytes on the wire for every bot on the surface to fix one
    engine's problem.
    """

    def encode(self, session_key: str) -> str:
        return session_key


class Base64SessionKeyCodec(SessionKeyCodec):
    """URL-safe, unpadded base64 — the engine's own ``encode_session_key``.

    Mirrors ``engine.community.shared.utils.encode_session_key`` byte for byte,
    which is what ``decode_session_key`` on the other side reverses.
    """

    def encode(self, session_key: str) -> str:
        return base64.urlsafe_b64encode(session_key.encode()).decode().rstrip("=")


class SessionKeyCodecRegistry:
    """Which codec an engine's session ids go through, by engine type.

    ``default`` is required: the composition root always has one to hand, and a
    registry that invented its own would decide a wire format the composition
    root thought it owned.
    """

    def __init__(self, default: SessionKeyCodec) -> None:
        self._default = default
        self._codecs: dict[str, SessionKeyCodec] = {}

    def register(self, engine_type: str, codec: SessionKeyCodec) -> None:
        """Bind ``codec`` to ``engine_type``, refusing a silent overwrite."""
        key = self._key(engine_type)
        if key in self._codecs:
            raise ValueError(f"session key codec already registered: {key}")
        self._codecs[key] = codec

    def resolve(self, engine_type: str) -> SessionKeyCodec:
        """Return the codec bound to ``engine_type``, or the default.

        An engine nobody registered a codec for gets the default — that is the
        registry's whole point. An **empty** engine type is not that case and
        raises: ``ac_bots.active_engine`` is ``NOT NULL`` with a default, so a
        blank one is corrupt data rather than a bot whose engine happens to be
        unremarkable, and guessing a wire format for it would send a request
        somewhere on the strength of a value nobody wrote.
        """
        return self._codecs.get(self._key(engine_type), self._default)

    @staticmethod
    def _key(engine_type: str) -> str:
        """Normalise an engine's spelling, refusing an absent one.

        ``normalize_engine_type`` substitutes a default engine for a missing
        value, which is the wrong answer on both sides of this registry: a
        registration keyed on a fallback binds the codec to an engine the
        caller never named, and a lookup on one picks that engine's wire format
        for a bot we cannot identify. So the fallback is never taken — an
        engine type that is empty or blank is refused instead.
        """
        key = normalize_engine_type(engine_type, default="")
        if not key:
            raise ValueError("engine type is required to resolve a session key codec")
        return key


__all__ = [
    "Base64SessionKeyCodec",
    "PassThroughSessionKeyCodec",
    "SessionKeyCodec",
    "SessionKeyCodecRegistry",
    "TECLAW_ENGINE_TYPE",
]
