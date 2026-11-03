"""Best-effort current-Owner delivery, with fresh reads and one bounded budget.

This service owns no persisted delivery state or manager follow-up. A failed
round never prevents a later independent synchronization.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
    SyncEvent,
)
from agentclaw.community.core.bot_collaborator.manager_sync.retry import (
    SyncRunPolicy,
    backoff_delay,
    default_jitter,
)
from agentclaw.community.core.bot_management.ownership_sync.projector import (
    build_snapshot,
)
from agentclaw.community.core.repository.protocols.bot.ownership_sync import (
    OwnershipSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    DeliveryFailure,
    SnapshotUnavailable,
    SyncKey,
    SyncOutcome,
    SyncWorkKind,
)
from agentclaw.community.plugin_api.bcs_ownership import (
    BcsOwnershipError,
    BcsOwnershipPlugin,
)

Wait = Callable[[float], Awaitable[None]]


class BotOwnershipSyncService:
    """Read, project and deliver current ownership without coordinating replicas."""

    def __init__(
        self,
        reader: OwnershipSyncReadRepository,
        bcs: BcsOwnershipPlugin,
        *,
        policy: SyncRunPolicy | None = None,
        clock: Callable[[], float] | None = None,
        jitter: Callable[[float], float] | None = None,
        wait: Wait | None = None,
        diagnostics: SyncDiagnostics | None = None,
    ) -> None:
        self._reader = reader
        self._bcs = bcs
        self._policy = policy or SyncRunPolicy()
        self._clock = clock or time.monotonic
        self._jitter = jitter or default_jitter
        self._wait = wait or asyncio.sleep
        self._diagnostics = diagnostics or SyncDiagnostics()

    async def sync_once(self, key: SyncKey) -> SyncOutcome:
        """Run bounded attempts; uncertainty retries, refusal and drift do not."""
        started = self._clock()
        deadline = started + self._policy.budget_seconds
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                if attempt > 1:
                    delay = backoff_delay(
                        retry_index=attempt - 1,
                        remaining_seconds=deadline - self._clock(),
                        jitter=self._jitter,
                    )
                    if delay is None:
                        return self._record(
                            SyncEvent.DEADLINE_EXPIRED, key, attempt, started
                        )
                    await self._wait(delay)
                if self._clock() >= deadline:
                    return self._record(
                        SyncEvent.DEADLINE_EXPIRED, key, attempt, started
                    )
                try:
                    facts = await asyncio.to_thread(self._reader.read_facts, key)
                except Exception:
                    return self._record(SyncEvent.READ_FAILED, key, attempt, started)
                if facts.key != key:
                    return self._record(SyncEvent.READ_FAILED, key, attempt, started)
                if self._clock() >= deadline:
                    # A synchronous read cannot be forcibly stopped; discard it.
                    return self._record(SyncEvent.READ_LATE, key, attempt, started)
                try:
                    snapshot = build_snapshot(facts)
                except (SnapshotUnavailable, ValueError):
                    return self._record(
                        SyncEvent.PROJECTION_FAILED, key, attempt, started
                    )
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return self._record(
                        SyncEvent.DEADLINE_EXPIRED, key, attempt, started
                    )
                try:
                    result = await asyncio.wait_for(
                        self._bcs.replace_ownership(
                            snapshot, timeout_seconds=remaining
                        ),
                        timeout=remaining,
                    )
                except BcsOwnershipError as exc:
                    if exc.kind is DeliveryFailure.REJECTED:
                        return self._record(
                            SyncEvent.SEND_REJECTED,
                            key,
                            attempt,
                            started,
                            error_code=exc.code,
                        )
                    self._record(
                        SyncEvent.SEND_RETRYABLE,
                        key,
                        attempt,
                        started,
                        error_code=exc.code,
                    )
                    continue
                except Exception:
                    self._record(
                        SyncEvent.SEND_RETRYABLE,
                        key,
                        attempt,
                        started,
                        error_code="unclassified_delivery",
                    )
                    continue
                if result.bot_id != snapshot.bcs_bot_id:
                    return self._record(
                        SyncEvent.SEND_DRIFT,
                        key,
                        attempt,
                        started,
                        SyncOutcome.DRIFT,
                        result.request_id,
                    )
                if result.owner_user_id is None:
                    return self._record(
                        SyncEvent.SEND_BOT_MISSING_NOOP,
                        key,
                        attempt,
                        started,
                        SyncOutcome.BOT_MISSING_NOOP,
                        result.request_id,
                    )
                if result.owner_user_id != snapshot.owner_user_id:
                    return self._record(
                        SyncEvent.SEND_DRIFT,
                        key,
                        attempt,
                        started,
                        SyncOutcome.DRIFT,
                        result.request_id,
                    )
                return self._record(
                    SyncEvent.SEND_CONFIRMED,
                    key,
                    attempt,
                    started,
                    SyncOutcome.CONFIRMED,
                    result.request_id,
                )
            except asyncio.CancelledError:
                self._record(SyncEvent.CANCELLED, key, attempt, started)
                raise
        return self._record(
            SyncEvent.RETRIES_EXHAUSTED,
            key,
            self._policy.max_attempts,
            started,
        )

    def _record(
        self,
        event: SyncEvent,
        key: SyncKey,
        attempt: int,
        started: float,
        outcome: SyncOutcome = SyncOutcome.FAILED,
        request_id: str = "",
        error_code: str = "",
    ) -> SyncOutcome:
        self._diagnostics.record(
            event,
            key=key,
            outcome=outcome,
            attempt=attempt,
            work_kind=SyncWorkKind.OWNERSHIP,
            request_id=request_id,
            elapsed_ms=max(0, int((self._clock() - started) * 1000)),
            error_code=error_code,
        )
        return outcome
