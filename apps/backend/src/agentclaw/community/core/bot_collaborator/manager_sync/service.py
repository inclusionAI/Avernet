"""BotManagerSyncService — one sync round: claim, project, deliver, confirm.

Orchestrates one Bot's manager-set sync between the read repository, the
projector, the BCS delivery plugin and the shared coordinator:

    claim lease → read facts (in a thread) → build_snapshot → begin
    attempt → replace_managers(timeout=remaining) → verify total →
    finish attempt → release lease

Safety-critical rules this module exists to enforce (each pinned by a
test):

- **Never replay an uncertain write.** ``begin`` returns uncertain
  (infrastructure cannot answer) ⇒ no send, report ``UNKNOWN``, keep
  the possibly-written marker; the *next* ``sync_once`` on that key
  finds the marker at claim time and is BLOCKED. A
  ``DeliveryFailure.UNKNOWN`` from the PUT does the same, without
  calling ``finish``.
- **After the PUT was invoked, ANY cancellation keeps the marker.** The
  round swallows the cancellation there (and only there, or during a
  ``begin``/``finish`` thread call already dispatched) and returns
  ``UNKNOWN`` — the delivery state is genuinely unknown. Cancellation
  before any marker may exist propagates as ``CancelledError``.
- **Late reads are dropped.** A cancelled/threaded read cannot stop the
  underlying SQL, so the result is discarded unless the remaining
  budget can still carry a send.
- **A slow ``begin`` stops the round, not the truth.** ``begin``
  answering only after the round deadline is confirmed-not-sent: the
  attempt is finished and the round is FAILED — never a PUT on a stale
  budget, and never a blind clear of a dispatched-but-unanswered
  ``begin`` (that result stays UNKNOWN with its marker kept).
- **claim BUSY is the only DEFERRED.** Any other infrastructure
  ambiguity reports FAILED, never BUSY, and never sends.
- **NOT_SENT is the only retryable delivery failure.** The attempt is
  finished (marker cleared) first; the round may then re-read and send
  again with a NEW attempt id, inside the remaining budget and at most
  ``max_attempts`` sends in total.
- **Lease hygiene.** The lease is renewed only when its remaining
  window is smaller than the remaining budget + 2s — strictly inside
  the deadline; a failed renewal stops the round before any send. The
  default 15s budget / 30s lease therefore performs zero renewals. The
  outer ``finally`` releases exactly this holder's lease and never
  touches a marker.

Thread discipline: the coordinator and the read repository are
synchronous, so every call runs through ``asyncio.to_thread``; no DB
session is ever held across the async HTTP call.
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
    SyncEvent,
)
from agentclaw.community.core.bot_collaborator.manager_sync.projector import (
    build_snapshot,
)
from agentclaw.community.core.bot_collaborator.manager_sync.retry import (
    MIN_SEND_BUDGET_SECONDS,
    RENEW_MARGIN_SECONDS,
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
from agentclaw.community.plugin_api.manager_sync_coordination import (
    Attempt,
    ClaimResult,
    ClaimStatus,
    CoordinationLost,
    CoordinationUnavailable,
    Lease,
    ManagerSyncCoordinator,
)

#: Async wait signature used by every backoff sleep (injectable).
Wait = Callable[[float], Awaitable[None]]


@dataclass
class _LeaseWindow:
    """The round's lease plus when its TTL window last started."""
    lease: Lease
    started: float

    def remaining(self, now: float, policy: SyncRunPolicy) -> float:
        return policy.lease_seconds - (now - self.started)


