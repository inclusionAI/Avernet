"""Desktop sandbox plugin — real and stub implementations."""

from ._real import RealDesktopSandbox, RealDesktopSandboxPlugin
from ._stub import StubDesktopSandbox, StubDesktopSandboxPlugin

__all__ = [
    "RealDesktopSandbox",
    "RealDesktopSandboxPlugin",
    "StubDesktopSandbox",
    "StubDesktopSandboxPlugin",
]
