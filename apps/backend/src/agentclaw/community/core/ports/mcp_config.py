"""An explicit no-write instruction for Bot MCP configuration.

Apply claims accept ``PRESERVE`` to leave configuration untouched, ``None`` to
clear it, or a mapping to replace it. This is an in-process command contract;
the marker is never persisted or included in a Genome revision.
"""

from enum import Enum
from typing import Any

__all__ = ["McpConfigDirective", "McpConfigWriteIntent"]


class McpConfigDirective(Enum):
    PRESERVE = "preserve"


McpConfigWriteIntent = dict[str, Any] | None | McpConfigDirective
