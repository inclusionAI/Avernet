"""BotManagerSyncDispatcher — bounded, nonblocking admission + worker loop.

The ``BotManagerSyncProtocol`` implementation business code calls. Two
entry points, one guarantee: they only touch memory. Submitting a sync
request never reads the database, never opens a network connection, and
never blocks on BCS — a hung service must not delay the caller's
request. Everything slow happens on the workers.

Safety rules this module exists to enforce (each pinned by a test):

- **OWNER_HANDOFF latches as a high-priority flag.** A handoff sets
  ``QueueEntry.owner_check_required``; ordinary notifications can only
  coalesce (``generation`` + 1) and can never clear it. A handoff that
  arrives while a round is running survives the round's completion
  cleanup: the old result — even ``UNKNOWN`` — is never allowed to
  swallow a freshly latched handoff. The next dispatch on that key is
  always the hold, never a roster send.
- **A failed hold never degrades to a send.** ``hold_owner_check``
  returning FAILED keeps the key owner-gated via a sticky latch (every
  later admission of that key is routed to the hold first), retries
  stay inside the bounded defer window, and there is no hot loop — the
  dropped entry is repair's (Task 10) to compensate.
- **DEFERRED is not "someone else finished it".** A claim-BUSY round
  reschedules the key inside its 15s defer budget; the entry stays
  queued until it actually completes or the budget ends.
- **Capacity is total and bounded.** ``max_keys`` counts queued, running
  and deferred entries plus pending space hints, AND owner-hold latched
  keys whose entry is gone — a retained latch still stands for
  owner-gated work, so it keeps consuming its slot (fail-closed at the
  cap; latches are never evicted, and a latched key that also holds an
  entry is counted once). Page expansion stops at the first full queue
  and records the rejected keys for repair.
- **Best-effort, never silent — and never noisy.** A full or closed
  queue may drop a notification — silently dropping it may not: every
  REJECTED admission (queue full / closed / env mismatch) and every
  Space expansion that actually lost keys to the cap leaves one
  diagnostics record plus one WARNING with
  the reason, scope and SyncKey dimensions only, so the loss is
  observable and the repair sweep can be pointed at it. Business
  callers stay non-blocking and never see a raise.
- **One wake, not a flood.** Bursts of submissions from arbitrary
  threads coalesce into a single scheduled wake callback; the mutex is
  held only over plain memory work — never across an ``await``.

The disabled posture ("feature off / unavailable") is a separate,
zero-resource class — :class:`DisabledBotManagerSync` — so the default
binding holds no threads and clients at all.
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
    SyncOutcome,
    SyncReason,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

logger = get_logger()

#: Total scheduling budget one key may spend inside the defer window of
#: its first pop — DEFERRED rounds and failed holds alike. Once the
#: budget ends the entry is dropped for repair (Task 10) to compensate,
#: never spun forever.
DEFER_WINDOW_SECONDS = 15.0

_DEFER_BASE_DELAY_SECONDS = 1.0
_DRAIN_POLL_SECONDS = 0.01


def _space_level_key(change: SpaceChange) -> SyncKey:
    """Diagnostic identity for a Space-level (multi-Bot) event.

    Carries the change's tenant/env dimensions with the fixed sentinel
    ``bot_pk=0`` — a static "no single Bot" marker, so space-scope
    records add no new cardinality dimension.
    """
    return SyncKey(avernet_tenant=change.avernet_tenant, env=change.env, bot_pk=0)


@dataclass(frozen=True)
class DispatcherPolicy:
    """Fixed design limits of the dispatcher (separate from SyncRunPolicy)."""

    worker_count: int = 4
    max_keys: int = 1024
    page_size: int = 100


@dataclass
class QueueEntry:
    """One key's queue state, mutated only under the dispatcher mutex.

    ``generation`` counts notifications since the entry last reset (a
    notification on an active key bumps it instead of enqueuing twice).
    ``running`` marks an in-flight worker round — running entries keep
    occupying capacity. ``owner_check_required`` is the latched
    OWNER_HANDOFF priority flag: set on the handoff reason, cleared only
    by a *successful* hold. ``defer_*`` implement the bounded DEFERRED /
    failed-hold reschedule window on the injected clock.
    """

    generation: int
    running: bool
    owner_check_required: bool
    deferred_until: float = 0.0
    defer_deadline: float = 0.0
    defer_index: int = 0


@dataclass(frozen=True)
class DispatcherStats:
    """Read-only introspection for tests and maintenance diagnostics."""

    entries: int
    space_hints: int
    running: int
    latched_owner_keys: int
    rejected_space_keys: int
    closed: bool
    attached: bool


class DisabledBotManagerSync:
    """The default (feature-off / unavailable) ``BotManagerSyncProtocol``.

    Returns DISABLED for both entry points and deliberately holds no
    network or thread resources: no loop, no workers, no clients — the
    process that never enables the feature pays nothing for it.
    """

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
    """Bounded memory admission and the OWNER-first background workers."""

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
        self._clock = clock or time.monotonic
        #: Rejection / truncation observability sink (memory-only).
        self._diagnostics = (
            diagnostics if diagnostics is not None else SyncDiagnostics()
        )
        #: The deployment env this syncer serves; empty disables the
        #: gate (unit tests construct without one). Requests for another
        #: env are refused before any send: the reader would refuse them
        #: later anyway, and a deployment must not pretend to serve an
        #: environment it was not configured for.
        self._env = env
        # -- all state below is guarded by ``_mutex`` -----------------
        self._mutex = threading.Lock()
        self._entries: dict[SyncKey, QueueEntry] = {}
        self._ready: deque[SyncKey] = deque()
        self._space_hints: deque[SpaceChange] = deque()
        #: Hints a worker has popped but whose per-Bot keys are not yet
        #: admitted — the window between hint-pop and admission that
        #: ``drain`` below must treat as "not done", or a wait could
        #: silently drop queued space-fanout work.
        self._expanding_hints = 0
        self._latched_owner_keys: set[SyncKey] = set()
        self._rejected_space_keys = 0
        self._closed = False
        # -- loop-owned (created on attach, torn down on stop) --------
        self._loop: asyncio.AbstractEventLoop | None = None
        self._workers: list[asyncio.Task] = []
        self._wake_event: asyncio.Event | None = None
        self._wake_pending = False
        self._defer_timers: set[asyncio.TimerHandle] = set()

    # -- BotManagerSyncProtocol ------------------------------------------

    def request_sync(
        self,
        *,
        bot_pk: int,
        env: str,
        avernet_tenant: str,
        reason: SyncReason,
    ) -> SubmissionResult:
        """Memory-only admission: never reads, never blocks, never sends."""
        key = SyncKey(avernet_tenant=avernet_tenant, env=env, bot_pk=bot_pk)
        if self._env and key.env != self._env:
            self._note_rejected(key, SyncRejection.ENV_MISMATCH, scope="submission")
            return SubmissionResult.REJECTED
        with self._mutex:
            result = self._admit_locked(key, reason)
            if result is SubmissionResult.REJECTED:
                rejected_because = (
                    SyncRejection.CLOSED if self._closed else SyncRejection.QUEUE_FULL
                )
        if result is SubmissionResult.REJECTED:
            self._note_rejected(key, rejected_because, scope="submission")
        return result

    def request_space_sync(self, change: SpaceChange) -> SubmissionResult:
        """Admit one Space fan-out hint; the workers expand it later.

        The hint occupies one capacity slot until a worker pages it into
        per-Bot keys — expansion never happens on a request thread, so
        a space-wide change cannot put a DB read on the caller either.
        """
        if self._env and change.env != self._env:
            self._note_rejected(
                _space_level_key(change), SyncRejection.ENV_MISMATCH,
                scope="space-fanout",
            )
            return SubmissionResult.REJECTED
        with self._mutex:
            if self._closed:
                rejected_because = SyncRejection.CLOSED
            elif self._capacity_used_locked() >= self._policy.max_keys:
                rejected_because = SyncRejection.QUEUE_FULL
            else:
                rejected_because = None
                self._space_hints.append(change)
                self._schedule_single_wakeup_locked()
        if rejected_because is not None:
            self._note_rejected(
                _space_level_key(change), rejected_because, scope="space-fanout"
            )
            return SubmissionResult.REJECTED
        return SubmissionResult.QUEUED

    # -- lifecycle surface (owned by the lifecycle) ---------------------

    async def attach(self) -> None:
        """Bind to the *current running* loop and start the workers.

        Called once, on the loop that will own every AsyncClient the
        delivery plugin may lazily create; a second attach is a wiring
        bug and raises instead of silently double-starting.
        """
        if self._loop is not None:
            raise RuntimeError("dispatcher already attached")
        loop = asyncio.get_running_loop()
        with self._mutex:
            self._loop = loop
            self._wake_event = asyncio.Event()
            self._workers = [
                asyncio.create_task(
                    self._worker_loop(), name=f"manager-sync-worker-{i + 1}"
                )
                for i in range(self._policy.worker_count)
            ]
            if self._entries or self._space_hints:
                # Admission accepted a bounded queue before startup —
                # the workers inherit it now.
                self._schedule_single_wakeup_locked()

    async def drain(self, timeout_seconds: float) -> bool:
        """Bounded wait until the queue and all running rounds finish.

        "Done" counts every unit of work the dispatcher has taken on:
        queued entries and running rounds, pending space hints, AND
        hints currently mid-expansion (popped from the deque while their
        per-Bot keys are still being paged into the queue). Without that
        last count, the hint-pop window would read as empty and a
        maintenance/shutdown wait could return "done" while queued
        space-fanout work silently drops.

        Maintenance/tests only — business paths must never block on a
        sync they submitted. Uses no event-loop primitives beyond a small
        poll sleep, so it is safe from a loop other than the workers'.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            with self._mutex:
                empty = (
                    not self._entries
                    and not self._space_hints
                    and not self._expanding_hints
                )
            if empty:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(_DRAIN_POLL_SECONDS)

    async def stop(self) -> None:
        """Cancel the workers and disarm the timers — on the worker loop.

        Worker-owned resources (wake event, timers, tasks) are bound to
        one loop; stopping from any other loop is refused rather than
        corrupting loop state. In-flight rounds receive their
        cancellation inside the service, which keeps every unknown
        marker exactly as it was — closing this dispatcher can never
        turn an unknown delivery into a confirmed one.
        """
        with self._mutex:
            attached_loop = self._loop
        if attached_loop is None:
            return  # never attached (or already stopped): clean no-op
        if asyncio.get_running_loop() is not attached_loop:
            raise RuntimeError(
                "dispatcher.stop() must run on the worker loop that owns it"
            )
        with self._mutex:
            workers, self._workers = self._workers, []
            timers = list(self._defer_timers)
            self._defer_timers.clear()
            self._wake_event = None
        for handle in timers:
            handle.cancel()
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        with self._mutex:
            self._loop = None
            self._wake_pending = False

    def wake(self) -> None:
        """Maintenance/test hook: schedule one (coalesced) worker wake.

        Production code never needs this — admissions and timers do it.
        Tests use it to advance deferred entries deterministically with
        an injected clock instead of waiting real seconds.
        """
        with self._mutex:
            self._schedule_single_wakeup_locked()

    def close_admission(self) -> None:
        """Stop accepting submissions; queued work keeps draining."""
        with self._mutex:
            self._closed = True

    @property
    def stats(self) -> DispatcherStats:
        """Queue introspection (tests/diagnostics only)."""
        with self._mutex:
            running = sum(1 for e in self._entries.values() if e.running)
            return DispatcherStats(
                entries=len(self._entries),
                space_hints=len(self._space_hints),
                running=running,
                latched_owner_keys=len(self._latched_owner_keys),
                rejected_space_keys=self._rejected_space_keys,
                closed=self._closed,
                attached=self._loop is not None,
            )

    # -- admission helpers (mutex held) -----------------------------------

    def _capacity_used_locked(self) -> int:
        """Total capacity in use: entries (running and deferred included),
        pending space hints, PLUS owner-hold latched keys whose entry is
        gone — a retained latch still stands for owner-gated work, so it
        keeps consuming a slot (fail-closed at ``max_keys``; the latch
        itself is never evicted). A latched key that also currently
        holds an entry is counted exactly once, via the entry."""
        latched_without_entry = len(self._latched_owner_keys - self._entries.keys())
        return len(self._entries) + len(self._space_hints) + latched_without_entry

    def _admit_locked(self, key: SyncKey, reason: SyncReason) -> SubmissionResult:
        previous = self._entries.get(key)
        if previous is not None:
            # An active key only re-notifies: bump the generation and OR
            # in the owner flag — one entry per key, latched or not.
            previous.generation += 1
            previous.owner_check_required |= (
                reason is SyncReason.OWNER_HANDOFF or key in self._latched_owner_keys
            )
            return SubmissionResult.COALESCED
        if self._closed or self._capacity_used_locked() >= self._policy.max_keys:
            return SubmissionResult.REJECTED
        self._entries[key] = QueueEntry(
            generation=1,
            running=False,
            owner_check_required=(
                reason is SyncReason.OWNER_HANDOFF or key in self._latched_owner_keys
            ),
        )
        self._ready.append(key)
        self._schedule_single_wakeup_locked()
        return SubmissionResult.QUEUED

    # -- rejection observability (caller-visible results unchanged) ------

    def _note_rejected(
        self, key: SyncKey, rejection: SyncRejection, *, scope: str
    ) -> None:
        """One diagnostics record + one WARNING per refused submission.

        Dropping a notification best-effort is allowed; dropping it
        silently is not. Pure memory: no flush, no external call, and
        the lines carry only the reason, scope, SyncKey dimensions and
        integer counts — never a payload or manager list. Business
        callers keep getting their plain ``REJECTED`` without a raise.
        """
        self._diagnostics.record(
            SyncEvent.SUBMIT_REJECTED,
            key=key,
            outcome=rejection,
            counts={"rejected": 1},
        )
        logger.warning(
            "[MANAGER-SYNC-DISPATCH] submit_rejected reason=%s scope=%s "
            "deployment_env=%s tenant=%s env=%s bot_pk=%d — submission "
            "dropped; run a manager-sync repair sweep when the queue drains",
            rejection.value,
            scope,
            self._env,
            key.avernet_tenant,
            key.env,
            key.bot_pk,
        )

    def _note_truncated(self, change: SpaceChange, admitted: int) -> None:
        """A hint's expansion stopped at capacity mid-way: one record +
        one WARNING (counts only — remaining keys are never emitted).

        ``admitted`` counts genuinely-new admissions only — coalesced
        re-notifications of keys already in the queue are excluded."""
        self._diagnostics.record(
            SyncEvent.SPACE_EXPANSION_TRUNCATED,
            key=_space_level_key(change),
            outcome=SyncRejection.QUEUE_FULL,
            counts={
                "spaces": len(change.space_refs),
                "admitted": admitted,
                "truncated": 1,
            },
        )
        logger.warning(
            "[MANAGER-SYNC-DISPATCH] space_expansion_truncated "
            "reason=queue_full scope=space-fanout tenant=%s env=%s spaces=%d "
            "admitted=%d — paging stopped at capacity, the unexpanded "
            "remainder is repair's; run a repair sweep when the queue drains",
            change.avernet_tenant,
            change.env,
            len(change.space_refs),
            admitted,
        )

    def _schedule_single_wakeup_locked(self) -> None:
        """Arm exactly one wake callback; a burst costs one schedule."""
        if self._wake_pending or self._loop is None or self._wake_event is None:
            return
        self._wake_pending = True
        loop = self._loop
        try:
            loop.call_soon_threadsafe(self._fire_wake)
        except RuntimeError:
            # The loop closed between the check and the schedule — the
            # next attach re-arms from the queue it inherits.
            self._wake_pending = False

    def _fire_wake(self) -> None:
        """On the worker loop: release one pending wake into the event."""
        event = self._wake_event
        if event is None:
            return
        with self._mutex:
            self._wake_pending = False
        event.set()

    def _pop_unit_locked(self):
        """Next unit of work, or None: ready keys first, then one hint."""
        scan = len(self._ready)
        now = self._clock()
        while scan > 0:
            scan -= 1
            key = self._ready.popleft()
            entry = self._entries.get(key)
            if entry is None or entry.running:
                continue  # stale ready marker, or already running
            if entry.deferred_until > now:
                self._ready.append(key)  # deferred: rotate to the back
                continue
            entry.running = True
            return ("bot", key, entry, entry.owner_check_required,
                    entry.generation)
        if self._space_hints:
            hint = self._space_hints.popleft()
            # The hint leaves the deque but its work does not exist in
            # the queue yet: count the expansion so ``drain`` cannot
            # answer "done" before this hint's keys are admitted.
            self._expanding_hints += 1
            return ("space", hint)
        return None

    # -- completion helpers (mutex held) -----------------------------------

    def _requeue_locked(self, key: SyncKey) -> None:
        """Ready the entry for its next dispatch and wake one worker."""
        self._ready.append(key)
        self._schedule_single_wakeup_locked()

    def _defer_locked(self, key: SyncKey, entry: QueueEntry) -> None:
        """Bounded reschedule (DEFERRED rounds / failed holds inside the
        defer window: 1s/2s/4s/… capped by the remaining budget)."""
        now = self._clock()
        if entry.defer_deadline == 0.0:
            entry.defer_deadline = now + DEFER_WINDOW_SECONDS
        if now >= entry.defer_deadline:
            del self._entries[key]
            logger.warning(
                "[MANAGER-SYNC-DISPATCH] defer_window_expired tenant=%s "
                "env=%s bot_pk=%s — entry dropped, repair compensates later",
                key.avernet_tenant,
                key.env,
                key.bot_pk,
            )
            return
        delay = min(
            _DEFER_BASE_DELAY_SECONDS * (2 ** entry.defer_index),
            entry.defer_deadline - now,
        )
        entry.defer_index += 1
        entry.deferred_until = now + delay
        self._ready.append(key)
        self._arm_defer_timer_locked(delay)

    def _arm_defer_timer_locked(self, delay: float) -> None:
        """One wake when the deferred entry becomes due (bounded timers)."""
        loop = self._loop
        if loop is None:
            return
        holder: list[asyncio.TimerHandle] = []

        def _fire() -> None:
            with self._mutex:
                if holder:
                    self._defer_timers.discard(holder[0])
                self._schedule_single_wakeup_locked()

        try:
            handle = loop.call_later(max(0.0, delay), _fire)
        except RuntimeError:
            return  # loop stopping: admission/timers re-arm after restart
        holder.append(handle)
        self._defer_timers.add(handle)

    # -- worker loop ---------------------------------------------------------

    async def _worker_loop(self) -> None:
        while True:
            with self._mutex:
                unit = self._pop_unit_locked()
            event = self._wake_event
            if unit is None:
                if event is None:
                    return  # torn down: nothing owns this worker
                await event.wait()
                event.clear()
                continue
            if unit[0] == "bot":
                _, key, entry, owner_flag, run_generation = unit
                await self._process_bot(key, entry, owner_flag, run_generation)
            else:
                await self._process_space(unit[1])

    async def _process_bot(
        self,
        key: SyncKey,
        entry: QueueEntry,
        owner_flag: bool,
        run_generation: int,
    ) -> None:
        """One dispatch for ``key`` — the mutex is never held across the
        service await, and completion re-compares state under the mutex.

        The OWNER flag latched at pop time decides *which* service call
        runs; the completion below is what makes a handoff that arrives
        mid-flight survive an ``UNKNOWN`` or any other stale result.
        """
        outcome: SyncOutcome
        try:
            with avernet_tenant_scope(key.avernet_tenant):
                if owner_flag:
                    outcome = await self._service.hold_owner_check(key)
                else:
                    outcome = await self._service.sync_once(key)
        except asyncio.CancelledError:
            # Cancellation is shutdown only: the service keeps every
            # unknown marker; this entry stays as-is for the post-mortem.
            raise
        except Exception:
            # A contract violation inside the service must keep the
            # workers alive — it is diagnosed and treated as FAILED.
            logger.error(
                "[MANAGER-SYNC-DISPATCH] dispatch_crashed tenant=%s env=%s "
                "bot_pk=%s owner_check=%s",
                key.avernet_tenant,
                key.env,
                key.bot_pk,
                int(owner_flag),
            )
            outcome = SyncOutcome.FAILED
        with self._mutex:
            if self._entries.get(key) is not entry:
                return  # entry resolved elsewhere (never expected): nothing to do
            entry.running = False
            if owner_flag:
                self._complete_hold_locked(key, entry, outcome, run_generation)
            else:
                self._complete_round_locked(key, entry, outcome, run_generation)

    def _complete_hold_locked(
        self,
        key: SyncKey,
        entry: QueueEntry,
        outcome: SyncOutcome,
        run_generation: int,
    ) -> None:
        """Completion of an owner-hold dispatch (priority semantics)."""
        if outcome is SyncOutcome.BLOCKED:
            if entry.generation > run_generation:
                # Conservative branch: a newer notification — possibly
                # another handoff — coalesced while this hold was in
                # flight. The requeued dispatch must re-run the hold
                # first (planting the marker again is idempotent), so
                # neither the flag nor the sticky latch is cleared here.
                # Safety must not depend on the coordination layer
                # refusing a roster send this side chose to attempt.
                self._requeue_locked(key)
                return
            # Generation stable: the shared owner-hold marker now guards
            # the key, the flag has done its job, and senders stay
            # blocked at claim time until ops (Task 10) clears it.
            self._latched_owner_keys.discard(key)
            entry.owner_check_required = False
            del self._entries[key]
            return
        # FAILED (or any non-BLOCKED answer): the hold did not stand.
        # Never clear the flag, never fall back to a roster send — latch
        # the key so every later admission routes to the hold first, and
        # retry inside the bounded defer window only.
        self._latched_owner_keys.add(key)
        entry.owner_check_required = True
        self._defer_locked(key, entry)

    def _complete_round_locked(
        self,
        key: SyncKey,
        entry: QueueEntry,
        outcome: SyncOutcome,
        run_generation: int,
    ) -> None:
        """Completion of an ordinary sync round.

        Owner flag first, then the generation. BLOCKED and UNKNOWN are
        treated as terminal for this entry (no hot loop; the unknown
        marker keeps blocking at claim time) — unless a handoff or a
        newer notification arrived during the run, in which case the
        entry requeues instead of being cleaned away by stale data.
        """
        if outcome is SyncOutcome.DEFERRED:
            if entry.owner_check_required:
                # A handoff outranks the deferred round: hold first.
                self._requeue_locked(key)
                return
            self._defer_locked(key, entry)
            return
        if entry.owner_check_required:
            self._requeue_locked(key)
            return
        if entry.generation > run_generation:
            self._requeue_locked(key)
            return
        del self._entries[key]

    # -- space hints --------------------------------------------------------

    async def _process_space(self, change: SpaceChange) -> None:
        """Expand one hint into per-Bot entries, paging until the first
        full queue — the rejected remainder is counted for repair.

        Runs with the in-flight-expansion count held (incremented at
        pop): whatever this loop does — full queue, empty page, reader
        failure, worker shutdown cancellation — the finally below
        releases the count only once the hint's fate is decided, so
        ``drain`` never sees a "done" that ignores it.
        """
        try:
            cursor = 0
            admitted_this_hint = 0
            dropped_this_hint = 0
            truncated = False
            while True:
                with self._mutex:
                    capacity_left = self._policy.max_keys - self._capacity_used_locked()
                if capacity_left <= 0:
                    # Full before this page: stop paging, never cache the
                    # rest. Repair (Task 10) compensates, this hint is done.
                    truncated = True
                    return
                try:
                    with avernet_tenant_scope(change.avernet_tenant):
                        page = await asyncio.to_thread(
                            self._reader.page_space_keys,
                            change,
                            after_pk=cursor,
                            limit=self._policy.page_size,
                        )
                except Exception:
                    logger.error(
                        "[MANAGER-SYNC-DISPATCH] space_expansion_failed tenant=%s "
                        "env=%s space_refs=%s — hint dropped for repair",
                        change.avernet_tenant,
                        change.env,
                        len(change.space_refs),
                    )
                    return
                if not page:
                    return
                with self._mutex:
                    for expanded in page:
                        result = self._admit_locked(
                            expanded, SyncReason.SPACE_MEMBERSHIP
                        )
                        if result is SubmissionResult.REJECTED:
                            # Coalesced keys are already queued — only genuine
                            # over-capacity drops count for repair.
                            self._rejected_space_keys += 1
                            dropped_this_hint += 1
                        elif result is SubmissionResult.QUEUED:
                            admitted_this_hint += 1
                with self._mutex:
                    full_now = self._capacity_used_locked() >= self._policy.max_keys
                if full_now:
                    # An exact fill is a completed fan-out, not a
                    # truncation: only a page that actually lost keys to
                    # the cap leaves the remainder-unaccounted signal —
                    # full-with-zero-drops means every key read so far
                    # sits in the queue (no drop was proven lost).
                    if dropped_this_hint:
                        truncated = True
                    return  # met a full queue: expansion stops at capacity
                cursor = page[-1].bot_pk
        finally:
            # Every exit path — capacity-stop, empty page, reader
            # failure, stop() cancellation — releases the pop count.
            with self._mutex:
                self._expanding_hints -= 1
            if truncated:
                self._note_truncated(change, admitted_this_hint)
