"""
Engine framework exceptions.

These are raised across the engine framework — by EngineRegistry, EngineManager,
BaseEngine, and engine implementations. Web routes catch them and translate to
HTTP errors (e.g. CapabilityNotSupportedError → 501).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.community.core.engine.capability import Capability


class EngineError(Exception):
    """Base class for all engine framework errors."""


class EngineNotFoundError(EngineError):
    """Raised when an engine name is not registered in EngineRegistry."""

    def __init__(self, engine_name: str) -> None:
        super().__init__(f"Engine not registered: {engine_name!r}")
        self.engine_name = engine_name


class CapabilityNotSupportedError(EngineError):
    """Raised when code attempts to use a capability the current engine does not support.

    Web routes should catch this and respond with HTTP 501.
    """

    def __init__(self, engine_name: str, capability: Capability) -> None:
        super().__init__(
            f"Engine {engine_name!r} does not support capability {capability.value!r}"
        )
        self.engine_name = engine_name
        self.capability = capability


__all__ = [
    "CapabilityNotSupportedError",
    "EngineError",
    "EngineNotFoundError",
]
