"""Desktop device SPI — Protocol for desktop/local Docker container lifecycle."""

from ._errors import SandboxPluginError, SandboxPluginErrorCode
from ._protocols import DesktopSandbox, DesktopSandboxPlugin

__all__ = [
    "DesktopSandbox",
    "DesktopSandboxPlugin",
    "SandboxPluginError",
    "SandboxPluginErrorCode",
]
