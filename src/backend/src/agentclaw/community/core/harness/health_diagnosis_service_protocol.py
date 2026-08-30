"""Service API for Bot health diagnosis."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HealthDiagnosisServiceProtocol(Protocol):
    """Start and query persisted Bot health diagnoses."""

    async def start(
        self,
        *,
        bot_id: str,
        owner_id: str,
        operator_id: str,
    ) -> dict[str, Any]: ...

    async def get_recent(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> dict[str, Any] | None: ...

    async def get_by_id(
        self,
        *,
        scan_id: int,
        bot_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...


__all__ = ["HealthDiagnosisServiceProtocol"]
