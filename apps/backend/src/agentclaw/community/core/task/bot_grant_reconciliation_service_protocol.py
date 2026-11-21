"""Best-effort reconciliation of secbaas grants for Bots owned by one Human."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BotGrantReconciliationServiceProtocol(Protocol):
    """Grant the task API key to one owned Bot or the owner's current fleet."""

    async def reconcile(
        self,
        *,
        owner_id: str,
        cookie: str,
        referer: str,
        bot_id: str | None = None,
    ) -> None:
        """Run a failure-isolated, idempotent grant reconciliation."""
        ...


__all__ = ["BotGrantReconciliationServiceProtocol"]
