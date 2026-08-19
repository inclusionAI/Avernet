"""Core-local ports consumed by the harness domain."""

from __future__ import annotations

from typing import Any, Protocol


class ContentScannerPort(Protocol):
    """Scan Bot content for the persisted health-diagnosis workflow."""

    async def scan(self, *args: Any, **kwargs: Any) -> Any: ...


__all__ = ["ContentScannerPort"]
