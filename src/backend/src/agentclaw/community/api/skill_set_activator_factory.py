"""Service API Protocol for the SkillSet activator factory."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillSetActivatorFactoryProtocol(Protocol):
    """Service API for minting per-bot skill-set activators."""

    def create(self, *args: Any, **kwargs: Any) -> Any: ...
