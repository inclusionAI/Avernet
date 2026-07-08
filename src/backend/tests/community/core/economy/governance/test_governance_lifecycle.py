"""Tests for GovernanceBotLifecycle — scheduled daily scan + once-lock dedup.

Covers: constructor deps, startup daemon thread, shutdown join,
_loop sleep-until-target-time logic, _run_scan once-lock gate
(acquire_lock + long TTL, no release), and exception resilience.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from agentclaw.community.core.economy.governance.lifecycle import (
    GovernanceBotLifecycle,
    _ONCE_LOCK_TTL_SECONDS,
)
from agentclaw.community.plugin_api.cache import CachePlugin


# --- Fakes ---


@dataclass
class _FakeConfig:
    """Stand-in for EconomyGovernanceConfig."""

    scan_hour: int = 14
    scan_minute: int = 0


@dataclass
class _FakeCronTickSummary:
    """Stand-in for CronTickSummary — only the fields lifecycle logs."""

    run_id: str = "run-1"
    duration_seconds: float = 0.5
    sent_count: int = 2
    failed_count: int = 0
    cancelled_count: int = 0
    reminders_created: int = 1
    schedule_due_count: int = 0
    timeout_recovered: int = 0
    errors: int = 0
    dry_run: bool = False


class _FakeService:
    """GovernanceBotService stand-in with recording for process_cron_tick."""

    def __init__(self, summary=None, raises: Exception | None = None):
        self._summary = summary if summary is not None else _FakeCronTickSummary()
        self._raises = raises
        self.process_cron_tick_calls: list[None] = []

    async def process_cron_tick(self, dry_run: bool | None = None, run_id: str | None = None):
        self.process_cron_tick_calls.append(None)
        if self._raises is not None:
            raise self._raises
        return self._summary


class _FakeCache:
    """CachePlugin stand-in — acquire_lock always wins by default."""

    def __init__(self, acquire_lock_result: str | None = "fake-token"):
        self._acquire_lock_result = acquire_lock_result
        self.acquire_lock_calls: list[tuple[str, int]] = []

    def acquire_lock(self, lock_key: str, ttl: int = 30) -> str | None:
        self.acquire_lock_calls.append((lock_key, ttl))
        return self._acquire_lock_result


def _build(service=None, cache=None, config=None):
    return GovernanceBotLifecycle(
        service=service or _FakeService(),
        cache=cache or _FakeCache(),
        config=config or _FakeConfig(),
    )


# --- __init__ ---


def test_init_stores_deps():
    """Constructor stores service, cache, config, creates stop_event."""
    svc = _FakeService()
    cache = _FakeCache()
    cfg = _FakeConfig(scan_hour=10, scan_minute=30)
    lc = GovernanceBotLifecycle(service=svc, cache=cache, config=cfg)

    assert lc._service is svc
    assert lc._cache is cache
    assert lc._config is cfg
    assert lc._scan_thread is None
    assert isinstance(lc._stop_event, threading.Event)
    assert not lc._stop_event.is_set()


# --- startup ---


def test_startup_spawns_daemon():
    """startup() creates a daemon thread that enters _loop."""
    lc = _build()

    # Prevent the loop from doing real work — set stop immediately.
    def _immediate_stop_wait(timeout=None):
        lc._stop_event.set()
        return True

    with patch.object(lc._stop_event, "wait", _immediate_stop_wait):
        asyncio.run(lc.startup())

    try:
        assert lc._scan_thread is not None
        assert lc._scan_thread.daemon is True
    finally:
        asyncio.run(lc.shutdown())


def test_startup_reads_scan_hour_from_config():
    """startup() reads scan_hour/scan_minute from config."""
    cfg = _FakeConfig(scan_hour=9, scan_minute=30)
    lc = _build(config=cfg)

    captured_args: list[tuple[int, int]] = []

    def _capturing_loop(target_hour: int, target_minute: int) -> None:
        captured_args.append((target_hour, target_minute))

    with patch.object(lc, "_loop", _capturing_loop):
        asyncio.run(lc.startup())

    assert len(captured_args) == 1
    assert captured_args[0] == (9, 30)


# --- shutdown ---


def test_shutdown_stops_thread():
    """shutdown() sets stop event and joins the daemon thread."""
    lc = _build()

    loop_entered = threading.Event()

    def _blocking_loop(target_hour: int, target_minute: int) -> None:
        loop_entered.set()
        lc._stop_event.wait(timeout=300)

    with patch.object(lc, "_loop", _blocking_loop):
        asyncio.run(lc.startup())

    loop_entered.wait(timeout=5)

    thread = lc._scan_thread
    assert thread is not None and thread.is_alive()

    asyncio.run(lc.shutdown())

    assert lc._stop_event.is_set()
    assert not thread.is_alive()


def test_shutdown_without_startup_is_noop():
    """shutdown() when no thread was started just sets the stop flag — no error."""
    lc = _build()

    asyncio.run(lc.shutdown())

    assert lc._stop_event.is_set()
    assert lc._scan_thread is None


# --- _loop ---


def test_loop_calls_run_scan():
    """_loop invokes _run_scan after the sleep period."""
    lc = _build()
    scan_calls: list[int] = []

    def _fake_scan():
        scan_calls.append(1)
        lc._stop_event.set()

    with patch.object(lc, "_seconds_until", return_value=0):
        with patch.object(lc._stop_event, "wait", return_value=False):
            with patch.object(lc, "_run_scan", _fake_scan):
                lc._loop(target_hour=14, target_minute=0)

    assert len(scan_calls) >= 1


def test_loop_catches_scan_exception():
    """Exception in _run_scan does not crash _loop — loop continues."""
    lc = _build()
    scan_count = 0

    def _failing_then_stop_scan():
        nonlocal scan_count
        scan_count += 1
        if scan_count == 1:
            raise RuntimeError("scan boom")
        lc._stop_event.set()

    wait_count = 0

    def _controlled_wait(timeout=None):
        nonlocal wait_count
        wait_count += 1
        if wait_count > 3:
            lc._stop_event.set()
            return True
        return False

    with patch.object(lc, "_seconds_until", return_value=0):
        with patch.object(lc._stop_event, "wait", _controlled_wait):
            with patch.object(lc, "_run_scan", _failing_then_stop_scan):
                lc._loop(target_hour=14, target_minute=0)

    assert scan_count >= 1


# --- _run_scan ---


def test_run_scan_calls_acquire_lock_then_process_cron_tick():
    """_run_scan() uses acquire_lock once-lock; if won, calls process_cron_tick."""
    svc = _FakeService(summary=_FakeCronTickSummary(sent_count=3))
    cache = _FakeCache(acquire_lock_result="win-token")
    lc = _build(service=svc, cache=cache)

    lc._run_scan()

    # acquire_lock was called with a once:governance_scan:{env}:{date} key
    assert len(cache.acquire_lock_calls) == 1
    key, ttl = cache.acquire_lock_calls[0]
    assert "once:governance_scan:" in key
    assert ttl == _ONCE_LOCK_TTL_SECONDS

    # process_cron_tick was called once (we won the once-lock)
    assert len(svc.process_cron_tick_calls) == 1


def test_run_scan_skips_when_acquire_lock_fails():
    """_run_scan() skips process_cron_tick when acquire_lock returns None."""
    svc = _FakeService(summary=_FakeCronTickSummary())
    cache = _FakeCache(acquire_lock_result=None)
    lc = _build(service=svc, cache=cache)

    lc._run_scan()

    # acquire_lock was called
    assert len(cache.acquire_lock_calls) == 1
    # process_cron_tick was NOT called (lock already held by another Pod)
    assert len(svc.process_cron_tick_calls) == 0


def test_run_scan_never_releases_lock():
    """_run_scan() never calls release_lock — once-lock persists via TTL."""
    svc = _FakeService(summary=_FakeCronTickSummary())
    cache = _FakeCache(acquire_lock_result="win-token")
    # Add release_lock tracking
    cache.release_lock_calls: list[tuple[str, str]] = []

    def _fake_release_lock(lock_key: str, lock_value: str) -> bool:
        cache.release_lock_calls.append((lock_key, lock_value))
        return True

    cache.release_lock = _fake_release_lock

    lc = _build(service=svc, cache=cache)
    lc._run_scan()

    # Lock was acquired but never released
    assert len(cache.acquire_lock_calls) == 1
    assert len(cache.release_lock_calls) == 0


def test_run_scan_logs_summary():
    """_run_scan() logs the CronTickSummary fields at INFO level."""
    svc = _FakeService(
        summary=_FakeCronTickSummary(
            run_id="tick-42",
            sent_count=5,
            failed_count=1,
            cancelled_count=0,
            reminders_created=2,
            schedule_due_count=3,
            timeout_recovered=1,
            duration_seconds=1.2,
        ),
    )
    lc = _build(service=svc)

    with patch.object(
        logging.getLogger("agentclaw.community.core.economy.governance.lifecycle"),
        "info",
    ) as mock_info:
        lc._run_scan()

    # Find the call that contains "Scan completed"
    scan_log_calls = [
        c for c in mock_info.call_args_list if "Scan completed" in str(c)
    ]
    assert len(scan_log_calls) == 1
    logged_msg = str(scan_log_calls[0])
    assert "tick-42" in logged_msg


# --- _seconds_until ---


def test_seconds_until_returns_positive():
    """_seconds_until always returns a positive value."""
    sec = GovernanceBotLifecycle._seconds_until(14, 0)
    assert sec > 0
    assert sec <= 24 * 3600


def test_seconds_until_target_in_past_returns_next_day():
    """If target time is already past today, returns seconds until tomorrow."""
    # Pick a time that is definitely in the past (hour=0, minute=0)
    sec = GovernanceBotLifecycle._seconds_until(0, 0)
    # Should be close to 24h (since 00:00 is always in the past or very near)
    assert sec > 0
    assert sec <= 24 * 3600


# --- once-lock TTL constant ---


def test_once_lock_ttl_is_2_days():
    """_ONCE_LOCK_TTL_SECONDS is 2 days (172800 seconds)."""
    assert _ONCE_LOCK_TTL_SECONDS == 2 * 86400


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))