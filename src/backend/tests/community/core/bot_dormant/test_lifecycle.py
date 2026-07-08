"""Unit tests for DormantBotLifecycle.

Lifecycle tests:
  L1 - lock not acquired  → process_run not called, release_lock not called
  L2 - lock acquired      → process_run called, release_lock called with token
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.bot_dormant.lifecycle import DormantBotLifecycle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lifecycle(
    service=None,
    cache=None,
    scan_policy=None,
) -> DormantBotLifecycle:
    if service is None:
        service = MagicMock()
        service.is_dry_run.return_value = False
        service.process_run = AsyncMock(return_value=None)
    if cache is None:
        cache = MagicMock()
    if scan_policy is None:
        scan_policy = MagicMock()
        scan_policy.get_policy.return_value.scheduled_scan_enabled = True
        scan_policy.get_policy.return_value.dry_run = False
        scan_policy.get_policy.return_value.env = "test"
        scan_policy.get_policy.return_value.source = "test"
    # Patch get_current_env so tests are env-independent
    with patch("agentclaw.community.core.bot_dormant.lifecycle.get_current_env", return_value="test"):
        lc = DormantBotLifecycle(service, cache, scan_policy)
    return lc


# ---------------------------------------------------------------------------
# L1 — scan: lock not acquired → process_run skipped
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_skipped_when_lock_not_acquired():
    """L1: acquire_lock returns None → process_run not called, no release."""
    svc = MagicMock()
    svc.is_dry_run.return_value = False
    svc.process_run = AsyncMock(return_value=None)
    cache = MagicMock()
    cache.acquire_lock.return_value = None  # lock not available
    policy = MagicMock()
    policy.get_policy.return_value.scheduled_scan_enabled = True
    policy.get_policy.return_value.dry_run = False
    policy.get_policy.return_value.env = "prod"
    policy.get_policy.return_value.source = "common_config"

    lc = _make_lifecycle(service=svc, cache=cache, scan_policy=policy)
    lc._run_scan()

    svc.process_run.assert_not_called()
    cache.release_lock.assert_not_called()


# ---------------------------------------------------------------------------
# L2 — scan: lock acquired → process_run called, lock released
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_runs_when_lock_acquired():
    """L2: acquire_lock returns token → process_run called, release_lock with token."""
    svc = MagicMock()
    svc.is_dry_run.return_value = True
    svc.process_run = AsyncMock(return_value=None)
    cache = MagicMock()
    cache.acquire_lock.return_value = "token-abc"
    policy = MagicMock()
    policy.get_policy.return_value.scheduled_scan_enabled = True
    policy.get_policy.return_value.dry_run = True
    policy.get_policy.return_value.env = "prod"
    policy.get_policy.return_value.source = "common_config"

    lc = _make_lifecycle(service=svc, cache=cache, scan_policy=policy)
    lc._run_scan()

    svc.process_run.assert_called_once_with(dry_run=True)
    cache.release_lock.assert_called_once_with(lc._scan_lock, "token-abc")


@pytest.mark.unit
def test_scan_skipped_when_policy_disabled():
    """Policy disabled → do not acquire lock or call process_run."""
    svc = MagicMock()
    svc.process_run = AsyncMock(return_value=None)
    cache = MagicMock()
    policy = MagicMock()
    policy.get_policy.return_value.scheduled_scan_enabled = False
    policy.get_policy.return_value.dry_run = True
    policy.get_policy.return_value.env = "pre"
    policy.get_policy.return_value.source = "common_config"

    with patch("agentclaw.community.core.bot_dormant.lifecycle.get_current_env", return_value="pre"):
        lc = DormantBotLifecycle(svc, cache, policy)

    lc._run_scan()

    cache.acquire_lock.assert_not_called()
    svc.process_run.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_startup_starts_auto_scan_thread_outside_prod():
    """Non-prod starts the loop; policy decides at run time whether scan runs."""
    svc = MagicMock()
    cache = MagicMock()
    policy = MagicMock()
    with patch("agentclaw.community.core.bot_dormant.lifecycle.get_current_env", return_value="pre"):
        lc = DormantBotLifecycle(svc, cache, policy)

    with patch.object(lc, "_loop") as loop:
        await lc.startup()
        assert lc._scan_thread is not None
        lc._stop_event.set()
        lc._scan_thread.join(timeout=1)

    loop.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_startup_starts_auto_scan_in_prod():
    """Prod still starts the 03:00 cron thread."""
    svc = MagicMock()
    cache = MagicMock()
    policy = MagicMock()
    with patch("agentclaw.community.core.bot_dormant.lifecycle.get_current_env", return_value="prod"):
        lc = DormantBotLifecycle(svc, cache, policy)

    with patch.object(lc, "_loop") as loop:
        await lc.startup()
        assert lc._scan_thread is not None
        lc._stop_event.set()
        lc._scan_thread.join(timeout=1)

    loop.assert_called_once()
