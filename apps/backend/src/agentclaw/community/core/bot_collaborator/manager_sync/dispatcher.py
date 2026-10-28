"""Bounded in-process dispatcher for best-effort manager projections.

Admission is memory-only. Workers run on the lifecycle's dedicated asyncio
loop, merge notifications per ``SyncKey``, and schedule one dirty rerun when a
notification arrives during an active round. No state coordinates replicas.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
    SyncEvent,
    SyncRejection,
)
from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SpaceChange,
    SubmissionResult,
    SyncKey,
    SyncReason,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

logger = get_logger()
_DRAIN_POLL_SECONDS = 0.01


def _space_level_key(change: SpaceChange) -> SyncKey:
    return SyncKey(change.avernet_tenant, change.env, 0)


@dataclass(frozen=True)
class DispatcherPolicy:
    worker_count: int = 4
    max_keys: int = 1024
    page_size: int = 100

    def __post_init__(self) -> None:
        if self.worker_count < 1:
            raise ValueError("worker_count must be positive")
        if self.max_keys < 1:
            raise ValueError("max_keys must be positive")
        if self.page_size < 1:
            raise ValueError("page_size must be positive")


@dataclass
class QueueEntry:
    running: bool = False
    dirty: bool = False


@dataclass(frozen=True)
class DispatcherStats:
    entries: int
    space_hints: int
    running: int
    rejected_space_keys: int
    closed: bool
    attached: bool


class DisabledBotManagerSync:
    """Zero-resource feature-off implementation."""

    def request_sync(
        self,
        *,
        bot_pk: int,
        env: str,
        avernet_tenant: str,
        reason: SyncReason,
    ) -> SubmissionResult:
        return SubmissionResult.DISABLED

    def request_space_sync(self, change: SpaceChange) -> SubmissionResult:
        return SubmissionResult.DISABLED


class BotManagerSyncDispatcher:
    """Merge and execute bounded local manager-sync work."""

    def __init__(
        self,
        service: BotManagerSyncService,
        reader: ManagerSyncReadRepository,
        policy: DispatcherPolicy | None = None,
        *,
        clock: Callable[[], float] | None = None,
        env: str = "",
        diagnostics: SyncDiagnostics | None = None,
    ) -> None:
        self._service = service
        self._reader = reader
        self._policy = policy or DispatcherPolicy()
        # Kept in the frozen constructor for callers that inject a clock.
        self._clock = clock or time.monotonic
        self._env = env
        self._diagnostics = diagnostics or SyncDiagnostics()

        self._mutex = threading.Lock()
        self._entries: dict[SyncKey, QueueEntry] = {}
        self._ready: deque[SyncKey] = deque()
        self._space_hints: deque[SpaceChange] = deque()
        self._expanding_hints = 0
        self._rejected_space_keys = 0
        self._closed = False

        self._loop: asyncio.AbstractEventLoop | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._wake_event: asyncio.Event | None = None
        self._wake_pending = False

    def request_sync(
        self,
        *,
        bot_pk: int,
        env: str,
        avernet_tenant: str,
        reason: SyncReason,
    ) -> SubmissionResult:
        """Admit a key from memory; ``reason`` never changes scheduling."""

        del reason
        key = SyncKey(avernet_tenant, env, bot_pk)
        if self._env and env != self._env:
            self._note_rejected(key, SyncRejection.ENV_MISMATCH, "submission")
            return SubmissionResult.REJECTED
        with self._mutex:
            result = self._admit_key_locked(key)
            rejection = self._rejection_for_locked(result)
        if rejection is not None:
            self._note_rejected(key, rejection, "submission")
        return result

    def request_space_sync(self, change: SpaceChange) -> SubmissionResult:
        """Admit one bounded fan-out hint without reading persistence."""

        key = _space_level_key(change)
        if self._env and change.env != self._env:
            self._note_rejected(key, SyncRejection.ENV_MISMATCH, "space-fanout")
            return SubmissionResult.REJECTED
        with self._mutex:
            if self._closed:
                rejection = SyncRejection.CLOSED
            elif self._capacity_used_locked() >= self._policy.max_keys:
                rejection = SyncRejection.QUEUE_FULL
            else:
                rejection = None
                self._space_hints.append(change)
                self._schedule_wakeup_locked()
        if rejection is not None:
            self._note_rejected(key, rejection, "space-fanout")
            return SubmissionResult.REJECTED
        return SubmissionResult.QUEUED

    async def attach(self) -> None:
        """Attach workers to the current running loop exactly once."""

        if self._loop is not None:
            raise RuntimeError("dispatcher already attached")
        loop = asyncio.get_running_loop()
        with self._mutex:
            self._loop = loop
            self._wake_event = asyncio.Event()
            self._workers = [
                asyncio.create_task(
                    self._worker_loop(), name=f"manager-sync-worker-{index + 1}"
                )
                for index in range(self._policy.worker_count)
            ]
            if self._ready or self._space_hints:
                self._wake_event.set()

    async def drain(self, timeout_seconds: float) -> bool:
        """Wait at most ``timeout_seconds`` for accepted work to finish."""

        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            with self._mutex:
                done = (
                    not self._entries
                    and not self._space_hints
                    and self._expanding_hints == 0
                )
            if done:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(_DRAIN_POLL_SECONDS)

    async def stop(self) -> None:
        """Close admission and cancel workers on their owning loop."""

        with self._mutex:
            attached_loop = self._loop
        if attached_loop is None:
            self.close_admission()
            return
        if asyncio.get_running_loop() is not attached_loop:
            raise RuntimeError(
                "dispatcher.stop() must run on the worker loop that owns it"
            )

        self.close_admission()
        with self._mutex:
            workers, self._workers = self._workers, []
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        with self._mutex:
            self._entries.clear()
            self._ready.clear()
            self._space_hints.clear()
            self._expanding_hints = 0
            self._wake_pending = False
            self._wake_event = None
            self._loop = None

    def close_admission(self) -> None:
        with self._mutex:
            self._closed = True

    @property
    def stats(self) -> DispatcherStats:
        with self._mutex:
            return DispatcherStats(
                entries=len(self._entries),
                space_hints=len(self._space_hints) + self._expanding_hints,
                running=sum(entry.running for entry in self._entries.values()),
                rejected_space_keys=self._rejected_space_keys,
                closed=self._closed,
                attached=self._loop is not None,
            )

    def _admit_key_locked(self, key: SyncKey) -> SubmissionResult:
        # Shutdown rejects every request, including an already-known key.
        if self._closed:
            return SubmissionResult.REJECTED
        entry = self._entries.get(key)
        if entry is not None:
            if entry.running:
                entry.dirty = True
            return SubmissionResult.COALESCED
        if self._capacity_used_locked() >= self._policy.max_keys:
            return SubmissionResult.REJECTED
        self._entries[key] = QueueEntry()
        self._ready.append(key)
        self._schedule_wakeup_locked()
        return SubmissionResult.QUEUED

    def _admit_expanded_key_locked(self, key: SyncKey) -> SubmissionResult:
        """Admit work represented by an already-accepted Space hint."""
        entry = self._entries.get(key)
        if entry is not None:
            if entry.running:
                entry.dirty = True
            return SubmissionResult.COALESCED
        if self._capacity_used_locked() >= self._policy.max_keys:
            return SubmissionResult.REJECTED
        self._entries[key] = QueueEntry()
        self._ready.append(key)
        self._schedule_wakeup_locked()
        return SubmissionResult.QUEUED

    def _rejection_for_locked(self, result: SubmissionResult) -> SyncRejection | None:
        if result is not SubmissionResult.REJECTED:
            return None
        return SyncRejection.CLOSED if self._closed else SyncRejection.QUEUE_FULL

    def _capacity_used_locked(self) -> int:
        return len(self._entries) + len(self._space_hints) + self._expanding_hints

    def _schedule_wakeup_locked(self) -> None:
        loop = self._loop
        event = self._wake_event
        if loop is None or event is None or self._wake_pending:
            return
        self._wake_pending = True
        loop.call_soon_threadsafe(self._deliver_wakeup)

    def _deliver_wakeup(self) -> None:
        with self._mutex:
            self._wake_pending = False
            event = self._wake_event
        if event is not None:
            event.set()

    async def _worker_loop(self) -> None:
        while True:
            work = self._take_work()
            if work is None:
                event = self._wake_event
                if event is None:
                    return
                await event.wait()
                continue
            kind, value = work
            if kind == "key":
                await self._run_key(value)
            else:
                await self._expand_space(value)

    def _take_work(self) -> tuple[str, SyncKey | SpaceChange] | None:
        with self._mutex:
            while self._ready:
                key = self._ready.popleft()
                entry = self._entries.get(key)
                if entry is None or entry.running:
                    continue
                entry.running = True
                return "key", key
            if self._space_hints:
                change = self._space_hints.popleft()
                self._expanding_hints += 1
                return "space", change
            if self._wake_event is not None:
                self._wake_event.clear()
            return None

    async def _run_key(self, key: SyncKey) -> None:
        try:
            with avernet_tenant_scope(key.avernet_tenant):
                await self._service.sync_once(key)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "[MANAGER-SYNC] round_failed tenant=%s env=%s bot_pk=%s",
                key.avernet_tenant,
                key.env,
                key.bot_pk,
            )
        finally:
            with self._mutex:
                entry = self._entries.get(key)
                if entry is not None:
                    if entry.dirty:
                        entry.running = False
                        entry.dirty = False
                        self._ready.append(key)
                        self._schedule_wakeup_locked()
                    else:
                        del self._entries[key]

    async def _expand_space(self, change: SpaceChange) -> None:
        truncated = False
        rejected = 0
        try:
            after_pk = 0
            while True:
                keys = await asyncio.to_thread(
                    self._read_space_page,
                    change,
                    after_pk,
                )
                if not keys:
                    break
                for index, key in enumerate(keys):
                    if (
                        key.avernet_tenant != change.avernet_tenant
                        or key.env != change.env
                    ):
                        raise ValueError("space page returned an out-of-scope key")
                    with self._mutex:
                        result = self._admit_expanded_key_locked(key)
                        if result is SubmissionResult.REJECTED:
                            rejected += len(keys) - index
                            self._rejected_space_keys += len(keys) - index
                            truncated = True
                            break
                if truncated or len(keys) < self._policy.page_size:
                    break
                after_pk = keys[-1].bot_pk
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "[MANAGER-SYNC] space_expansion_failed tenant=%s env=%s",
                change.avernet_tenant,
                change.env,
            )
            self._diagnostics.record(
                SyncEvent.SPACE_EXPANSION_FAILED,
                key=_space_level_key(change),
                outcome="failed",
            )
        finally:
            with self._mutex:
                self._expanding_hints -= 1
                self._schedule_wakeup_locked()

        if truncated:
            self._diagnostics.record(
                SyncEvent.SPACE_EXPANSION_TRUNCATED,
                key=_space_level_key(change),
                outcome=SyncRejection.QUEUE_FULL,
                counts={"rejected_keys": rejected},
            )
            logger.warning(
                "[MANAGER-SYNC] space_expansion_truncated tenant=%s env=%s "
                "rejected_keys=%d",
                change.avernet_tenant,
                change.env,
                rejected,
            )

    def _read_space_page(
        self, change: SpaceChange, after_pk: int
    ) -> tuple[SyncKey, ...]:
        with avernet_tenant_scope(change.avernet_tenant):
            return self._reader.page_space_keys(
                change,
                after_pk=after_pk,
                limit=self._policy.page_size,
            )

    def _note_rejected(self, key: SyncKey, reason: SyncRejection, scope: str) -> None:
        self._diagnostics.record(
            SyncEvent.SUBMIT_REJECTED,
            key=key,
            outcome=reason,
        )
        logger.warning(
            "[MANAGER-SYNC] submit_rejected scope=%s reason=%s tenant=%s "
            "env=%s bot_pk=%s",
            scope,
            reason.value,
            key.avernet_tenant,
            key.env,
            key.bot_pk,
        )


__all__ = [
    "BotManagerSyncDispatcher",
    "DisabledBotManagerSync",
    "DispatcherPolicy",
    "DispatcherStats",
]
