"""Plugin contracts crossed by the Engine materialization service."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from engine.community.core.resource_materialization.models import (
    MaterializationRequest,
    MaterializationResult,
)


@runtime_checkable
class BaasMaterializationClient(Protocol):
    """Pull one BaaS upload transfer into a caller-owned temporary file."""

    async def pull(
        self,
        request: MaterializationRequest,
        destination: Path,
    ) -> None: ...


@runtime_checkable
class BackendMaterializationCallbackClient(Protocol):
    """Report a terminal materialization result to Backend."""

    async def report(self, result: MaterializationResult) -> None: ...
