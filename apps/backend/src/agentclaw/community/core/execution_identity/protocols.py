"""Service-side ports for execution identity transitions."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RuntimePassportTokenUpdaterProtocol(Protocol):
    def hot_update_passport_token_to_device(
        self, bot_id: str, user_id: str, token: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class ExecutionIdentityServiceProtocol(Protocol):
    def change_execution_identity(
        self,
        *,
        bot_id: str,
        owner_id: str,
        action: str,
        modifier_id: str,
        execution_workno: str | None = None,
        identity_type: str | None = None,
    ) -> dict[str, Any]: ...

    def resolve_execution_workno(self, *, bot_pk: int, owner_id: str) -> str: ...


__all__ = [
    "ExecutionIdentityServiceProtocol",
    "RuntimePassportTokenUpdaterProtocol",
]
