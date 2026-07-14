"""Device plugins — stub and real implementations."""

from secbaas.community.plugins.sandbox.arca import (
    StubArcaSandboxPlugin,
)
from secbaas.community.plugins.sandbox.desktop import (
    RealDesktopSandboxPlugin,
    StubDesktopSandboxPlugin,
)

__all__ = [
    "RealDesktopSandboxPlugin",
    "StubArcaSandboxPlugin",
    "StubDesktopSandboxPlugin",
]
