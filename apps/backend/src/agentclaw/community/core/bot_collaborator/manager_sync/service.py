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
                    return self._finish(
                        SyncEvent.DEADLINE_EXPIRED, key, attempt - 1
                    )
                await self._wait(delay)

            if deadline <= self._clock():
                return self._finish(SyncEvent.DEADLINE_EXPIRED, key, attempt)

            outcome = await self._attempt(key, deadline=deadline, attempt=attempt)
            if outcome is not _RETRY:
                return outcome

        return self._finish(
            SyncEvent.RETRIES_EXHAUSTED,
            key,
            self._policy.max_attempts,
            counts={"attempts_used": self._policy.max_attempts},
        )

    async def _attempt(
        self, key: SyncKey, *, deadline: float, attempt: int
    ) -> SyncOutcome | _Retry:
        try:
            facts = await asyncio.to_thread(self._reader.read_facts, key)
        except asyncio.CancelledError:
            self._finish(SyncEvent.CANCELLED, key, attempt)
            raise
        except Exception:
            return self._finish(SyncEvent.READ_FAILED, key, attempt)

        if facts.key != key:
            return self._finish(SyncEvent.READ_FAILED, key, attempt)

        remaining = deadline - self._clock()
        if remaining <= 0:
            # A synchronous repository call cannot be forcibly cancelled. A
            # late result is discarded here and is never delivered.
            return self._finish(SyncEvent.READ_LATE, key, attempt)

        try:
            snapshot = build_snapshot(facts)
        except (SnapshotUnavailable, SnapshotTooLarge, ValueError):
            return self._finish(SyncEvent.PROJECTION_FAILED, key, attempt)

        remaining = deadline - self._clock()
        if remaining <= 0:
            return self._finish(SyncEvent.DEADLINE_EXPIRED, key, attempt)

        try:
            result = await asyncio.wait_for(
                self._bcs.replace_managers(snapshot, timeout_seconds=remaining),
                timeout=remaining,
            )
        except asyncio.CancelledError:
            self._finish(SyncEvent.CANCELLED, key, attempt)
            raise
        except BcsManagersError as exc:
            if exc.kind is DeliveryFailure.REJECTED:
                return self._finish(SyncEvent.SEND_REJECTED, key, attempt)
            return self._retryable(key, attempt)
        except Exception:
            return self._retryable(key, attempt)

        expected = len(snapshot.manager_user_ids)
        if int(result.total) != expected:
            return self._finish(
                SyncEvent.SEND_DRIFT,
                key,
                attempt,
                outcome=SyncOutcome.DRIFT,
                counts={"expected": expected, "reported": int(result.total)},
            )
        if expected == 0:
            return self._finish(
                SyncEvent.SEND_ACKNOWLEDGED_EMPTY,
                key,
                attempt,
                outcome=SyncOutcome.ACKNOWLEDGED_EMPTY,
            )
        return self._finish(
            SyncEvent.SEND_CONFIRMED,
            key,
            attempt,
            outcome=SyncOutcome.CONFIRMED,
            counts={
                "managers": expected,
                "added": int(result.added),
                "removed": int(result.removed),
            },
        )

    def _retryable(self, key: SyncKey, attempt: int) -> _Retry:
        self._finish(SyncEvent.SEND_RETRYABLE, key, attempt)
        return _RETRY

    def _finish(
        self,
        event: SyncEvent,
        key: SyncKey,
        attempt: int,
        *,
        outcome: SyncOutcome = SyncOutcome.FAILED,
        counts: dict[str, int] | None = None,
    ) -> SyncOutcome:
        self._diagnostics.record(
            event,
            key=key,
            outcome=outcome,
            attempt=attempt,
            counts=counts or {},
        )
        return outcome
