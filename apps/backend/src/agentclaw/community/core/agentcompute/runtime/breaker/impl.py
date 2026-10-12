"""Upstream circuit breaker (task 12.5) — deterministic state + timestamps.

The breaker guards subagent LLM upstreams from caller-side stampede: when
the upstream is unhealthy, executions fail fast with
``error="upstream_breaker_open"`` instead of stalling runs toward their
deadline with per-attempt timeouts.
"""
from __future__ import annotations

import enum


class BreakerState(enum.StrEnum):
    CLOSED = "closed"
    OPEN = "open"


class UpstreamBreaker:
    """CLOSED/OPEN circuit breaker over consecutive upstream failures.

    Contract (D18-adjacent: closed set is the ``BreakerState`` enum):

    - ``allow(now)`` — CLOSED is always allowed; OPEN is refused until
      ``open_seconds`` have elapsed since it opened, after which the next
      call is allowed as a probe (success closes, failure re-opens).
      ``allow`` itself never mutates state: a probe outcome decides.
    - ``record_success()`` — a fully successful upstream call: closes and
      resets the consecutive-failure count.
    - ``record_failure(now)`` — any upstream call failure: while CLOSED,
      ``failure_threshold`` consecutive failures trip to OPEN at ``now``;
      while OPEN, one probe failure re-opens immediately at ``now``.

    Pure state + passed-in timestamps, no clocks and no async, so it is
    deterministic under test. The asyncio runtime is single-threaded, so
    no lock is required.
    """

    def __init__(self, failure_threshold: int, open_seconds: float) -> None:
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def allow(self, now: float) -> bool:
        if self._state is BreakerState.CLOSED:
            return True
        opened_at = self._opened_at
        if opened_at is None:
            return True
        return (now - opened_at) >= self._open_seconds

    def record_success(self) -> None:
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self, now: float) -> None:
        self._consecutive_failures += 1
        if self._state is BreakerState.OPEN:
            self._opened_at = now
            return
        if self._consecutive_failures >= self._failure_threshold:
            self._state = BreakerState.OPEN
            self._opened_at = now