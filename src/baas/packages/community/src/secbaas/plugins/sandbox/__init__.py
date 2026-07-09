"""Device plugins — stub and real implementations."""

from secbaas.plugins.sandbox.arca import (
    StubArcaSandboxPlugin,
)
from secbaas.plugins.sandbox.desktop import (
    RealDesktopSandboxPlugin,
    StubDesktopSandboxPlugin,
)

__all__ = [
    "RealDesktopSandboxPlugin",
    "StubArcaSandboxPlugin",
    "StubDesktopSandboxPlugin",
]
