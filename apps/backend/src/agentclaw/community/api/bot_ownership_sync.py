"""Ownership sync Service API v1: bounded, memory-only admission.

Business callers submit explicit ownership work after commit. Admission does
not read persistence, perform delivery, or confirm remote state; admitted work
can be lost on process exit. This does not change the managers-only API.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncReason


@runtime_checkable
class BotOwnershipSyncProtocol(Protocol):
    """Request best-effort alignment of BCS with current Backend ownership."""

    def request_ownership_sync(
        self,
        *,
        bot_pk: int,
        env: str,
        avernet_tenant: str,
        reason: SyncReason,
    ) -> SubmissionResult:
        """Admit or coalesce work without waiting for database or network I/O.

        Busy/disabled states return SubmissionResult rather than raising.
        ``reason`` is diagnostic only; it does not select the work kind.
        """
        ...
