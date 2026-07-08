"""Unit tests for DesktopBotLifecycle."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.desktop_bot.lifecycle import DesktopBotLifecycle, _PENDING_TIMEOUT
from agentclaw.community.core.desktop_bot.services.desktop_bot_service import (
    DesktopBotOrphanError,
    DesktopBotServiceError,
)
from agentclaw.community.core.desktop_bot.status_mapping import StatusDecision
from agentclaw.community.di.config import DesktopBotPeriodicScanConfig


def _full_rollout_cfg() -> DesktopBotPeriodicScanConfig:
    """Default test stance: full rollout (apply to everyone)."""
    return DesktopBotPeriodicScanConfig(
        enabled=True,
        apply_owner_whitelist=frozenset({"*"}),
        global_dry_run=False,
    )


def _make_lifecycle(scan_cfg: DesktopBotPeriodicScanConfig | None = None):
    bot_repo = MagicMock()
    svc = MagicMock()
    cache = MagicMock()
    cache.acquire_lock.return_value = "test-lock-token"
    cache.release_lock.return_value = True
    lifecycle = DesktopBotLifecycle(
        bot_repo=bot_repo,
        desktop_bot_service=svc,
        scan_config=scan_cfg or _full_rollout_cfg(),
        cache=cache,
    )
    return lifecycle, bot_repo, svc


def _make_pending_bot(bot_id, device_id="dev-1", owner_id="u001",
                      binding_id=1, gmt_create=None):
    return {
        "bot_id": bot_id,
        "device_id": device_id,
        "owner_id": owner_id,
        "binding_id": binding_id,
        "status": "PENDING",
        "gmt_create": gmt_create or datetime.now().isoformat(),
    }


class TestDesktopBotLifecycle:
    @pytest.mark.asyncio
    async def test_startup_recovers_pending_to_active(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot = _make_pending_bot("b1")
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.return_value = {
            "device_status": "ALL_ONLINE",
            "bot_status": "ACTIVE",
        }

        await lifecycle.startup()

        svc._apply_decision.assert_called_once()
        call_args = svc._apply_decision.call_args
        assert call_args[0][0] == "b1"  # bot_id
        assert call_args[0][3] == "PENDING"  # current_status
        decision = call_args[0][4]
        assert decision.target_status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_startup_recovers_pending_to_failed(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot = _make_pending_bot("b1")
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.return_value = {
            "device_status": "ALL_OFFLINE",
            "bot_status": "FAILED",
        }

        await lifecycle.startup()

        svc._apply_decision.assert_called_once()
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status == "FAILED"

    @pytest.mark.asyncio
    async def test_startup_timeout_marks_failed(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        old_time = (datetime.now() - _PENDING_TIMEOUT - timedelta(minutes=1)).isoformat()
        bot = _make_pending_bot("b1", gmt_create=old_time)
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.side_effect = DesktopBotServiceError("unreachable")

        await lifecycle.startup()

        svc._apply_decision.assert_called_once()
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status == "FAILED"
        assert decision.release_reason == "baas_unreachable_timeout"

    @pytest.mark.asyncio
    async def test_startup_skips_recent_pending(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot = _make_pending_bot("b1", gmt_create=datetime.now().isoformat())
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.return_value = {
            "device_status": "PROVISIONING",
            "bot_status": "PENDING",
        }

        await lifecycle.startup()

        # PENDING → no change, _apply_decision still called but with None target
        svc._apply_decision.assert_called_once()
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status is None

    @pytest.mark.asyncio
    async def test_startup_single_failure_continues(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot1 = _make_pending_bot("b1", device_id="dev-1")
        bot2 = _make_pending_bot("b2", device_id="dev-2")
        bot_repo.search_bots.return_value = (2, [bot1, bot2])
        svc.query_device_status.side_effect = [
            DesktopBotServiceError("BaaS error"),
            {"device_status": "ALL_ONLINE", "bot_status": "ACTIVE"},
        ]
        # bot1: unreachable but recent → skip
        # bot2: ALL_ONLINE → ACTIVE

        await lifecycle.startup()

        # Only bot2 gets _apply_decision (bot1 skipped as recent + unreachable)
        assert svc._apply_decision.call_count == 1
        call_args = svc._apply_decision.call_args[0]
        assert call_args[0] == "b2"
        assert call_args[4].target_status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_startup_no_pending_bots(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot_repo.search_bots.return_value = (0, [])

        await lifecycle.startup()

        svc.query_device_status.assert_not_called()
        svc._apply_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_startup_skips_bot_without_device_id(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot = _make_pending_bot("b1", device_id="")
        bot_repo.search_bots.return_value = (1, [bot])

        await lifecycle.startup()

        svc.query_device_status.assert_not_called()
        svc._apply_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_startup_orphan_404_marks_failed(self):
        """BaaS 404 + BOT_NOT_FOUND on PENDING → FAILED."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot = _make_pending_bot("b1")
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.side_effect = DesktopBotOrphanError("not found")

        await lifecycle.startup()

        svc._apply_decision.assert_called_once()
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status == "FAILED"
        assert decision.release_reason == "baas_orphan_404"

    @pytest.mark.asyncio
    async def test_startup_unreachable_recent_skips(self):
        """BaaS unreachable + recent bot → skip."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot = _make_pending_bot("b1", gmt_create=datetime.now().isoformat())
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.side_effect = DesktopBotServiceError("timeout")

        await lifecycle.startup()

        svc._apply_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_startup_baas_pending_no_change(self):
        """BaaS bot_status=PENDING → no status change."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot = _make_pending_bot("b1")
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.return_value = {
            "device_status": "",
            "bot_status": "PENDING",
        }

        await lifecycle.startup()

        svc._apply_decision.assert_called_once()
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status is None
