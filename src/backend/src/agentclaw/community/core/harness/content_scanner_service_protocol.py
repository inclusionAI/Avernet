"""Service API Protocol for the harness content scanner.

Note: the current harness router also reaches into ContentScanner
private attributes (``_bot_profile``, ``_mcp_center``,
``_patch_library``, ``_enrich_with_templates``). Those are not part
of the documented Protocol surface — they continue to work because
DI returns the concrete ``ContentScanner`` instance, which has them
in addition to the methods declared here. Tightening that coupling
is tracked separately as harness encapsulation cleanup.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ContentScannerProtocol(Protocol):
    """Service API for scanning bot content against patch templates."""

    async def scan(self, *args: Any, **kwargs: Any) -> Any: ...
