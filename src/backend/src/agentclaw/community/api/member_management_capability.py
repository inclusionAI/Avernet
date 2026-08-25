"""Service API Protocol for bot member-management capabilities."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MemberManagementCapabilityProtocol(Protocol):
    """Expose whether a bot uses engine-managed member semantics."""

    def uses_member_management_semantics(
        self, bot: object, bot_id: str | None = None
    ) -> bool: ...
