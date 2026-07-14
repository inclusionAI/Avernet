"""Service API for the default-bot Passport repair operation."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DefaultBotPassportRepairServiceProtocol(Protocol):
    """Transport-independent repair service consumed by HTTP adapters."""

    def repair(
        self,
        *,
        target_user_id: str,
        target_env: str,
        operator_user_id: str,
        operator_name: str,
    ) -> dict[str, Any]: ...
