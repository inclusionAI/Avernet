"""Service API Protocol for skill propagation."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillPropagationServiceProtocol(Protocol):
    """Service API for propagating skill changes downstream."""

    def propagate_on_upgrade(self, *args: Any, **kwargs: Any) -> Any: ...

    def propagate_on_removal(self, *args: Any, **kwargs: Any) -> Any: ...
