"""Bounded content acquisition for one Apply execution, independent of its producer.

The mutable ledger is shared by compilation and capability preparation. Limits
live here; Manifest's historical imports remain compatibility re-exports.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

APPLY_FETCH_TOTAL_LIMIT = 500 * 1024 * 1024
APPLY_BUDGET_S = 300.0


def new_fetch_budget() -> ApplyFetchBudget:
    """Start an allowance on this process's monotonic clock."""
    return ApplyFetchBudget(
        deadline=time.monotonic() + APPLY_BUDGET_S,
        total_bytes=APPLY_FETCH_TOTAL_LIMIT,
        clock=time.monotonic,
    )


class ApplyFetchBudget:
    """The wall-clock and byte allowance one apply's fetches must fit in.

    ``deadline`` is an absolute ``time.monotonic()`` reading, not a duration:
    the budget is exhausted once ``clock()`` reaches it. ``total_bytes`` is a
    running cap on everything charged. ``clock`` is the test seam; production
    leaves it at ``time.monotonic``::

        ApplyFetchBudget(
            deadline=time.monotonic() + 300.0,   # APPLY_BUDGET_S
            total_bytes=500 * 1024 * 1024,       # APPLY_FETCH_TOTAL_LIMIT
        )

        # A test pins both halves instead:
        ApplyFetchBudget(deadline=0.0, total_bytes=10**9)   # already expired
        ApplyFetchBudget(deadline=9e99, total_bytes=5)      # byte-starved

    What a ``charge`` looks like on each road:

    * object store — ``charge(len(content))`` in
      ``source_fetchers.ObjectStoreFetcher._acquire``: the bytes that came
      over the wire.
    * git — ``ctx.budget.charge(checkout.tree_bytes)`` in
      ``source_fetchers.GitSourceFetcher.fetch``: the whole tree's declared
      size, charged only when *this* call did the fetching.
    * canonical bytes filed back — ``charge(len(content))`` in
      ``entry_delivery.GitDelivery.file``, so a git entry's canonical form is
      on the ledger too.

    What is deliberately **not** charged:

    * a store hit, pinned or ``keep_last`` — no network moved, so the fast
      path is free;
    * a cached git checkout, when another entry already fetched that
      ``(url, ref)`` this apply — that call answers a read, not a fetch;
    * anything on a caller with no budget at all (``ctx.budget is None``),
      which is the hand-driven and single-install road.
    """

    def __init__(
        self,
        *,
        deadline: float,
        total_bytes: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._deadline = deadline
        self._total_bytes = total_bytes
        self._clock = clock
        self._spent_bytes = 0

    def expired(self) -> str | None:
        """Why the budget is exhausted, or ``None`` when it is not.

        The string is report-safe and lands verbatim as an entry's ``reason``::

            "this apply's fetch budget is exhausted (time): its entries have
             spent the whole of the apply's fetch time allowance — remaining
             entries fail rather than hold the bot's apply lock any longer"

        Time is checked first, so an apply that has run out of both reports the
        time reason.

        A reason rather than a boolean: bytes and time name different fixes
        for the caller, and an error that says which one ran out saves the
        author a re-apply to find out.
        """
        if self._clock() >= self._deadline:
            return (
                "this apply's fetch budget is exhausted (time): its entries "
                "have spent the whole of the apply's fetch time allowance — "
                "remaining entries fail rather than hold the bot's apply "
                "lock any longer"
            )
        if self._spent_bytes >= self._total_bytes:
            return (
                "this apply's fetch budget is exhausted (bytes): its entries "
                f"have downloaded {self._spent_bytes} of the "
                f"{self._total_bytes}-byte allowance — remaining entries fail "
                "rather than hold the bot's apply lock any longer"
            )
        return None

    def charge(self, size_bytes: int) -> None:
        """Account one network fetch against the total.

        Non-positive sizes are ignored, so a zero-byte object costs nothing.
        Charging never raises: it can push the ledger past its cap, and the
        next :meth:`expired` call is what refuses the following entry.
        """
        if size_bytes > 0:
            self._spent_bytes += size_bytes



_execution_budget: ContextVar[ApplyFetchBudget | None] = ContextVar("apply_fetch_budget", default=None)


def current_fetch_budget() -> ApplyFetchBudget | None:
    """The active execution's ledger; absent for callers outside a run.

    ``asyncio.to_thread`` copies the context and therefore shares this mutable
    object with the execution, rather than creating a second allowance.
    """
    return _execution_budget.get()


@contextmanager
def fetch_budget_scope() -> Iterator[None]:
    """Give one execution a fresh ledger and restore its caller on every exit.

    A nested Apply is a separate execution, even if its caller is already
    applying. ContextVar tokens preserve the outer execution on return and
    isolate sibling asyncio tasks.
    """
    token = _execution_budget.set(new_fetch_budget())
    try:
        yield
    finally:
        _execution_budget.reset(token)
