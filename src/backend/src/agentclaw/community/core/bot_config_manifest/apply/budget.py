"""One apply's fetch allowance — the ledger behind the lock TTL's sanity.

`fetch/limits.py` defines the two apply-scope numbers (a wall-clock budget
and a total downloaded bytes cap) and W4's comments promised an orchestrator
threading them through every entry. Nothing ever did — W4 fetched nothing,
so the promise was inert; W5 put real fetches behind it and an audit caught
the gap live: 50 entries x 60s-per-hop x a 30-minute lock TTL can hold the
per-bot lock long enough that the stale-lock reaper hands a still-running
apply's lock to a second one, and the UNIQUE lock guard is then the only
thing between two concurrent writers of the same bot.

This object is that threading. One instance per apply, carried by the (--
otherwise frozen) ``ApplyContext`` and consulted by the entry fetcher:

- **before** each fetch — an entry that starts past the deadline is
  refused without touching the network, so a budget-exhausted apply ends
  in bounded time and the lock is released for the next attempt;
- **after** each network fetch — its bytes are charged to the total.

Deliberately mutable: it is a ledger threaded through an immutable context,
the way a run's writes are a ledger threaded through an immutable bot id.
Reads answered from the platform's own copy cost neither half (no network
was touched); that is the fast path, and making it free is the point.
"""
from __future__ import annotations

import time
from typing import Callable


class ApplyFetchBudget:
    """The wall-clock and byte allowance one apply's fetches must fit in."""

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
        """Account one network fetch against the total."""
        if size_bytes > 0:
            self._spent_bytes += size_bytes

