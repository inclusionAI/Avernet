"""Unit tests for WorkerWakeup — the latch that cuts short the worker's idle wait.

The interesting cases are all about *thread boundaries* and *lost signals*, not
about the happy path:

- a signal raised from a real non-loop thread must actually end the wait (a
  plain ``Event.set()`` would queue the callback without waking the loop out of
  ``select()``, so the wait would run its full timeout and the feature would be
  a silent no-op);
- a signal raised while the caller is *between* waits must survive to the next
  one, because that is exactly when an enqueue lands during a poll.

Timeouts here are deliberately lopsided: the "slow" timeout is far longer than
any real signal delay, so a test that accidentally falls back to the timer fails
loudly on elapsed time instead of passing slowly.
"""
import asyncio
import threading
import time

import pytest

from agentclaw.community.core.task_queue.services.wakeup import WorkerWakeup

pytestmark = pytest.mark.integration

#: Long enough that reaching it means the wake mechanism did not work at all.
_SLOW_TIMEOUT = 10.0
#: Short enough to keep the suite fast when a timeout is the expected outcome.
_FAST_TIMEOUT = 0.05


# ── binding ─────────────────────────────────────────────────────────────────
def test_notify_before_bind_is_a_noop():
    """The worker may be disabled by config, or simply not have reached
    ``startup()`` yet. An enqueue must not blow up because there is no loop to
    wake — the task is still picked up by whatever polls next."""
    WorkerWakeup().notify()  # must not raise


def test_notify_after_unbind_is_a_noop():
    """``shutdown()`` unbinds, but handler threads can still be draining and
    may enqueue follow-up work on the way out."""
    wakeup = WorkerWakeup()

    async def drive():
        wakeup.bind(asyncio.get_running_loop())
        wakeup.unbind()

    asyncio.run(drive())
    wakeup.notify()  # must not raise


def test_notify_after_the_loop_closed_is_a_noop():
    """Still bound, but the loop is gone: ``call_soon_threadsafe`` raises
    ``RuntimeError`` from ``_check_closed()``. A latency optimisation must never
    turn into a failed enqueue."""
    wakeup = WorkerWakeup()

    async def drive():
        wakeup.bind(asyncio.get_running_loop())

    asyncio.run(drive())  # returns with the loop closed but still bound
    wakeup.notify()  # must not raise


# ── waiting ─────────────────────────────────────────────────────────────────
def test_wait_times_out_when_nobody_signals():
    """The unsignalled path is the old behaviour: wait out the interval."""

    async def drive():
        wakeup = WorkerWakeup()
        wakeup.bind(asyncio.get_running_loop())
        return await wakeup.wait(_FAST_TIMEOUT)

    assert asyncio.run(drive()) is False


def test_wait_returns_immediately_when_already_signalled():
    """A signal raised *between* waits — an enqueue landing while the worker was
    mid-poll — must not be lost. This is the case that a clear-before-wait
    implementation would silently drop."""

    async def drive():
        wakeup = WorkerWakeup()
        wakeup.bind(asyncio.get_running_loop())
        wakeup.notify()
        await asyncio.sleep(0)  # let the queued set() run
        started = time.monotonic()
        signalled = await wakeup.wait(_SLOW_TIMEOUT)
        return signalled, time.monotonic() - started

    signalled, elapsed = asyncio.run(drive())
    assert signalled is True
    assert elapsed < _SLOW_TIMEOUT / 5


def test_wait_clears_the_latch_so_the_next_wait_blocks_again():
    """One signal buys one wake. Leaving the latch set would spin the poll loop
    at full speed forever."""

    async def drive():
        wakeup = WorkerWakeup()
        wakeup.bind(asyncio.get_running_loop())
        wakeup.notify()
        await asyncio.sleep(0)
        first = await wakeup.wait(_SLOW_TIMEOUT)
        second = await wakeup.wait(_FAST_TIMEOUT)
        return first, second

    first, second = asyncio.run(drive())
    assert first is True
    assert second is False


def test_a_burst_of_notifies_coalesces_into_a_single_wake():
    """A handler fanning out many tasks must not schedule one poll per task. The
    latch is a single Event, so N signals collapse to one wake — after which the
    worker's greedy re-poll drains the backlog."""

    async def drive():
        wakeup = WorkerWakeup()
        wakeup.bind(asyncio.get_running_loop())
        for _ in range(500):
            wakeup.notify()
        await asyncio.sleep(0)
        woke = await wakeup.wait(_SLOW_TIMEOUT)
        # If the burst had queued 500 independent wakes, this would return True.
        extra = await wakeup.wait(_FAST_TIMEOUT)
        return woke, extra

    woke, extra = asyncio.run(drive())
    assert woke is True
    assert extra is False


# ── the cross-thread case this class exists for ─────────────────────────────
def test_notify_from_a_non_loop_thread_ends_the_wait_promptly():
    """The motivating case: a handler running under ``asyncio.to_thread``
    enqueues follow-up work and signals from that pool thread.

    A bare ``Event.set()`` here would append to the loop's ready queue without
    waking it out of ``select()``, so this wait would run the full
    ``_SLOW_TIMEOUT`` and the test would fail on elapsed time — which is exactly
    the silent regression the assertion below is guarding.
    """
    wakeup = WorkerWakeup()
    idents: dict[str, int] = {}

    async def drive():
        wakeup.bind(asyncio.get_running_loop())
        idents["loop"] = threading.get_ident()

        def signal_from_another_thread():
            idents["notifier"] = threading.get_ident()
            time.sleep(0.05)  # let the loop reach select() first
            wakeup.notify()

        thread = threading.Thread(target=signal_from_another_thread)
        thread.start()
        started = time.monotonic()
        signalled = await wakeup.wait(_SLOW_TIMEOUT)
        elapsed = time.monotonic() - started
        thread.join()
        return signalled, elapsed

    signalled, elapsed = asyncio.run(drive())

    # Guards the premise: if these ever matched, the test would prove nothing.
    assert idents["notifier"] != idents["loop"]
    assert signalled is True
    assert elapsed < _SLOW_TIMEOUT / 5
