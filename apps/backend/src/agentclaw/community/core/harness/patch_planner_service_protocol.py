"""Service API Protocol for the harness patch planner."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PatchPlannerProtocol(Protocol):
    """Service API for generating + persisting patch plans from scan findings."""

    async def generate_and_save_patches(self, *args: Any, **kwargs: Any) -> Any: ...
