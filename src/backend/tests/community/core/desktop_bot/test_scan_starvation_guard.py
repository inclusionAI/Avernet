"""Starvation guard: a stuck throttle timestamp must NOT skip scans forever.

Root cause of the 2026-06-08 87-minute outage: the throttle reads a shared
``_SCAN_TS_KEY`` and skips when it looks "< interval old". If that timestamp
gets wedged at a near-now value (clock skew / replication lag / concurrent
writes in the shared GZone cache), every round skips with no upper bound and
no alert — the scan starves indefinitely until a manual restart.

The guard: after ``_MAX_CONSECUTIVE_THROTTLE_SKIPS`` consecutive throttle-skips,
force one scan through regardless, then reset the counter so it re-arms.

These tests pin that behavior deterministically:
  - a FIXED virtual clock, so the throttle's ``now - last < interval`` check is
    ALWAYS True (faithful "wedged timestamp") without depending on wall time;
  - a SMALL injected threshold, so the guard fires in a handful of rounds
    instead of millions.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import agentclaw.community.core.desktop_bot.lifecycle as lifecycle_mod
from agentclaw.community.core.desktop_bot.lifecycle import DesktopBotLifecycle
from agentclaw.community.di.config import DesktopBotPeriodicScanConfig

_FROZEN_NOW = datetime(2026, 6, 8, 14, 0, 0)


def _cfg() -> DesktopBotPeriodicScanConfig:
    return DesktopBotPeriodicScanConfig(
        enabled=True,
        apply_owner_whitelist=frozenset({"*"}),
        global_dry_run=False,
    )


def _make_lifecycle_with_wedged_throttle():
    """Lifecycle whose cache always grants the lock and reports 'scanned now'.

    Returns (lifecycle, scan_calls). ``scan_calls[0]`` counts how many times
    ``_scan_all_bots`` actually ran.
    """
    cache = MagicMock()
    cache.acquire_lock.return_value = "tok"
    cache.release_lock.return_value = True
    # Wedged timestamp: always the frozen "now" → with the clock also frozen,
    # ``now - last == 0 < interval`` forever, so the throttle never ages out.
    cache.get.return_value = _FROZEN_NOW.isoformat()

    lc = DesktopBotLifecycle(
        bot_repo=MagicMock(),
        desktop_bot_service=MagicMock(),
        scan_config=_cfg(),
        cache=cache,
    )
    scan_calls = [0]

    async def _fake_scan_all() -> None:
        scan_calls[0] += 1

    lc._scan_all_bots = _fake_scan_all  # type: ignore[assignment]
    return lc, scan_calls


@pytest.mark.asyncio
async def test_wedged_throttle_forces_scan_after_threshold():
    """First N rounds skip; round N+1 forces a scan (guard fires)."""
    lc, scan_calls = _make_lifecycle_with_wedged_throttle()

    with patch.object(lifecycle_mod, "_MAX_CONSECUTIVE_THROTTLE_SKIPS", 2), \
            patch.object(lifecycle_mod, "datetime") as dt:
        dt.now.return_value = _FROZEN_NOW
        dt.fromisoformat.side_effect = datetime.fromisoformat

        # Rounds 1, 2: throttled, no scan yet.
        await lc._guarded_scan()
        assert scan_calls[0] == 0
        await lc._guarded_scan()
        assert scan_calls[0] == 0
        # Round 3: consecutive skips (3) > threshold (2) → forced scan.
        await lc._guarded_scan()
        assert scan_calls[0] == 1


@pytest.mark.asyncio
async def test_forced_scan_resets_counter_so_guard_rearms():
    """After a forced scan the counter resets; the guard fires again, not every round."""
    lc, scan_calls = _make_lifecycle_with_wedged_throttle()

    with patch.object(lifecycle_mod, "_MAX_CONSECUTIVE_THROTTLE_SKIPS", 2), \
            patch.object(lifecycle_mod, "datetime") as dt:
        dt.now.return_value = _FROZEN_NOW
        dt.fromisoformat.side_effect = datetime.fromisoformat

        # 9 rounds with threshold 2 → forced scan on rounds 3, 6, 9 → exactly 3.
        for _ in range(9):
            await lc._guarded_scan()

    assert scan_calls[0] == 3, (
        f"expected 3 forced scans over 9 rounds (every 3rd), got {scan_calls[0]}"
    )


@pytest.mark.asyncio
async def test_no_guard_means_indefinite_starvation():
    """Sanity: WITHOUT the guard (huge threshold), a wedged throttle never scans.

    This proves the test setup faithfully reproduces starvation — the guard is
    what breaks it, not test-clock drift.
    """
    lc, scan_calls = _make_lifecycle_with_wedged_throttle()

    with patch.object(lifecycle_mod, "_MAX_CONSECUTIVE_THROTTLE_SKIPS", 10_000), \
            patch.object(lifecycle_mod, "datetime") as dt:
        dt.now.return_value = _FROZEN_NOW
        dt.fromisoformat.side_effect = datetime.fromisoformat

        for _ in range(50):
            await lc._guarded_scan()

    assert scan_calls[0] == 0, "wedged throttle below threshold must starve (zero scans)"
