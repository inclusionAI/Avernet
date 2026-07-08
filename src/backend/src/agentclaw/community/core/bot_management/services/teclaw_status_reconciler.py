"""Post-approve teclaw container status reconciler.

When a **teclaw** bot is created, its container is started asynchronously by
BaaS, so the status is not yet known at creation — ``TeclawProvisionService``
records a ``PENDING`` binding and bot row. This reconciler closes that gap: right
after the container is approved, it polls BaaS ``get_publish_progress(publish_id)``
on an interval and, on a **terminal** result (``SUCCESS`` → ``ACTIVE`` /
``FAILED``/``REJECTED``/``REVOKED`` → ``FAILED``), writes the mapped status back
to **both** the bot row (``ac_bots.status``) and the bot's device binding — then
stops. It gives up at a 10-minute cap, leaving the last-written status.

Persisting the status makes the stored column the **authoritative,
eventually-consistent source of truth**, so list / detail / search all read the
DB column directly — no per-read BaaS call (the old read-through). The trade is a
≤1-poll-interval staleness window (the same model desktop uses), which buys
correct ``/api/bots/search`` filtering and pagination on the status column.

Design notes:
- The provision path is **synchronous** (``bot_service.create_bot`` /
  ``TeclawProvisionService.provision`` are sync ``def``s, no event loop), so the
  poll runs in the background — NOT ``asyncio.create_task`` (no running loop).
- **No thread parks while waiting.** Each poll is a *single, short* step that, if
  the status is not yet terminal, **reschedules itself** on a shared
  :class:`_DelayedScheduler` after ``poll_interval_s`` — it never ``sleep``s in
  place. A single module-level daemon timer thread serves every in-flight
  reconcile off a due-time heap, so the thread count is **O(1)** regardless of
  how many teclaw bots are converging (vs. one parked thread per bot). The timer
  thread is a daemon, so a process/test shutdown is never blocked on an
  in-flight ≤10-min reconcile (consistent with the accepted best-effort/restart
  gap below); and because every scheduled step is short, there is no
  ``ThreadPoolExecutor``-style atexit-join hazard.
- **Best-effort:** a transient BaaS error is logged and retried on the next
  interval; a poll step never raises. It writes only on the terminal transition
  (the row is already ``PENDING`` from provision, so re-writing ``PENDING`` would
  be churn).
- **Restart durability (accepted gap):** the scheduler is in-process; a backend
  restart mid-window leaves the bot at its last-written status. No backstop scan
  in this feature (locked decision 2026-06-12).
- DB session thread-safety: the repos open a session per call, so each write
  from the timer thread is self-contained.
"""
from __future__ import annotations

import functools
import heapq
import threading
import time
from typing import TYPE_CHECKING, Callable

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.log import get_logger


if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

logger = get_logger()

# Statuses that end the poll loop. PENDING (still in flight) and ``None`` (a
# transient BaaS probe error) are NOT terminal — keep polling until timeout.
_TERMINAL_STATUSES = frozenset({DeviceBindingStatus.ACTIVE, DeviceBindingStatus.FAILED})


class _DelayedScheduler:
    """Minimal single-thread scheduled executor (à la a single-threaded
    ``ScheduledExecutorService``): ``schedule(fn, delay_s)`` runs ``fn`` after a
    delay on one shared daemon timer thread.

    Pending callables live in a due-time min-heap; the timer thread waits until
    the earliest is due (no busy-spin, no per-task thread), then runs it. A task
    that wants to run again simply calls :meth:`schedule` on itself — the wait
    between runs is held in the heap, never by a parked worker. The thread is
    started lazily on first use and lives for the process; callables are run
    best-effort (an exception in one never kills the loop or other tasks).
    """

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = 0
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None

    def schedule(self, fn: Callable[[], None], delay_s: float) -> None:
        with self._cond:
            due = time.monotonic() + max(0.0, delay_s)
            heapq.heappush(self._heap, (due, self._seq, fn))
            self._seq += 1
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="teclaw-reconciler-scheduler", daemon=True
                )
                self._thread.start()
            self._cond.notify()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._heap:
                    self._cond.wait()
                due, _seq, fn = self._heap[0]
                now = time.monotonic()
                if due > now:
                    # Wait until the head is due (or a sooner task arrives).
                    self._cond.wait(timeout=due - now)
                    continue
                heapq.heappop(self._heap)
            try:
                fn()  # run outside the lock — fn may reschedule itself
            except Exception as e:  # best-effort: one bad task never wedges the loop
                logger.warning(
                    "[TeclawStatusReconciler] scheduled poll step raised: %s", e
                )


# Process-wide scheduler shared by all reconcilers (one daemon timer thread).
_SCHEDULER = _DelayedScheduler()


def map_publish_status(publish_status: str | None) -> str:
    """Map a BaaS publish-workflow status onto a stored bot/binding status.

    ``SUCCESS`` → ``ACTIVE``; ``FAILED``/``REJECTED``/``REVOKED`` → ``FAILED``;
    everything still in flight (``INIT``/``PENDING``/``ACTIVE``-executing/
    ``APPROVING``) or unknown → ``PENDING``.
    """
    normalized = (publish_status or "").strip().upper()
    if normalized == "SUCCESS":
        return DeviceBindingStatus.ACTIVE
    if normalized in ("FAILED", "REJECTED", "REVOKED"):
        return DeviceBindingStatus.FAILED
    return DeviceBindingStatus.PENDING


