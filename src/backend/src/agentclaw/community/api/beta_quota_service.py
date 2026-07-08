"""Service API Protocol for beta invite quota."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BetaQuotaServiceProtocol(Protocol):
    def get_quota(self, env: str) -> dict[str, int]: ...

    def adjust_quota(self, env: str, delta: int, entity_id: str) -> dict[str, int]: ...
