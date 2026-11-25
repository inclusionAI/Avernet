"""Arca device SPI — Protocol for Arca sandbox lifecycle."""

from ._arca_sandbox_info import ArcaSandboxInfo
from ._errors import (
    ArcaSandboxError,
    ArcaSandboxNotFoundError,
    ArcaSandboxTimeoutError,
)
from ._protocols import ArcaSandbox, ArcaSandboxInfoReader, ArcaSandboxPlugin

__all__ = [
    "ArcaSandbox",
    "ArcaSandboxError",
    "ArcaSandboxInfo",
    "ArcaSandboxInfoReader",
    "ArcaSandboxNotFoundError",
    "ArcaSandboxPlugin",
    "ArcaSandboxTimeoutError",
]