class TeclawStatusReconciler:
    """Polls BaaS for a teclaw container's status and persists it on terminal."""

    def __init__(
        self,
        *,
        baas_service: "BaasService",
        bot_repository: "BotRepository",
        device_binding_repo: "DeviceBindingRepository",
        poll_interval_s: float = 10.0,
        timeout_s: float = 600.0,
        schedule: Callable[[Callable[[], None], float], None] = _SCHEDULER.schedule,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._baas = baas_service
        self._bot_repository = bot_repository
        self._device_binding_repo = device_binding_repo
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s
        # Injected seams keep start/_poll_step unit-testable without real
        # threads/clocks; the test DI wires a no-op ``schedule`` so the real
        # provision path never starts the timer thread under test.
        self._schedule = schedule
        self._clock = clock

    def reconcile_once(self, publish_id: int) -> str | None:
        """One poll: ``baas.get_publish_progress`` → :func:`map_publish_status`.

        Returns the mapped status (``ACTIVE``/``FAILED``/``PENDING``), or ``None``
        when the BaaS probe fails — a status read must never break the poll loop,
        so a transient error is logged and retried on the next interval.
        """
        try:
            progress = self._baas.get_publish_progress(publish_id)
        except Exception as e:  # best-effort: a probe failure is not terminal
            logger.warning(
                "[TeclawStatusReconciler.reconcile_once] publish progress probe "
                "failed for publish_id=%s: %s",
                publish_id,
                e,
            )
            return None
        return map_publish_status((progress or {}).get("status"))

    def start(
        self, *, publish_id: int, bot_id: str, owner_id: str, binding_id: int
    ) -> None:
        """Fire-and-forget: schedule the first poll step via the ``schedule`` seam.

        No-op (with a warning) when the ids needed to poll/persist are missing,
        so a malformed call can never schedule a doomed reconcile. The deadline is
        captured now (``clock() + timeout_s``); each step reschedules the next
        until terminal or deadline.
        """
        if publish_id is None or not binding_id:
            logger.warning(
                "[TeclawStatusReconciler.start] skipping reconcile for bot_id=%s "
                "— missing publish_id=%s or binding_id=%s",
                bot_id,
                publish_id,
                binding_id,
            )
            return
        deadline = self._clock() + self._timeout_s
        self._schedule(
            functools.partial(
                self._poll_step, publish_id, bot_id, owner_id, binding_id, deadline, 1
            ),
            0.0,  # first poll immediately
        )
        logger.info(
            "[TeclawStatusReconciler.start] scheduled status reconcile for "
            "bot_id=%s publish_id=%s binding_id=%s",
            bot_id,
            publish_id,
            binding_id,
        )

    def _poll_step(
        self,
        publish_id: int,
        bot_id: str,
        owner_id: str,
        binding_id: int,
        deadline: float,
        attempt: int,
    ) -> None:
        """One poll: resolve status, persist on terminal, else reschedule self.

        Reschedules the next step after ``poll_interval_s`` while the status is
        non-terminal (or a terminal status that failed to persist) and the
        deadline hasn't passed; stops on a persisted terminal status or at the
        timeout cap. Never raises (best-effort) — the scheduler's loop would log
        and survive anyway, but a step is self-contained.
        """
        status = self.reconcile_once(publish_id)
        if status in _TERMINAL_STATUSES and self._persist(
            bot_id=bot_id, owner_id=owner_id, binding_id=binding_id, status=status
        ):
            logger.info(
                "[TeclawStatusReconciler] reconciled publish_id=%s bot_id=%s to "
                "%s after %d poll(s); stopping",
                publish_id,
                bot_id,
                status,
                attempt,
            )
            return
        if self._clock() >= deadline:
            logger.info(
                "[TeclawStatusReconciler] gave up on publish_id=%s bot_id=%s "
                "after %d poll(s) / %.0fs timeout; leaving last status=%s",
                publish_id,
                bot_id,
                attempt,
                self._timeout_s,
                status,
            )
            return
        self._schedule(
            functools.partial(
                self._poll_step,
                publish_id,
                bot_id,
                owner_id,
                binding_id,
                deadline,
                attempt + 1,
            ),
            self._poll_interval_s,
        )

    def _persist(
        self, *, bot_id: str, owner_id: str, binding_id: int, status: str
    ) -> bool:
        """Write the terminal status to the bot row + device binding.

        Best-effort: returns ``True`` only when both writes succeed, so a
        transient persistence error leaves the reconcile rescheduling (it retries
        on the next interval until the timeout cap) rather than silently dropping
        the terminal status.
        """
        try:
            self._bot_repository.update_by_owner(bot_id, owner_id, {"status": status})
            self._device_binding_repo.update_status(
                binding_id=binding_id, status=status
            )
        except Exception as e:
            logger.warning(
                "[TeclawStatusReconciler._persist] failed to persist status=%s "
                "for bot_id=%s binding_id=%s: %s (will retry next interval)",
                status,
                bot_id,
                binding_id,
                e,
            )
            return False
        return True
