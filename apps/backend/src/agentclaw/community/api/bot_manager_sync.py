"""Service API Protocol for bot manager sync (DI surface).

The narrow contract business code calls to request a Backend→BCS
manager-set sync. Two entry points cover single-Bot changes and
Space-triggered fan-out; both only *submit* work — they never block on
BCS and never read persistence themselves (the dispatch, read, and
delivery pieces live behind later tasks, reached via DI against this
Protocol).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.bot_manager_sync import (
    SpaceChange,
    SubmissionResult,
    SyncReason,
)


@runtime_checkable
class BotManagerSyncProtocol(Protocol):
    """Request that BCS's manager set be brought back to the facts."""

    def request_sync(
        self,
        *,
        bot_pk: int,
        env: str,
        avernet_tenant: str,
        reason: SyncReason,
    ) -> SubmissionResult:
        """Submit one Bot for manager sync.

        Returns one of QUEUED / COALESCED / DISABLED / REJECTED; it
        does not run the sync. Implementations must not raise for
        busy/downstream states — those are SubmissionResult values.
        """
        ...

    def request_space_sync(self, change: SpaceChange) -> SubmissionResult:
        """Submit a Space-triggered fan-out for manager sync.

        ``change.user_ids`` empty means every Bot under
        ``space_refs`` with an explicit collaborator.
        """
        ...