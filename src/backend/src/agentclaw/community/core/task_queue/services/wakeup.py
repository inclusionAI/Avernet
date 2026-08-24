"""WorkerWakeup — the latch that lets an enqueue cut short the worker's idle wait.

The worker's poll loop sleeps ``poll_interval_seconds`` between claims, so a
task enqueued just after a tick waits out most of that interval before anything
looks at it. This latch turns that interval into a **ceiling** rather than a
fixed cost: :meth:`notify` wakes the loop immediately, and the timer only
applies when nobody signalled.

A DI singleton shared by :class:`TaskQueueService` (which signals) and
:class:`TaskWorker` (which waits), so neither has to depend on the other.

**Thread safety is the whole point of this class.** ``enqueue()`` is
synchronous and runs on whatever thread called it — which, for a handler
enqueuing follow-up work, is a ``asyncio.to_thread`` pool thread, not the event
loop thread. ``asyncio.Event`` has no internal locking (it assumes only the loop
thread touches it), and more importantly a plain ``set()`` from a foreign thread
appends to the loop's ready queue *without waking the loop out of ``select()``*
— so the callback would sit there until the poll timer expired anyway, silently
costing exactly the latency this class exists to remove. ``call_soon_threadsafe``
both queues the callback and writes to the loop's self-pipe, which is what
actually ends the sleep. Every cross-thread signal must go through it.

Signals **coalesce**: the latch is one ``asyncio.Event``, so a handler fanning
out five hundred tasks produces one extra poll, not five hundred. The worker's
existing greedy re-poll then drains the backlog at full speed.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from agentclaw.community.log import get_logger

logger = get_logger()


class WorkerWakeup:
    """A coalescing, thread-safe wake latch for the task worker's poll loop."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        # The loop the worker's poll runs on. ``None`` is a real state, not a
        # widened type: it means this process has no running worker loop — the
        # worker is disabled by config, has not reached ``startup()`` yet, or
        # has already shut down. In every one of those cases there is genuinely
        # nothing to wake, and a signal is correctly a no-op.
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── bound by the worker ─────────────────────────────────────────────
    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the loop that :meth:`wait` runs on.

        Called from the worker's ``startup()``, which runs *on* that loop —
        ``asyncio.get_running_loop()`` only works from the loop thread, so the
        handle has to be captured there rather than looked up at signal time.
        """
        self._loop = loop

    def unbind(self) -> None:
        """Detach the loop. Called from the worker's ``shutdown()`` so a late
        signal from a still-draining handler thread becomes a no-op instead of
        touching a loop that is going away."""
        self._loop = None

    # ── signalled by enqueue (any thread) ───────────────────────────────
    def notify(self) -> None:
        """Ask the worker to poll now. Safe to call from any thread.

        A no-op when no worker loop is bound in this process, or when the loop
        has already closed — both mean the wake has nowhere to go, and the task
        is still picked up by the next claim (here or on another pod). Never
        raises: an enqueue must not fail because the latency optimisation
        could not be delivered.
        """
        loop = self._loop
        if loop is None:
            return
        try:
            # NB: ``self._event.set`` is passed unbound and *uncalled* — the
            # loop invokes it on its own thread. Adding parentheses here would
            # run it on the calling thread, which is the exact bug this class
            # exists to prevent.
            loop.call_soon_threadsafe(self._event.set)
        except RuntimeError:
            # ``_check_closed()`` firing: the loop shut down between the read of
            # ``self._loop`` above and this call. A real race whenever handler
            # threads outlive teardown.
            logger.debug("[WorkerWakeup] notify skipped — event loop is closed")

    # ── awaited by the worker ───────────────────────────────────────────
    async def wait(self, timeout: float) -> bool:
        """Block until signalled or ``timeout`` elapses. Returns whether a
        signal arrived (diagnostic only — the caller polls either way).

        The latch is cleared **after** waking and **before** the caller's poll,
        which is what makes a signal impossible to lose: anything enqueued
        before the clear is visible to the poll that follows it, and anything
        enqueued after it re-sets the latch, so the next wait returns at once.
        Clearing *before* waiting would instead discard a signal that arrived
        while the previous poll was still running.
        """
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._event.clear()
