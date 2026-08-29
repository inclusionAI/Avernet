"""Service API Protocol for bot data initialization."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DataInitServiceProtocol(Protocol):
    """Service API for seeding initial bot data and reading its safe status."""

    async def trigger_init(
        self,
        bot_id: str,
        owner_id: str,
        entity_id: str,
        entity_type: str,
        force: bool = False,
        iam_token: str | None = None,
    ) -> dict[str, str]: ...

    def get_status(self, bot_id: str, owner_id: str) -> dict[str, str | None]: ...
