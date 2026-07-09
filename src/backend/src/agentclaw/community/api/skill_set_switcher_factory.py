"""Service API Protocol for the SkillSet switcher factory."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillSetSwitcherFactoryProtocol(Protocol):
    """Service API for minting per-bot skill-set switchers."""

    def create(self, *args: Any, **kwargs: Any) -> Any: ...
