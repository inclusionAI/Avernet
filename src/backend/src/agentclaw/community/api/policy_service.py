"""Service API Protocol for access-policy decisions and quota."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PolicyServiceProtocol(Protocol):
    """Service API for entity allow/disallow + quota policy."""

    def check(self, *, entity_id: str, entity_type: str) -> bool: ...

    def allow(self, *, entity_id: str, entity_type: str) -> None: ...

    def disallow(self, *, entity_id: str, entity_type: str) -> None: ...

    def get_bots_ceiling(self, *, entity_id: str, default: int = 5) -> int: ...

    def set_bots_ceiling(self, *, entity_id: str, ceiling: int) -> None: ...

    def get_quota(self) -> dict: ...
