"""One apply's fetch allowance — the ledger behind the lock TTL's sanity.

One instance per apply, carried on the otherwise frozen ``ApplyContext`` and
consulted by the entry fetcher:

- **before** each fetch — an entry that starts past the deadline is
  refused without touching the network, so a budget-exhausted apply ends
  in bounded time and the lock is released for the next attempt;
- **after** each network fetch — its bytes are charged to the total.

Created by: ``services/config_manifest_apply_service`` (both the apply and the
dry-run path) as ``ApplyFetchBudget(deadline=time.monotonic() +
APPLY_BUDGET_S, total_bytes=APPLY_FETCH_TOTAL_LIMIT)`` — 300 seconds and
500 MiB, from ``fetch/limits.py``.
Consumed by: ``apply/entry_fetch.EntryFetcher.fetch_declared``, both fetchers
in ``apply/source_fetchers``, and ``apply/entry_delivery.GitDelivery.file``.

Deliberately mutable: it is a ledger threaded through an immutable context,
the way a run's writes are a ledger threaded through an immutable bot id.

The numbers are apply-scope and the lock TTL is why they are enforced at all:
50 entries at 60 seconds per hop can outrun a 30-minute lock TTL, and the
stale-lock reaper would then hand a still-running apply's lock to a second one.
"""
from __future__ import annotations

import time
from typing import Callable


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

