"""Service API Protocol for the harness content scanner.

Re-export only. The Protocol is defined in its owning core module
(``core/harness/content_scanner_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.harness.content_scanner_service_protocol import (
    ContentScannerProtocol,
)

__all__ = [
    "ContentScannerProtocol",
]
