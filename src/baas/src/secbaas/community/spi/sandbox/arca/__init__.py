"""Arca device SPI — Protocol for Arca sandbox lifecycle."""

from ._errors import (
    ArcaSandboxConnectionError,
    ArcaSandboxError,
    ArcaSandboxNotFoundError,
    ArcaSandboxTimeoutError,
)
from ._protocols import ArcaSandbox, ArcaSandboxPlugin

__all__ = [
    "ArcaSandbox",
    "ArcaSandboxConnectionError",
    "ArcaSandboxError",
    "ArcaSandboxNotFoundError",
    "ArcaSandboxPlugin",
    "ArcaSandboxTimeoutError",
]
