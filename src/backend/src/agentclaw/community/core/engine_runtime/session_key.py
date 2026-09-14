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
:class:`PassThroughSessionKeyCodec` for every engine that has not registered
anything — the session id exactly as the caller passed it, which is what every
engine but teclaw wants — and ``teclaw`` registers
:class:`Base64SessionKeyCodec`. An engine that later needs its own rule
registers a codec here; no handler changes.

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
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class SessionKeyCodec(Protocol):
    """Turn a public session id into the form one engine's routes accept."""

    def encode(self, session_key: str) -> str:
        """Return the wire form of ``session_key`` for this engine."""
        ...


class PassThroughSessionKeyCodec:
    """The default: the engine takes the session id verbatim.

    Every engine but teclaw. Deliberately identity rather than
    percent-encoding: the ids these engines answer with are already routed
    as-is today, and re-encoding them here would change the bytes on the wire
    for every bot on the surface to fix one engine's problem.
    """

    def encode(self, session_key: str) -> str:
        return session_key


class Base64SessionKeyCodec:
    """URL-safe, unpadded base64 — the engine's own ``encode_session_key``.

    Mirrors ``engine.community.shared.utils.encode_session_key`` byte for byte,
    which is what ``decode_session_key`` on the other side reverses.
    """

    def encode(self, session_key: str) -> str:
        return base64.urlsafe_b64encode(session_key.encode()).decode().rstrip("=")


class SessionKeyCodecRegistry:
    """Which codec an engine's session ids go through, by engine type."""

    def __init__(self, default: SessionKeyCodec | None = None) -> None:
        self._default: SessionKeyCodec = default or PassThroughSessionKeyCodec()
        self._codecs: dict[str, SessionKeyCodec] = {}

    def register(self, engine_type: str, codec: SessionKeyCodec) -> None:
        """Bind ``codec`` to ``engine_type``, refusing a silent overwrite."""
        key = normalize_engine_type(engine_type, default="")
        if key in self._codecs:
            raise ValueError(f"session key codec already registered: {key}")
        self._codecs[key] = codec

    def resolve(self, engine_type: str | None) -> SessionKeyCodec:
        """Return the codec for ``engine_type``, or the pass-through default.

        An unknown, empty or missing engine resolves to the default on
        purpose: not knowing which engine a bot runs is not a reason to change
        what its session ids look like.
        """
        return self._codecs.get(
            normalize_engine_type(engine_type, default=""), self._default
        )


def _build_default_registry() -> SessionKeyCodecRegistry:
    """Assemble the process-wide registry with every engine that has a rule.

    Pure and side-effect free — it only instantiates stateless codecs — so it
    is built eagerly at import, like ``EngineProvisioningRegistry``.
    """
    registry = SessionKeyCodecRegistry()
    registry.register(TECLAW_ENGINE_TYPE, Base64SessionKeyCodec())
    return registry


_REGISTRY: SessionKeyCodecRegistry = _build_default_registry()


def get_session_key_codec_registry() -> SessionKeyCodecRegistry:
    """Return the process-wide session-key codec registry."""
    return _REGISTRY


def encode_session_key(engine_type: str | None, session_key: str) -> str:
    """Return ``session_key`` in the form ``engine_type``'s routes accept."""
    return get_session_key_codec_registry().resolve(engine_type).encode(session_key)


__all__ = [
    "Base64SessionKeyCodec",
    "PassThroughSessionKeyCodec",
    "SessionKeyCodec",
    "SessionKeyCodecRegistry",
    "TECLAW_ENGINE_TYPE",
    "encode_session_key",
    "get_session_key_codec_registry",
]
