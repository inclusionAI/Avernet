"""Service API Protocol for harness patch execution."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PatchEngineProtocol(Protocol):
    """Service API for applying / previewing / rolling back patches."""

    async def preview(self, *args: Any, **kwargs: Any) -> Any: ...

    async def apply(self, *args: Any, **kwargs: Any) -> Any: ...

    async def rollback(self, *args: Any, **kwargs: Any) -> Any: ...

    async def rollback_by_patch(self, *args: Any, **kwargs: Any) -> Any: ...
