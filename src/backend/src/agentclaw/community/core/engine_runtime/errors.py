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
    "EngineUpstreamError",
]
