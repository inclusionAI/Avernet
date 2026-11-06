"""Poll-tick failure reporting (observability, behaviour-neutral).

The poll loop's catch-all used to log every failure identically, so a
one-off connection-pool blip and a sustained outage produced the same
single line with no exception name. These tests pin the classification:
recognised infra transients are warnings tagged ``transient=true``, a
sustained streak escalates to error, an unrecognised exception keeps the
full traceback, and a successful tick reports recovery and resets the
streak. The claim path itself is unchanged — a failed tick is retried on
the next poll, which the last test covers.

Harness mirrors ``test_drain_breaker.py`` (same fakes, same worker shape).
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker
from agentclaw.community.core.agentswarm.runtime.worker.impl import (
    _POLL_FAILURE_ALERT_TICKS,
)

from tests.community.core.agentswarm.runtime.worker.test_drain_breaker import (
    DrainDispatchRepo, DrainHandler, _drain_config, _stub_dispatch,
)


class _PoolExhausted(Exception):
    """Stands in for sqlalchemy.exc.PoolError — a real prod transient.

    Constructed with the driver's class name so the worker's name-based
    classification sees exactly what it would in production, without the
    test importing SQLAlchemy.
    """

    def __init__(self, message: str = "pool exhausted") -> None:
        super().__init__(message)
        self.__class__.__name__ = "PoolError"


class _FlakyClaimRepo(DrainDispatchRepo):
    """``claim_batch`` raises a scripted exception per tick, then succeeds.

    ``failures`` is consumed one entry per call; ``None`` means succeed
    normally (which delegates to the parent's one-shot claim).
    """

    def __init__(self, failures: list[Exception | None], pending: int = 1) -> None:
        super().__init__(pending=pending)
        self._failures = list(failures)
        self.claim_attempts = 0

    def claim_batch(self, worker_id, tenant_filter, limit, lease_seconds):
        self.claim_attempts += 1
        failure = self._failures.pop(0) if self._failures else None
        if failure is not None:
            raise failure
        return super().claim_batch(worker_id, tenant_filter, limit, lease_seconds)


class _OneTickWorker(AgentSwarmWorker):
    """Pin the loop to exactly ``ticks`` iterations, then stop claiming."""

    def __init__(self, *args, ticks: int = 1, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._test_ticks = ticks

    async def run_once(self) -> int:
        try:
            if self._tick_count >= self._test_ticks:
                self._running = False
                return 0
            self._tick_count += 1
            return await super().run_once()
        finally:
            # Always stop after the budget so _loop() cannot spin forever.
            if self._tick_count >= self._test_ticks:
                self._running = False

    _tick_count = 0


def _poll_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "poll tick failed" in r.getMessage()]


def _run_loop(worker: AgentSwarmWorker) -> None:
    async def main():
        worker._running = True
        await worker._loop()

    asyncio.run(main())


class TestPollFailureLogging:

    def test_transient_failure_logs_warning_with_exception_name(self, caplog):
        repo = _FlakyClaimRepo([_PoolExhausted()])
        worker = _OneTickWorker(
            config=_drain_config(grace=1.0), handler=DrainHandler(),
            dispatch_repo=repo,
        )
        with caplog.at_level(logging.INFO, logger="agentswarm"):
            _run_loop(worker)
        records = _poll_records(caplog)
        assert len(records) == 1
        record = records[0]
        message = record.getMessage()
        assert record.levelno == logging.WARNING
        assert "transient=true" in message
        assert "exc=PoolError" in message
        # A transient blip is reported, not traced: no stack attached.
        assert record.exc_info is None

    def test_unexpected_failure_keeps_full_traceback(self, caplog):
        repo = _FlakyClaimRepo([ValueError("bad tenant filter")])
        worker = _OneTickWorker(
            config=_drain_config(grace=1.0), handler=DrainHandler(),
            dispatch_repo=repo,
        )
        with caplog.at_level(logging.INFO, logger="agentswarm"):
            _run_loop(worker)
        records = _poll_records(caplog)
        assert len(records) == 1
        record = records[0]
        assert record.levelno == logging.ERROR
        assert "transient=true" not in record.getMessage()
        assert "exc=ValueError" in record.getMessage()
        # Unrecognised => a defect: the traceback must stay attached.
        assert record.exc_info is not None
        assert record.exc_info[0] is ValueError

    def test_sustained_streak_escalates_to_error(self, caplog):
        ticks = _POLL_FAILURE_ALERT_TICKS
        repo = _FlakyClaimRepo([_PoolExhausted()] * ticks)
        worker = _OneTickWorker(
            config=_drain_config(grace=1.0), handler=DrainHandler(),
            dispatch_repo=repo, ticks=ticks,
        )
        with caplog.at_level(logging.INFO, logger="agentswarm"):
            _run_loop(worker)
        records = _poll_records(caplog)
        assert len(records) == ticks
        levels = [r.levelno for r in records]
        # Below the threshold: blips. At/above it: an outage.
        assert levels[0] == logging.WARNING
        assert levels[-1] == logging.ERROR
        assert "sustained" in records[-1].getMessage()
        assert worker._poll_failures == ticks

    def test_successful_tick_resets_streak_and_reports_recovery(self, caplog):
        repo = _FlakyClaimRepo(
            [_PoolExhausted(), None], pending=1,
        )
        repo.enqueue_dispatch(_stub_dispatch(run_id="recovery-run"))
        handler = DrainHandler()
        handler.gate("recovery-run").set()
        worker = _OneTickWorker(
            config=_drain_config(grace=1.0), handler=handler,
            dispatch_repo=repo, ticks=2,
        )
        with caplog.at_level(logging.INFO, logger="agentswarm"):
            _run_loop(worker)
        assert len(_poll_records(caplog)) == 1
        assert worker._poll_failures == 0
        recovery = [r for r in caplog.records if "poll recovered" in r.getMessage()]
        assert len(recovery) == 1
        assert "after 1 failed tick(s)" in recovery[0].getMessage()
        # The dispatch the failed tick never reached is claimed on retry.
        assert repo.claim_attempts == 2
        assert handler.outcomes == {"recovery-run": "complete"}

    def test_failed_claim_is_retried_on_the_next_tick(self):
        """Behaviour-neutral: the error never loses the dispatch."""
        repo = _FlakyClaimRepo([_PoolExhausted(), None], pending=1)
        repo.enqueue_dispatch(_stub_dispatch(run_id="retry-run"))
        handler = DrainHandler()
        handler.gate("retry-run").set()
        worker = _OneTickWorker(
            config=_drain_config(grace=1.0), handler=handler,
            dispatch_repo=repo, ticks=2,
        )
        _run_loop(worker)
        assert handler.outcomes == {"retry-run": "complete"}
        assert repo.fails == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))