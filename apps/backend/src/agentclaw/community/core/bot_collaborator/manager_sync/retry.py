"""SyncRunPolicy — the immutable limits of one sync round.

The policy is a value injected into the service (default constructed in
tests with ``SyncRunPolicy()``); everything in it is read-only because a
round must not re-negotiate its own budget mid-flight.

- ``max_attempts`` — total PUTs one ``sync_once`` round may start; the
  first NOT_SENT retry consumes another attempt, each with a **new**
  attempt id. Uncertain sends are never retried inside a round, so in
  the worst case this is the number of *confirmed-not-sent* retries.
- ``budget_seconds`` — wall-clock budget of one round, measured on the
  injected clock; the remaining budget both bounds every backoff wait
  and is the ``timeout_seconds`` handed to the delivery plugin.
- ``lease_seconds`` — the claim/renew TTL for the coordination lease.
  With the default 15s budget / 30s lease the lease always outlives
  the budget and no renewal ever happens (see ``service.py``).

The helpers here stay pure: jitter and waiting are injectable so tests
use a fake clock and a recording wait instead of real sleeps.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

#: A send that cannot finish inside this much remaining budget is not
#: started at all — beginning a PUT with no time left only manufactures
#: UNKNOWN deliveries.
MIN_SEND_BUDGET_SECONDS = 1.0

#: The lease must cover the remaining send budget plus this margin
#: before a send may start; below that the service renews (strictly
#: inside the deadline) or refuses to send.
RENEW_MARGIN_SECONDS = 2.0

#: Upper bound of the default jitter fraction (additive, +0%…+10%).
_JITTER_FRACTION = 0.1


@dataclass(frozen=True)
class SyncRunPolicy:
    """Immutable limits for one ``sync_once`` round (see module docs)."""

    max_attempts: int = 3
    budget_seconds: float = 15.0
    lease_seconds: int = 30


def default_jitter(base_seconds: float) -> float:
    """Bounded additive jitter: ``base * (1 + U[0, 0.1])``.

    Jitter spreads retry pressure across deployments without ever
    shrinking below the base delay (waiting less than the base would
    turn contention into a busy loop).
    """
    return base_seconds * (1.0 + random.random() * _JITTER_FRACTION)


def backoff_delay(
    *,
    retry_index: int,
    remaining_seconds: float,
    jitter: Callable[[float], float],
) -> float | None:
    """Wait before retry ``retry_index`` (1 = after the first attempt).

    Exponential base ``2 ** (retry_index - 1)`` seconds — 1s, 2s, 4s … —
    plus the injected jitter, **bounded by the remaining budget** so a
    round never waits past its own deadline. ``None`` means the budget
    is already exhausted and no further attempt can be started.
    """
    if remaining_seconds <= 0:
        return None
    base = float(2 ** (retry_index - 1))
    return max(0.0, min(jitter(base), remaining_seconds))