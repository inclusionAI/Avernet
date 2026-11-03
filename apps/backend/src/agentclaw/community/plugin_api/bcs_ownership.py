"""Ownership delivery Plugin API v1; no transport objects or response bodies.

Core supplies a current snapshot; the Plugin makes one bounded attempt.
Retry policy belongs to Core, not to the delivery implementation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.kernel.bot_ownership_sync import (
    OwnershipChange,
    OwnershipSnapshot,
)
from agentclaw.community.plugin_api.base import Plugin


class BcsOwnershipError(Exception):
    """Unconfirmed delivery, carrying only a stable classification and code."""

    def __init__(self, kind: DeliveryFailure, code: str) -> None:
        super().__init__(f"{kind.value} {code}")
        self._kind = kind
        self._code = code

    @property
    def kind(self) -> DeliveryFailure:
        return self._kind

    @property
    def code(self) -> str:
        return self._code


@runtime_checkable
class BcsOwnershipPlugin(Plugin, Protocol):
    """Replace ownership for one existing stable Bot without initializing it."""

    async def replace_ownership(
        self,
        snapshot: OwnershipSnapshot,
        *,
        timeout_seconds: float,
    ) -> OwnershipChange:
        """Deliver once, with no hidden retries; failures raise BcsOwnershipError.

        A returned change can represent a missing-Bot no-op, not necessarily
        successful alignment. Core interprets it against the target snapshot.
        """
        ...

    async def aclose(self) -> None:
        """Release owned resources on the delivery loop, not injected clients."""
        ...
