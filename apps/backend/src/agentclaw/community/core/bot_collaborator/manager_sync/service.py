"""Best-effort read/project/replace orchestration for one Bot.

There is deliberately no distributed coordination. Each attempt reads fresh
facts, builds a full replacement snapshot, and asks BCS to apply it. Concurrent
replicas may race and a late snapshot may win; the feature contract accepts
that tradeoff in exchange for a small, recoverable side channel.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
    SyncEvent,
)
from agentclaw.community.core.bot_collaborator.manager_sync.projector import (
    build_snapshot,
)
from agentclaw.community.core.bot_collaborator.manager_sync.retry import (
    SyncRunPolicy,
    backoff_delay,
    default_jitter,
)
from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    DeliveryFailure,
    SnapshotTooLarge,
    SnapshotUnavailable,
    SyncKey,
    SyncOutcome,
)
from agentclaw.community.plugin_api.bcs_managers import (
    BcsManagersError,
    BcsManagersPlugin,
)

Wait = Callable[[float], Awaitable[None]]


class _Retry:
    __slots__ = ()


_RETRY = _Retry()


class BotManagerSyncService:
    """Run one bounded, best-effort manager projection."""

    def __init__(
        self,
        reader: ManagerSyncReadRepository,
        bcs: BcsManagersPlugin,
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
        """Read and deliver fresh facts, retrying transient delivery failures."""

        deadline = self._clock() + self._policy.budget_seconds
        for attempt in range(1, self._policy.max_attempts + 1):
            if attempt > 1:
                remaining = deadline - self._clock()
                delay = backoff_delay(
                    retry_index=attempt - 1,
                    remaining_seconds=remaining,
                    jitter=self._jitter,
                )
                if delay is None:
                    self._record(
                        SyncEvent.DEADLINE_EXPIRED,
                        key=key,
                        outcome=SyncOutcome.FAILED,
                        attempt=attempt - 1,
                    )
                    return SyncOutcome.FAILED
                await self._wait(delay)

            if deadline <= self._clock():
                self._record(
                    SyncEvent.DEADLINE_EXPIRED,
                    key=key,
                    outcome=SyncOutcome.FAILED,
                    attempt=attempt,
                )
                return SyncOutcome.FAILED

            outcome = await self._attempt(key, deadline=deadline, attempt=attempt)
            if outcome is not _RETRY:
                return outcome

        self._record(
            SyncEvent.RETRIES_EXHAUSTED,
            key=key,
            outcome=SyncOutcome.FAILED,
            attempt=self._policy.max_attempts,
            counts={"attempts_used": self._policy.max_attempts},
        )
        return SyncOutcome.FAILED

    async def _attempt(
        self, key: SyncKey, *, deadline: float, attempt: int
    ) -> SyncOutcome | _Retry:
        try:
            facts = await asyncio.to_thread(self._reader.read_facts, key)
        except asyncio.CancelledError:
            self._record(
                SyncEvent.CANCELLED,
                key=key,
                outcome=SyncOutcome.FAILED,
                attempt=attempt,
            )
            raise
        except Exception:
            self._record(
                SyncEvent.READ_FAILED,
                key=key,
                outcome=SyncOutcome.FAILED,
                attempt=attempt,
            )
            return SyncOutcome.FAILED

        if facts.key != key:
            self._record(
                SyncEvent.READ_FAILED,
                key=key,
                outcome=SyncOutcome.FAILED,
                attempt=attempt,
            )
            return SyncOutcome.FAILED

        remaining = deadline - self._clock()
        if remaining <= 0:
            # A synchronous repository call cannot be forcibly cancelled. A
            # late result is discarded here and is never delivered.
            self._record(
                SyncEvent.READ_LATE,
                key=key,
                outcome=SyncOutcome.FAILED,
                attempt=attempt,
            )
            return SyncOutcome.FAILED

        try:
            snapshot = build_snapshot(facts)
        except (SnapshotUnavailable, SnapshotTooLarge, ValueError):
            self._record(
                SyncEvent.PROJECTION_FAILED,
                key=key,
                outcome=SyncOutcome.FAILED,
                attempt=attempt,
            )
            return SyncOutcome.FAILED

        remaining = deadline - self._clock()
        if remaining <= 0:
            self._record(
                SyncEvent.DEADLINE_EXPIRED,
                key=key,
                outcome=SyncOutcome.FAILED,
                attempt=attempt,
            )
            return SyncOutcome.FAILED

        try:
            result = await asyncio.wait_for(
                self._bcs.replace_managers(snapshot, timeout_seconds=remaining),
                timeout=remaining,
            )
        except asyncio.CancelledError:
            self._record(
                SyncEvent.CANCELLED,
                key=key,
                outcome=SyncOutcome.FAILED,
                attempt=attempt,
            )
            raise
        except BcsManagersError as exc:
            if exc.kind is DeliveryFailure.REJECTED:
                self._record(
                    SyncEvent.SEND_REJECTED,
                    key=key,
                    outcome=SyncOutcome.FAILED,
                    attempt=attempt,
                )
                return SyncOutcome.FAILED
            return self._retryable(key, attempt)
        except Exception:
            return self._retryable(key, attempt)

        expected = len(snapshot.manager_user_ids)
        if int(result.total) != expected:
            self._record(
                SyncEvent.SEND_DRIFT,
                key=key,
                outcome=SyncOutcome.DRIFT,
                attempt=attempt,
                counts={"expected": expected, "reported": int(result.total)},
            )
            return SyncOutcome.DRIFT
        if expected == 0:
            self._record(
                SyncEvent.SEND_ACKNOWLEDGED_EMPTY,
                key=key,
                outcome=SyncOutcome.ACKNOWLEDGED_EMPTY,
                attempt=attempt,
            )
            return SyncOutcome.ACKNOWLEDGED_EMPTY
        self._record(
            SyncEvent.SEND_CONFIRMED,
            key=key,
            outcome=SyncOutcome.CONFIRMED,
            attempt=attempt,
            counts={
                "managers": expected,
                "added": int(result.added),
                "removed": int(result.removed),
            },
        )
        return SyncOutcome.CONFIRMED

    def _retryable(self, key: SyncKey, attempt: int) -> _Retry:
        self._record(
            SyncEvent.SEND_RETRYABLE,
            key=key,
            outcome=SyncOutcome.FAILED,
            attempt=attempt,
        )
        return _RETRY

    def _record(
        self,
        event: SyncEvent,
        *,
        key: SyncKey,
        outcome: SyncOutcome,
        attempt: int = 0,
        counts: dict[str, int] | None = None,
    ) -> None:
        self._diagnostics.record(
            event,
            key=key,
            outcome=outcome,
            attempt=attempt,
            counts=counts or {},
        )