def mint_holder() -> str:
    """Per-instance holder: ``hostname:pid:uuid4[:8]``.

    The lease value IS the holder string passed to ``claim`` / ``renew``
    / ``release``: two processes sharing a holder can silently renew or
    release each other's lease. The 8-hex suffix makes every service
    instance in one process unique, so this can never collide across
    instances, processes or hosts.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class BotManagerSyncService:
    """Drive one Bot's manager set to BCS inside one bounded round."""

    def __init__(
        self,
        reader: ManagerSyncReadRepository,
        bcs: BcsManagersPlugin,
        coordinator: ManagerSyncCoordinator,
        *,
        policy: SyncRunPolicy | None = None,
        clock: Callable[[], float] | None = None,
        jitter: Callable[[float], float] | None = None,
        wait: Wait | None = None,
        diagnostics: SyncDiagnostics | None = None,
    ) -> None:
        self._reader = reader
        self._bcs = bcs
        self._coordinator = coordinator
        self._policy = policy or SyncRunPolicy()
        self._clock = clock or time.monotonic
        self._jitter = jitter or default_jitter
        self._wait: Wait = wait or asyncio.sleep
        self._diagnostics = diagnostics or SyncDiagnostics()
        # One holder per service instance (see mint_holder); a process
        # running one instance stays stable across its rounds.
        self._holder = mint_holder()

    # -- public entry points ------------------------------------------

    async def sync_once(self, key: SyncKey) -> SyncOutcome:
        """Run one bounded sync round for ``key`` (see module docs)."""
        window: _LeaseWindow | None = None
        try:
            claim = await self._claim(key)
            if isinstance(claim, SyncOutcome):
                return claim
            window = _LeaseWindow(lease=claim, started=self._clock())
            return await self._run_attempts(key, window)
        finally:
            # Release exactly this holder's lease; never a marker.
            if window is not None:
                await self._release_quietly(key, window.lease)

    async def hold_owner_check(self, key: SyncKey) -> SyncOutcome:
        """Set the owner-hold marker for ``key`` — never any HTTP.

        For the dispatcher's OWNER_HANDOFF high-priority reason: the
        marker stops senders at claim time while the Owner completes a
        live check. Marker set → BLOCKED; infrastructure failure →
        FAILED with a WARNING (the coordination layer could not give a
        trustworthy "stop" answer, so the caller must not treat it as
        held).
        """
        try:
            await asyncio.to_thread(self._coordinator.hold_owner_check, key)
        except CoordinationUnavailable:
            self._record(SyncEvent.HOLD_FAILED, key=key, outcome=SyncOutcome.FAILED)
            return SyncOutcome.FAILED
        self._record(SyncEvent.HOLD_SET, key=key, outcome=SyncOutcome.BLOCKED)
        return SyncOutcome.BLOCKED

    # -- claim ---------------------------------------------------------

    async def _claim(self, key: SyncKey) -> Lease | SyncOutcome:
        """Claim the lease; a ``SyncOutcome`` result means "round over"."""
        try:
            claim: ClaimResult = await asyncio.to_thread(
                self._coordinator.claim,
                key,
                holder=self._holder,
                ttl_seconds=self._policy.lease_seconds,
            )
        except CoordinationUnavailable:
            # Infra ambiguity is FAILED here — never a fake BUSY.
            self._record(SyncEvent.CLAIM_FAILED, key=key, outcome=SyncOutcome.FAILED)
            return SyncOutcome.FAILED
        if claim.status is ClaimStatus.BUSY:
            self._record(SyncEvent.CLAIM_BUSY, key=key, outcome=SyncOutcome.DEFERRED)
            return SyncOutcome.DEFERRED
        if claim.status is not ClaimStatus.ACQUIRED or claim.lease is None:
            # BLOCKED: an in-flight or owner-hold marker says stop. Only
            # ACQUIRED carries a lease; anything else never sends.
            self._record(
                SyncEvent.CLAIM_BLOCKED, key=key, outcome=SyncOutcome.BLOCKED
            )
            return SyncOutcome.BLOCKED
        return claim.lease

    # -- attempt loop --------------------------------------------------

    async def _run_attempts(self, key: SyncKey, window: _LeaseWindow) -> SyncOutcome:
        """Bounded send loop: NOT_SENT may retry with a new attempt id."""
        deadline = window.started + self._policy.budget_seconds
        attempt = 0
        while attempt < self._policy.max_attempts:
            if attempt > 0:
                remaining = deadline - self._clock()
                delay = backoff_delay(
                    retry_index=attempt,
                    remaining_seconds=remaining,
                    jitter=self._jitter,
                )
                if delay is None:
                    self._record(
                        SyncEvent.DEADLINE_EXPIRED,
                        key=key,
                        outcome=SyncOutcome.FAILED,
                        attempt=attempt,
                        counts={"budget_remaining_ms": max(0, int(remaining * 1000))},
                    )
                    return SyncOutcome.FAILED
                await self._wait(delay)
            attempt += 1
            outcome = await self._send_once(key, window, deadline, attempt)
            if not isinstance(outcome, _Resend):
                return outcome
        self._record(
            SyncEvent.RETRIES_EXHAUSTED,
            key=key,
            outcome=SyncOutcome.FAILED,
            attempt=attempt,
            counts={"attempts_used": attempt},
        )
        return SyncOutcome.FAILED

    # -- one send attempt -----------------------------------------------

    async def _send_once(
        self, key: SyncKey, window: _LeaseWindow, deadline: float, attempt_no: int
    ) -> SyncOutcome | _Resend:
        """Read → project → lease guard → begin → PUT → verify/finish.

        Returns a ``SyncOutcome`` for the whole round, or the
        ``_Resend`` sentinel when a NOT_SENT attempt may be retried.
        """
        try:
            facts = await asyncio.to_thread(self._reader.read_facts, key)
        except asyncio.CancelledError:
            # No attempt exists yet (begin not invoked); a clean raise
            # also releases the lease in sync_once's finally.
            raise
        except SnapshotUnavailable:
            self._record(
                SyncEvent.READ_FAILED, key=key, outcome=SyncOutcome.FAILED,
                attempt=attempt_no,
            )
            return SyncOutcome.FAILED
        except Exception:
            self._record(
                SyncEvent.READ_FAILED, key=key, outcome=SyncOutcome.FAILED,
                attempt=attempt_no,
            )
            return SyncOutcome.FAILED

        # The underlying SQL cannot be cancelled — drop a late result:
        # no attempt, no send, once the budget can no longer carry it.
        now = self._clock()
        remaining = deadline - now
        if remaining < MIN_SEND_BUDGET_SECONDS:
            self._record(
                SyncEvent.READ_LATE,
                key=key,
                outcome=SyncOutcome.FAILED,
                attempt=attempt_no,
                counts={"budget_remaining_ms": max(0, int(remaining * 1000))},
            )
            return SyncOutcome.FAILED

        try:
            snapshot = build_snapshot(facts)
        except (SnapshotUnavailable, SnapshotTooLarge, ValueError):
            # Facts cannot be projected (identity / role / cap): never a
            # reason to talk to BCS; caller retries get fresh facts.
            self._record(
                SyncEvent.PROJECTION_FAILED,
                key=key,
                outcome=SyncOutcome.FAILED,
                attempt=attempt_no,
            )
            return SyncOutcome.FAILED

        if window.remaining(now, self._policy) < remaining + RENEW_MARGIN_SECONDS:
            # Strictly inside the deadline (remaining >= MIN_SEND_BUDGET
            # > 0 already). Renewal failure = the lease is no longer
            # provably ours => no send this round.
            try:
                renewed = await asyncio.to_thread(
                    self._coordinator.renew,
                    window.lease,
                    ttl_seconds=self._policy.lease_seconds,
                )
            except CoordinationUnavailable:
                self._record(
                    SyncEvent.LEASE_RENEW_FAILED,
                    key=key,
                    outcome=SyncOutcome.FAILED,
                    attempt=attempt_no,
                )
                return SyncOutcome.FAILED
            if not renewed:
                self._record(
                    SyncEvent.LEASE_RENEW_FAILED,
                    key=key,
                    outcome=SyncOutcome.FAILED,
                    attempt=attempt_no,
                )
                return SyncOutcome.FAILED
            window.started = self._clock()

        try:
            attempt = await asyncio.to_thread(
                self._coordinator.begin,
                window.lease,
                attempt_id=uuid.uuid4().hex[:12],
                digest=snapshot.digest,
            )
        except asyncio.CancelledError:
            # The thread was already dispatched: the marker may exist.
            self._record(
                SyncEvent.CANCELLED,
                key=key,
                outcome=SyncOutcome.UNKNOWN,
                attempt=attempt_no,
            )
            return SyncOutcome.UNKNOWN
        except CoordinationLost:
            # The coordinator answered: the lease is gone or another
            # marker exists — never send over someone else's state.
            self._record(
                SyncEvent.BEGIN_LOST, key=key, outcome=SyncOutcome.BLOCKED,
                attempt=attempt_no,
            )
            return SyncOutcome.BLOCKED
        except CoordinationUnavailable:
            # Begin-uncertain: a marker may or may not have been
            # written; never send, report UNKNOWN so the next claim on
            # this key is strictly BLOCKED by the marker check.
            self._record(
                SyncEvent.BEGIN_UNCERTAIN,
                key=key,
                outcome=SyncOutcome.UNKNOWN,
                attempt=attempt_no,
            )
            return SyncOutcome.UNKNOWN

        # begin answered, but it may have consumed the budget: recompute
        # the remaining time from a fresh clock read before any PUT (the
        # lease guard above already covers this smaller remaining).
        now = self._clock()
        remaining = deadline - now
        if remaining < MIN_SEND_BUDGET_SECONDS:
            # Confirmed-not-sent: begin returned and no PUT was ever
            # invoked, so the marker is ours to finish here — an
            # *unanswered* dispatched begin keeps its marker instead.
            confirmed = await self._finish_confirmed(key, attempt, attempt_no)
            if confirmed is None:
                return SyncOutcome.UNKNOWN  # cancelled during finish
            if not confirmed:
                return SyncOutcome.BLOCKED  # marker is not ours to clear
            self._record(
                SyncEvent.DEADLINE_EXPIRED,
                key=key,
                outcome=SyncOutcome.FAILED,
                attempt=attempt_no,
                counts={"budget_remaining_ms": max(0, int(remaining * 1000))},
            )
            return SyncOutcome.FAILED

        try:
            result = await self._bcs.replace_managers(
                snapshot, timeout_seconds=remaining
            )
        except asyncio.CancelledError:
            # The PUT was invoked — the delivery state is unknown. Keep
            # the marker (no finish) and report UNKNOWN; never replay.
            self._record(
                SyncEvent.SEND_CANCELLED,
                key=key,
                outcome=SyncOutcome.UNKNOWN,
                attempt=attempt_no,
                counts={"managers": len(snapshot.manager_user_ids)},
            )
            return SyncOutcome.UNKNOWN
        except BcsManagersError as exc:
            if exc.kind is DeliveryFailure.UNKNOWN:
                self._record(
                    SyncEvent.SEND_UNKNOWN,
                    key=key,
                    outcome=SyncOutcome.UNKNOWN,
                    attempt=attempt_no,
                )
                return SyncOutcome.UNKNOWN  # no finish — keep the marker
            confirmed = await self._finish_confirmed(key, attempt, attempt_no)
            if confirmed is None:
                return SyncOutcome.UNKNOWN  # cancelled during finish
            if not confirmed:
                return SyncOutcome.BLOCKED  # marker is not ours to clear
            if exc.kind is DeliveryFailure.REJECTED:
                self._record(
                    SyncEvent.SEND_REJECTED,
                    key=key,
                    outcome=SyncOutcome.FAILED,
                    attempt=attempt_no,
                )
                return SyncOutcome.FAILED  # terminal for the round
            # DeliveryFailure.NOT_SENT — the only retryable failure.
            # Marker cleared (finish confirmed above); the loop may
            # re-read and send with a NEW attempt id in the budget.
            return _Resend()
        except Exception:
            # Contract violation (non-BcsManagersError escape): the PUT
            # may or may not have landed — same posture as UNKNOWN.
            self._record(
                SyncEvent.SEND_UNKNOWN,
                key=key,
                outcome=SyncOutcome.UNKNOWN,
                attempt=attempt_no,
            )
            return SyncOutcome.UNKNOWN

        confirmed = await self._finish_confirmed(key, attempt, attempt_no)
        if confirmed is None:
            return SyncOutcome.UNKNOWN  # cancelled during finish
        if not confirmed:
            return SyncOutcome.BLOCKED
        if result.total != len(snapshot.manager_user_ids):
            # False success: BCS confirmed a different set than it was
            # handed — surface DRIFT, never CONFIRMED.
            self._record(
                SyncEvent.SEND_DRIFT,
                key=key,
                outcome=SyncOutcome.DRIFT,
                attempt=attempt_no,
                counts={
                    "expected": len(snapshot.manager_user_ids),
                    "reported": int(result.total),
                },
            )
            return SyncOutcome.DRIFT
        if result.total > 0:
            self._record(
                SyncEvent.SEND_CONFIRMED,
                key=key,
                outcome=SyncOutcome.CONFIRMED,
                attempt=attempt_no,
                counts={
                    "managers": int(result.total),
                    "added": int(result.added),
                    "removed": int(result.removed),
                },
            )
            return SyncOutcome.CONFIRMED
        # Empty roster acknowledged empty: total==0 proves the ack, not
        # that the target ever existed — Task 10/12 verify existence
        # from registration evidence, never from this answer.
        self._record(
            SyncEvent.SEND_ACKNOWLEDGED_EMPTY,
            key=key,
            outcome=SyncOutcome.ACKNOWLEDGED_EMPTY,
            attempt=attempt_no,
        )
        return SyncOutcome.ACKNOWLEDGED_EMPTY

    # -- finish / release ------------------------------------------------

    async def _finish_confirmed(
        self, key: SyncKey, attempt: Attempt, attempt_no: int
    ) -> bool | None:
        """Finish the confirmed attempt in a thread.

        ``True`` — marker deleted for this attempt. ``False`` — the
        marker is not (or no longer) ours to clear ⇒ BLOCKED. ``None`` —
        the finish thread was cancelled: its result is unknown, so the
        marker stays and the round reports UNKNOWN. Infrastructure
        failures (``CoordinationUnavailable``) become ``False``; nothing
        else is swallowed.
        """
        try:
            confirmed = await asyncio.to_thread(self._coordinator.finish, attempt)
        except asyncio.CancelledError:
            self._record(
                SyncEvent.CANCELLED,
                key=key,
                outcome=SyncOutcome.UNKNOWN,
                attempt=attempt_no,
            )
            return None
        except CoordinationUnavailable:
            self._record(
                SyncEvent.FINISH_FAILED,
                key=key,
                outcome=SyncOutcome.BLOCKED,
                attempt=attempt_no,
            )
            return False
        if not confirmed:
            self._record(
                SyncEvent.FINISH_MISMATCH,
                key=key,
                outcome=SyncOutcome.BLOCKED,
                attempt=attempt_no,
            )
        return confirmed

    async def _release_quietly(self, key: SyncKey, lease: Lease) -> None:
        """Release this holder's lease; never let cleanup mask a result
        or touch a marker. An unreleaseable lease self-heals by TTL."""
        try:
            await asyncio.to_thread(self._coordinator.release, lease)
        except Exception:
            self._record(
                SyncEvent.RELEASE_FAILED, key=key, outcome=SyncOutcome.FAILED
            )

    # -- diagnostics --------------------------------------------------------

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
            event, key=key, outcome=outcome, attempt=attempt, counts=counts or {}
        )


class _Resend:
    """Sentinel: the attempt finished NOT_SENT and a retry is allowed."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return "<resend allowed>"