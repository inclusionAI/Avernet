"""BotManagerSyncLifecycle — the dedicated worker-loop owner.

One pthread hosts exactly one asyncio loop; the dispatcher workers and every
``AsyncClient`` the delivery plugin lazily creates live on that loop. Synchronous
repository reads use a bounded executor. A running synchronous read cannot be
forcibly cancelled, so shutdown bounds its own wait and never claims otherwise.

Startup/shutdown posture (each pinned by a test):

- **Startup never aborts the backend.** A failed start (thread, loop,
  executor, worker spawn) only closes the syncer's admission — callers
  get REJECTED, matching "unavailable" — and alerts; the process keeps
  booting. Structural config errors are a different failure: they raise
  at config resolution, per the repo's config policy.
- **Shutdown stops admission first, drains bounded, then ends the loop.**
  In-flight async rounds are cancelled and ``bcs.aclose()`` runs on the worker
  loop. No shutdown path creates delayed retries or persistent sync state.
- **The disabled posture holds zero resources.** The composition root
  binds :class:`DisabledBotManagerSyncLifecycle` whenever the feature is
  off or its stack is unavailable, so lifecycle discovery still resolves
  a participant without spinning a thread loop for a disabled feature.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin

logger = get_logger()

#: Name of the one thread owning the syncer's asyncio loop.
_WORKER_LOOP_THREAD_NAME = "manager-sync-loop"

#: Upper bound of the loop-owned default executor: the service's
#: ``to_thread`` repository reads run here,
#: four workers deep — never unbounded.
_EXECUTOR_MAX_WORKERS = 8

#: Bounded waits (seconds) for the loop thread / worker handshake.
_LOOP_READY_TIMEOUT_SECONDS = 5.0
_ATTACH_TIMEOUT_SECONDS = 5.0
_STOP_TIMEOUT_SECONDS = 10.0
_JOIN_TIMEOUT_SECONDS = 10.0
_STARTUP_COMPLETION_TIMEOUT_SECONDS = (
    _LOOP_READY_TIMEOUT_SECONDS + _ATTACH_TIMEOUT_SECONDS + 1.0
)

#: How long shutdown lets already-admitted work finish before the
#: worker tasks are cancelled. Bounded, never "wait until the queue is
#: empty no matter what".
_SHUTDOWN_DRAIN_SECONDS = 10.0


class BotManagerSyncLifecycle(LifecycleBase):
    """Own the dispatcher's thread, loop and executor; close bcs there."""

    def __init__(
        self,
        dispatcher: BotManagerSyncDispatcher,
        bcs: BcsManagersPlugin,
    ) -> None:
        if not isinstance(dispatcher, BotManagerSyncDispatcher):
            raise TypeError("manager-sync lifecycle requires the real dispatcher")
        self._dispatcher = dispatcher
        self._bcs = bcs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started_lock = threading.Lock()
        self._startup_claimed = False
        self._started = False
        self._startup_done = threading.Event()
        self._startup_done.set()
        self._startup_abort = threading.Event()

    # -- Lifecycle ----------------------------------------------------------------

    async def startup(self) -> None:
        """Start the dedicated loop + workers; never raise to the boot.

        Config-structural errors never reach here (they fail at config
        resolution); everything that can go wrong at runtime makes the
        syncer unavailable — admission closed, alert logged — and the
        backend finishes its startup regardless.
        """
        with self._started_lock:
            if self._startup_claimed:
                return
            # A lifecycle instance is single-use: its dispatcher admission is
            # permanently closed after failure or shutdown, so retrying startup
            # would only reopen loop/thread ownership races without recovery.
            self._startup_claimed = True
            self._started = True
            self._startup_abort.clear()
            self._startup_done.clear()

        def _start_and_signal() -> None:
            try:
                self._start_background()
            finally:
                # The synchronous operation owns this signal. Cancelling the
                # awaiting coroutine cannot falsely report startup complete.
                self._startup_done.set()

        try:
            await asyncio.to_thread(_start_and_signal)
        except Exception:
            logger.error(
                "[MANAGER-SYNC-LIFECYCLE] startup_failed syncer unavailable "
                "(worker loop did not come up cleanly)"
            )
            try:
                self._dispatcher.close_admission()
            except Exception:  # noqa: BLE001 — best-effort degrade
                logger.error("[MANAGER-SYNC-LIFECYCLE] close_admission_failed")
            # Partial resources (thread/loop) must not outlive the
            # failed start: stop the loop where it runs and join it.
            with self._started_lock:
                owns_cleanup = self._started
                self._started = False
            if owns_cleanup:
                try:
                    await asyncio.to_thread(self._stop_on_worker_loop)
                except Exception:  # noqa: BLE001 — startup must still degrade cleanly
                    logger.error("[MANAGER-SYNC-LIFECYCLE] startup_cleanup_failed")
            return
        with self._started_lock:
            still_started = self._started
        if still_started:
            logger.info("[MANAGER-SYNC-LIFECYCLE] started")

    async def shutdown(self) -> None:
        """Close admission, drain bounded, stop the loop, close bcs there.

        Cancellation propagates through running async rounds; no pool-close
        path reports a delivery result or schedules later work.
        """
        with self._started_lock:
            if not self._started:
                return
            startup_done = self._startup_done
        self._dispatcher.close_admission()
        startup_finished = await asyncio.to_thread(
            startup_done.wait,
            _STARTUP_COMPLETION_TIMEOUT_SECONDS,
        )
        if not startup_finished:
            self._startup_abort.set()
            logger.error(
                "[MANAGER-SYNC-LIFECYCLE] startup_wait_timeout — aborting startup"
            )
            startup_finished = await asyncio.to_thread(
                startup_done.wait,
                _JOIN_TIMEOUT_SECONDS,
            )
            if not startup_finished:
                logger.error(
                    "[MANAGER-SYNC-LIFECYCLE] startup_abort_timeout — "
                    "continuing bounded shutdown"
                )
        with self._started_lock:
            if not self._started:
                return
            self._started = False
        try:
            drained = await self._dispatcher.drain(_SHUTDOWN_DRAIN_SECONDS)
            if drained is False:
                logger.warning(
                    "[MANAGER-SYNC-LIFECYCLE] drain_timeout — cancelling "
                    "in-flight async rounds"
                )
        except Exception:  # noqa: BLE001 — teardown must not blow up shutdown
            logger.error("[MANAGER-SYNC-LIFECYCLE] drain_failed")
        # Stop workers + close the delivery pool ON the worker loop, so a
        # lazily-created AsyncClient is closed where it was created.
        await asyncio.to_thread(self._stop_on_worker_loop)

    async def bootstrap(self) -> None:
        return None

    async def teardown(self) -> None:
        return None

    # -- internals -----------------------------------------------------------------

    def _start_background(self) -> None:
        """Spawn the loop thread; attach the dispatcher onto that loop."""
        if self._startup_abort.is_set():
            raise RuntimeError("manager-sync startup aborted")
        ready = threading.Event()
        start_loop = threading.Event()
        abort = threading.Event()
        loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

        def _run_loop() -> None:
            loop = asyncio.new_event_loop()
            executor = ThreadPoolExecutor(
                max_workers=_EXECUTOR_MAX_WORKERS,
                thread_name_prefix="manager-sync-exec",
            )
            loop.set_default_executor(executor)
            asyncio.set_event_loop(loop)
            loop_holder["loop"] = loop
            ready.set()
            # Do not enter run_forever until the starter accepts the loop. If
            # readiness times out, the abort handshake lets this late thread
            # close itself instead of becoming an unowned daemon loop.
            start_loop.wait()
            try:
                if not abort.is_set() and not self._startup_abort.is_set():
                    loop.run_forever()
            finally:
                # Running synchronous repository calls cannot be stopped. Do
                # not let executor shutdown turn the lifecycle's bounded join
                # into an unbounded wait; outstanding calls finish naturally.
                executor.shutdown(wait=False, cancel_futures=True)
                loop.close()

        thread = threading.Thread(
            target=_run_loop, name=_WORKER_LOOP_THREAD_NAME, daemon=True
        )
        thread.start()
        if not ready.wait(_LOOP_READY_TIMEOUT_SECONDS):
            abort.set()
            start_loop.set()
            thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                logger.warning(
                    "[MANAGER-SYNC-LIFECYCLE] worker_loop_thread_lingers_after_start_timeout"
                )
            raise RuntimeError("manager-sync worker loop did not become ready")
        if self._startup_abort.is_set():
            abort.set()
            start_loop.set()
            thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
            raise RuntimeError("manager-sync startup aborted")
        self._loop = loop_holder["loop"]
        self._thread = thread
        start_loop.set()
        # Bind the dispatcher and confirm its workers actually spawned —
        # the coroutine runs on the dedicated loop, this thread only waits.
        fut = asyncio.run_coroutine_threadsafe(self._dispatcher.attach(), self._loop)
        fut.result(timeout=_ATTACH_TIMEOUT_SECONDS)

    def _stop_on_worker_loop(self) -> None:
        """Cancel workers and close bcs on their loop, then stop it."""
        loop = self._loop
        if loop is None:
            return

        async def _shutdown_on_loop() -> None:
            await self._dispatcher.stop()
            await self._bcs.aclose()

        fut = asyncio.run_coroutine_threadsafe(_shutdown_on_loop(), loop)
        try:
            fut.result(timeout=_STOP_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 — teardown must not blow up shutdown
            logger.error("[MANAGER-SYNC-LIFECYCLE] worker_loop_stop_failed")
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass  # already stopped
        thread = self._thread
        if thread is not None:
            thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                logger.warning("[MANAGER-SYNC-LIFECYCLE] worker_loop_thread_lingers")
        self._teardown_worker_loop()

    def _teardown_worker_loop(self) -> None:
        """Clear the references; the thread's finally closed the loop."""
        self._loop = None
        self._thread = None


class DisabledBotManagerSyncLifecycle(BotManagerSyncLifecycle):
    """Zero-resource participant for the disabled / unavailable posture.

    The composition root binds this when the feature is off or the
    stack could not resolve, so lifecycle discovery still resolves a
    (no-op) participant and never sees a raise. Deliberately calls no
    ``__init__``super — there is no dispatcher or delivery pool to own.
    """

    def __init__(self, reason: str = "disabled") -> None:
        # No super().__init__: this posture owns nothing at all. The
        # reason is a stable diagnostic word (never secret material).
        self._reason = reason

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return f"<DisabledBotManagerSyncLifecycle reason={self._reason!r}>"

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def bootstrap(self) -> None:
        return None

    async def teardown(self) -> None:
        return None


__all__ = [
    "BotManagerSyncLifecycle",
    "DisabledBotManagerSyncLifecycle",
]
