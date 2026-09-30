"""Service API Protocol for the SkillParameterService factory."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillParameterServiceFactoryProtocol(Protocol):
    """Service API for minting SkillParameterService for a (bot_id, user_id) pair."""

    async def create(self, *args: Any, **kwargs: Any) -> Any: ...
