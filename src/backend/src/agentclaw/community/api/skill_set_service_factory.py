"""Service API Protocol for the SkillSetService factory."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillSetServiceFactoryProtocol(Protocol):
    """Service API for minting per-request SkillSetService instances."""

    def create(self, *args: Any, **kwargs: Any) -> Any: ...
