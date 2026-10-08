"""HTTP-neutral runtime-binding resolution failures."""

from __future__ import annotations


class RuntimeBindingResolutionError(RuntimeError):
    """Base failure for private runtime-target resolution."""


class RuntimeBotNotFoundError(RuntimeBindingResolutionError):
    """The owner-scoped Bot row is unavailable."""


class RuntimeBindingNotFoundError(RuntimeBindingResolutionError):
    """The resolved binding is missing, inactive, or does not match its scope."""


class CallerInstanceNotReadyError(RuntimeBindingResolutionError):
    """The authenticated user's independent Caller instance is not ready."""


__all__ = [
    "CallerInstanceNotReadyError",
    "RuntimeBindingNotFoundError",
    "RuntimeBindingResolutionError",
    "RuntimeBotNotFoundError",
]
