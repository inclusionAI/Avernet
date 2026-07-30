"""Engine-runtime domain errors.

Semantic state only — **no HTTP status** (Rule 7, ``docs/arch/arch.rules.md``).
The public adapter owns the mapping to a status + fixed message; see
``adapters/http/openapi_v1/responses.py::ENVELOPE_ERRORS``.

These sit alongside, not instead of, the errors the relay re-raises from its
dependencies: ``BotNotFoundError`` / ``BotPermissionError`` (a bot that is not
the caller's — already a masked 404) and ``DeviceAdapterTimeoutError`` (the
transport's own deadline).
"""

from __future__ import annotations


class EngineRuntimeError(RuntimeError):
    """Base for every engine-runtime relay failure."""


class EngineDeviceNotReadyError(EngineRuntimeError):
    """The bot's device is not currently reachable.

    Cold, dormant, restarting, or without an active binding. Distinct from "not
    found" (the bot is the caller's) and from an internal error (nothing is
    broken) — the caller should retry rather than treat it as terminal.
    """


class EngineCapabilityUnsupportedError(EngineRuntimeError):
    """The bot's engine does not declare the capability this call needs.

    Raised when the device answers 501. The supported set differs per engine, so
    the same public path can succeed on one of a caller's bots and raise this on
    another; the capabilities endpoint is how a caller finds out in advance.
    """


class EngineBotTypeNotSupportedError(EngineRuntimeError):
    """This endpoint group is not offered for the bot's type.

    Not an engine capability question — a product rule. The sessions group is
    personal-bots-only because the engine's session list is not scoped per
    caller, so on a multi-caller ``service`` bot it would expose other callers'
    conversations to the bot's owner.
    """


class EngineHistoryDepthExceededError(EngineRuntimeError):
    """The requested message page reaches past the history depth served.

    A product rule like :class:`EngineBotTypeNotSupportedError`, not an engine
    condition — the engine is never asked. Message history is tail-limited, so
    the cost of a page is its whole window and the depth has a ceiling; a page
    beyond it cannot be served short, because a short page is how this surface
    signals the end of history and would report the ceiling as an exact total.
    """


class EngineResourceNotFoundError(EngineRuntimeError):
    """The engine answered 404 — the addressed resource does not exist.

    Distinct from :class:`EngineCapabilityUnsupportedError`. The transport
    raises its not-found error for *any* adapter 404, and the engine returns 404
    for ordinary missing resources — an unknown session
    (``api/session/router.py``), an unknown model id (``api/models/router.py``),
    an unknown engine name. Folding those into "capability unsupported" would
    tell a caller polling a deleted session that its bot lost the sessions
    capability. A capability the engine does not declare comes back as **501**
    from ``check_capability``, which is a different status and a different
    error.
    """


class EngineUpstreamError(EngineRuntimeError):
    """The device answered, but not with a usable success.

    Covers a non-2xx other than 501, and a 200 whose body carries
    ``success: false`` — the engine's envelope can report failure inside a
    successful HTTP response, and that must never reach a caller as success.
    """


__all__ = [
    "EngineRuntimeError",
    "EngineBotTypeNotSupportedError",
    "EngineCapabilityUnsupportedError",
    "EngineDeviceNotReadyError",
    "EngineHistoryDepthExceededError",
    "EngineResourceNotFoundError",
    "EngineUpstreamError",
]
