"""Unit tests for DesktopBotLifecycle periodic health scan."""
import asyncio
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.desktop_bot.lifecycle import (
    DesktopBotLifecycle,
    _SCAN_BATCH_SIZE,
    _SCAN_SKIP_IF_CHECKED_WITHIN_SECONDS,
)
from agentclaw.community.core.desktop_bot.services.desktop_bot_service import (
    DesktopBotOrphanError,
    DesktopBotServiceError,
)
from agentclaw.community.di.config import DesktopBotPeriodicScanConfig


def _cfg(
    *,
    enabled: bool = True,
    whitelist: frozenset[str] = frozenset({"*"}),
    global_dry_run: bool = False,
) -> DesktopBotPeriodicScanConfig:
    return DesktopBotPeriodicScanConfig(
        enabled=enabled,
        apply_owner_whitelist=whitelist,
        global_dry_run=global_dry_run,
    )


def _make_lifecycle(
    scan_cfg: DesktopBotPeriodicScanConfig | None = None,
    cache: MagicMock | None = None,
):
    """Default lifecycle stance: full rollout (apply to ALL).

    Runtime default is safe (NOBODY applied) but most tests assert apply
    behavior — they get the full-rollout config unless they pass their own.

    Default cache mock always grants the lock (acquire_lock returns a token),
    so existing scan-behavior tests run the scan exactly as before.
    """
    bot_repo = MagicMock()
    svc = MagicMock()
    if cache is None:
        cache = MagicMock()
        cache.acquire_lock.return_value = "test-lock-token"
        cache.release_lock.return_value = True
        cache.get.return_value = None  # no last_scan_ts → scan proceeds
    lifecycle = DesktopBotLifecycle(
        bot_repo=bot_repo,
        desktop_bot_service=svc,
        scan_config=scan_cfg or _cfg(),
        cache=cache,
    )
    return lifecycle, bot_repo, svc


def _make_bot(bot_id, status="ACTIVE", device_id="dev-1", owner_id="u001",
              binding_id=1, ext=None, gmt_create=None):
    return {
        "bot_id": bot_id,
        "device_id": device_id,
        "status": status,
        "owner_id": owner_id,
        "binding_id": binding_id,
        "ext": ext or {},
        "gmt_create": gmt_create or datetime.now().isoformat(),
    }


class TestPeriodicScanAllBots:
    @pytest.mark.asyncio
    async def test_scan_processes_non_terminal_bots(self):
        """Periodic scan checks ACTIVE/OFFLINE/PENDING/RELEASING/FAILED, skips RELEASED."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bots = [
            _make_bot("b1", status="ACTIVE"),
            _make_bot("b2", status="RELEASED"),  # should be skipped
            _make_bot("b3", status="OFFLINE"),
        ]
        bot_repo.search_bots.return_value = (3, bots)
        svc.query_device_status.return_value = {
            "device_status": "ALL_ONLINE", "bot_status": "ACTIVE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        # b1 and b3 checked, b2 (RELEASED) skipped
        assert svc.query_device_status.call_count == 2
        assert svc._apply_decision.call_count == 2

    @pytest.mark.asyncio
    async def test_scan_paginates_large_bot_sets(self):
        """When bots exceed BATCH_SIZE, scan fetches next page."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        page1 = [_make_bot(f"b{i}") for i in range(_SCAN_BATCH_SIZE)]
        page2 = [_make_bot(f"b{_SCAN_BATCH_SIZE + i}") for i in range(3)]
        bot_repo.search_bots.side_effect = [
            (_SCAN_BATCH_SIZE, page1),
            (3, page2),
        ]
        svc.query_device_status.return_value = {
            "device_status": "ALL_ONLINE", "bot_status": "ACTIVE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        # All bots from both pages checked
        assert svc.query_device_status.call_count == _SCAN_BATCH_SIZE + 3
        assert bot_repo.search_bots.call_count == 2

    @pytest.mark.asyncio
    async def test_scan_skips_recently_checked_bots(self):
        """Bots with ext.last_health_check within skip window are skipped."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        recent_check = datetime.now().isoformat()
        old_check = (
            datetime.now() - timedelta(seconds=_SCAN_SKIP_IF_CHECKED_WITHIN_SECONDS + 60)
        ).isoformat()
        bots = [
            _make_bot("b1", ext={"last_health_check": recent_check}),  # skip
            _make_bot("b2", ext={"last_health_check": old_check}),     # check
            _make_bot("b3", ext={}),                                     # check (no timestamp)
        ]
        bot_repo.search_bots.return_value = (3, bots)
        svc.query_device_status.return_value = {
            "device_status": "ALL_ONLINE", "bot_status": "ACTIVE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        # b1 skipped (recent), b2 and b3 checked
        assert svc.query_device_status.call_count == 2

    @pytest.mark.asyncio
    async def test_scan_single_failure_continues(self):
        """One bot query failure doesn't stop the scan."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bots = [
            _make_bot("b1"),
            _make_bot("b2"),
        ]
        bot_repo.search_bots.return_value = (2, bots)
        svc.query_device_status.side_effect = [
            DesktopBotServiceError("timeout"),
            {"device_status": "ALL_ONLINE", "bot_status": "ACTIVE"},
        ]

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        # b1 failed but b2 still checked
        assert svc._apply_decision.call_count == 1

    @pytest.mark.asyncio
    async def test_scan_detects_orphan(self):
        """Periodic scan detects orphan bots via 404."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bots = [_make_bot("b1", status="ACTIVE")]
        bot_repo.search_bots.return_value = (1, bots)
        svc.query_device_status.side_effect = DesktopBotOrphanError("not found")

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        svc._apply_decision.assert_called_once()
        call_args = svc._apply_decision.call_args[0]
        decision = call_args[4]
        assert decision.target_status == "RELEASED"
        assert decision.soft_delete is True

    @pytest.mark.asyncio
    async def test_scan_skips_bots_without_device_id(self):
        """Bots without device_id are skipped."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bots = [_make_bot("b1", device_id="")]
        bot_repo.search_bots.return_value = (1, bots)

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        svc.query_device_status.assert_not_called()


class TestPeriodicLifecycle:
    @pytest.mark.asyncio
    async def test_shutdown_stops_periodic_task(self):
        """shutdown() cancels the periodic loop."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot_repo.search_bots.return_value = (0, [])

        await lifecycle.startup()
        assert lifecycle._running is True
        assert lifecycle._periodic_task is not None

        await lifecycle.shutdown()
        assert lifecycle._running is False
        assert lifecycle._periodic_task.cancelled() or lifecycle._periodic_task.done()

    @pytest.mark.asyncio
    async def test_startup_starts_periodic_task(self):
        """startup() creates a background periodic task."""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot_repo.search_bots.return_value = (0, [])

        await lifecycle.startup()

        assert lifecycle._running is True
        assert lifecycle._periodic_task is not None
        assert not lifecycle._periodic_task.done()

        # Cleanup
        await lifecycle.shutdown()


class TestRecentlyChecked:
    def test_recent_timestamp_returns_true(self):
        lifecycle, _, _ = _make_lifecycle()
        now = datetime.now()
        recent = (now - timedelta(seconds=60)).isoformat()
        assert lifecycle._recently_checked(recent, now) is True

    def test_old_timestamp_returns_false(self):
        lifecycle, _, _ = _make_lifecycle()
        now = datetime.now()
        old = (now - timedelta(seconds=_SCAN_SKIP_IF_CHECKED_WITHIN_SECONDS + 60)).isoformat()
        assert lifecycle._recently_checked(old, now) is False

    def test_invalid_timestamp_returns_false(self):
        lifecycle, _, _ = _make_lifecycle()
        assert lifecycle._recently_checked("not-a-date", datetime.now()) is False

    def test_empty_string_returns_false(self):
        lifecycle, _, _ = _make_lifecycle()
        assert lifecycle._recently_checked("", datetime.now()) is False


class TestKillSwitches:
    @pytest.mark.asyncio
    async def test_disabled_in_config_skips_task_creation(self):
        """enabled=False → startup runs recovery only, periodic task not started."""
        lifecycle, bot_repo, _ = _make_lifecycle(_cfg(enabled=False))
        bot_repo.search_bots.return_value = (0, [])

        await lifecycle.startup()

        assert lifecycle._running is False
        assert lifecycle._periodic_task is None

    @pytest.mark.asyncio
    async def test_global_dry_run_queries_but_never_applies(self):
        """global_dry_run=True → every bot queried, none applied."""
        lifecycle, bot_repo, svc = _make_lifecycle(
            _cfg(whitelist=frozenset({"*"}), global_dry_run=True),
        )
        bots = [
            _make_bot("b1", owner_id="u1", status="ACTIVE"),
            _make_bot("b2", owner_id="u2", status="ACTIVE"),
        ]
        bot_repo.search_bots.return_value = (2, bots)
        svc.query_device_status.return_value = {
            "device_status": "ALL_OFFLINE", "bot_status": "ACTIVE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        assert svc.query_device_status.call_count == 2
        svc._apply_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitelist_applies_inside_logs_outside(self):
        """Whitelist scans EVERYONE; only whitelisted owners get real changes,
        others go through log-only path."""
        lifecycle, bot_repo, svc = _make_lifecycle(
            _cfg(whitelist=frozenset({"u1"})),
        )
        bots = [
            _make_bot("b1", owner_id="u1"),  # in whitelist → apply
            _make_bot("b2", owner_id="u2"),  # outside      → log only
            _make_bot("b3", owner_id="u3"),  # outside      → log only
        ]
        bot_repo.search_bots.return_value = (3, bots)
        svc.query_device_status.return_value = {
            "device_status": "ALL_OFFLINE", "bot_status": "ACTIVE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        # all three queried (visibility for everyone)
        assert svc.query_device_status.call_count == 3
        # only the whitelisted one was actually applied
        assert svc._apply_decision.call_count == 1
        assert svc._apply_decision.call_args[0][0] == "b1"

    @pytest.mark.asyncio
    async def test_empty_whitelist_is_log_only_for_everyone(self):
        """Safe default: empty whitelist → NOBODY applied, all log-only."""
        lifecycle, bot_repo, svc = _make_lifecycle(
            _cfg(whitelist=frozenset()),
        )
        bots = [
            _make_bot("b1", owner_id="u1"),
            _make_bot("b2", owner_id="u2"),
        ]
        bot_repo.search_bots.return_value = (2, bots)
        svc.query_device_status.return_value = {
            "device_status": "ALL_OFFLINE", "bot_status": "ACTIVE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        assert svc.query_device_status.call_count == 2
        svc._apply_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitelist_star_sentinel_applies_everyone(self):
        """Whitelist contains "*" → full rollout, every owner gets applied."""
        lifecycle, bot_repo, svc = _make_lifecycle(
            _cfg(whitelist=frozenset({"*"})),
        )
        bots = [
            _make_bot("b1", owner_id="u1"),
            _make_bot("b2", owner_id="u2"),
        ]
        bot_repo.search_bots.return_value = (2, bots)
        svc.query_device_status.return_value = {
            "device_status": "ALL_ONLINE", "bot_status": "ACTIVE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        assert svc._apply_decision.call_count == 2

    @pytest.mark.asyncio
    async def test_global_dry_run_overrides_whitelist(self):
        """global_dry_run wins even if whitelist would otherwise apply."""
        lifecycle, bot_repo, svc = _make_lifecycle(
            _cfg(whitelist=frozenset({"u1"}), global_dry_run=True),
        )
        bots = [_make_bot("b1", owner_id="u1")]
        bot_repo.search_bots.return_value = (1, bots)
        svc.query_device_status.return_value = {
            "device_status": "ALL_OFFLINE", "bot_status": "ACTIVE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        svc._apply_decision.assert_not_called()


class TestSingleInstanceGuard:
    @pytest.mark.asyncio
    async def test_scan_runs_when_lock_acquired(self):
        cache = MagicMock()
        cache.acquire_lock.return_value = "tok-1"
        cache.release_lock.return_value = True
        lifecycle, bot_repo, svc = _make_lifecycle(cache=cache)
        # one empty page so _scan_all_bots returns quickly
        bot_repo.search_bots.return_value = (0, [])

        await lifecycle._guarded_scan()

        cache.acquire_lock.assert_called_once()
        assert bot_repo.search_bots.called  # scan actually ran
        cache.release_lock.assert_called_once_with(lifecycle._scan_lock_key, "tok-1")

    @pytest.mark.asyncio
    async def test_scan_skipped_when_lock_held_by_other(self):
        cache = MagicMock()
        cache.acquire_lock.return_value = None  # another worker holds it
        lifecycle, bot_repo, svc = _make_lifecycle(cache=cache)

        await lifecycle._guarded_scan()

        cache.acquire_lock.assert_called_once()
        assert not bot_repo.search_bots.called  # scan did NOT run
        assert not cache.release_lock.called

    @pytest.mark.asyncio
    async def test_lock_released_even_if_scan_raises(self):
        cache = MagicMock()
        cache.acquire_lock.return_value = "tok-2"
        cache.release_lock.return_value = True
        lifecycle, bot_repo, svc = _make_lifecycle(cache=cache)
        bot_repo.search_bots.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await lifecycle._guarded_scan()

        cache.release_lock.assert_called_once_with(lifecycle._scan_lock_key, "tok-2")

    @pytest.mark.asyncio
    async def test_lock_acquire_failure_skips_round_safely(self):
        cache = MagicMock()
        cache.acquire_lock.side_effect = RuntimeError("zcache down")
        lifecycle, bot_repo, svc = _make_lifecycle(cache=cache)

        # _guarded_scan should swallow the acquire error and not scan
        await lifecycle._guarded_scan()

        assert not bot_repo.search_bots.called
        assert not cache.release_lock.called


class TestScanThrottle:
    """Lock held → still throttle by last_scan_ts so the global scan
    frequency stays ~interval even though workers hold staggered timers."""

    @pytest.mark.asyncio
    async def test_skips_scan_when_last_scan_too_recent(self):
        recent = datetime.now().isoformat()
        cache = MagicMock()
        cache.acquire_lock.return_value = "tok"
        cache.release_lock.return_value = True
        cache.get.return_value = recent  # scanned just now
        lifecycle, bot_repo, svc = _make_lifecycle(cache=cache)

        await lifecycle._guarded_scan()

        # lock acquired but scan skipped because too recent
        cache.acquire_lock.assert_called_once()
        cache.get.assert_called_once_with(lifecycle._scan_ts_key)
        assert not bot_repo.search_bots.called  # did NOT scan
        # lock released so others aren't blocked
        cache.release_lock.assert_called_once()
        # did NOT overwrite the timestamp
        assert not cache.set.called

    @pytest.mark.asyncio
    async def test_scans_when_last_scan_old_and_stamps_ts(self):
        from agentclaw.community.core.desktop_bot.lifecycle import (
            _SCAN_INTERVAL_MINUTES,
        )
        old = (
            datetime.now() - timedelta(minutes=_SCAN_INTERVAL_MINUTES + 1)
        ).isoformat()
        cache = MagicMock()
        cache.acquire_lock.return_value = "tok"
        cache.release_lock.return_value = True
        cache.get.return_value = old
        lifecycle, bot_repo, svc = _make_lifecycle(cache=cache)
        bot_repo.search_bots.return_value = (0, [])

        await lifecycle._guarded_scan()

        assert bot_repo.search_bots.called  # scanned
        cache.set.assert_called_once()  # stamped new ts
        assert cache.set.call_args[0][0] == lifecycle._scan_ts_key
        cache.release_lock.assert_called_once()

    @pytest.mark.asyncio
    async def test_scans_when_no_last_scan_ts(self):
        cache = MagicMock()
        cache.acquire_lock.return_value = "tok"
        cache.release_lock.return_value = True
        cache.get.return_value = None  # first run, never scanned
        lifecycle, bot_repo, svc = _make_lifecycle(cache=cache)
        bot_repo.search_bots.return_value = (0, [])

        await lifecycle._guarded_scan()

        assert bot_repo.search_bots.called  # scanned
        cache.set.assert_called_once()  # stamped ts

    @pytest.mark.asyncio
    async def test_scans_when_last_scan_ts_malformed(self):
        cache = MagicMock()
        cache.acquire_lock.return_value = "tok"
        cache.release_lock.return_value = True
        cache.get.return_value = "not-a-timestamp"  # corrupt → treat as scannable
        lifecycle, bot_repo, svc = _make_lifecycle(cache=cache)
        bot_repo.search_bots.return_value = (0, [])

        await lifecycle._guarded_scan()

        assert bot_repo.search_bots.called  # scanned despite bad ts
        cache.set.assert_called_once()


class TestPendingTransitionProtection:
    """重启过渡态:扫描捞到 PENDING bot 时,正常过渡(设备暂离线)保持 PENDING,
    但 ext.pending_since 超过 _PENDING_TIMEOUT 仍 PENDING+离线 → 判 OFFLINE 兜底。

    关键:超时基准是 ext.pending_since(本次进入 PENDING 的时刻),不是 gmt_create
    (bot 创建时刻)。老 bot 重启时 gmt_create 早已超 10min,若用它会立即误判 OFFLINE。
    """

    @pytest.mark.asyncio
    async def test_recent_pending_offline_stays_pending(self):
        """刚重启的 PENDING bot,设备 ALL_OFFLINE → 保持 PENDING,不打 OFFLINE。"""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot = _make_bot(
            "b1", status="PENDING",
            ext={"pending_since": datetime.now().isoformat()},  # 刚进入 PENDING
        )
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_OFFLINE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        svc._apply_decision.assert_called_once()
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status is None  # 保持 PENDING,无状态变更

    @pytest.mark.asyncio
    async def test_old_bot_restart_stays_pending(self):
        """老 bot(创建于很久前)重启:gmt_create 早超 10min,但 pending_since 是刚刚,
        必须保持 PENDING——这是用户报告的核心 bug:老 bot 重启不该秒变 OFFLINE。"""
        lifecycle, bot_repo, svc = _make_lifecycle()
        long_ago = (datetime.now() - timedelta(days=7)).isoformat()
        bot = _make_bot(
            "b1", status="PENDING",
            gmt_create=long_ago,  # 7 天前创建的老 bot
            ext={"pending_since": datetime.now().isoformat()},  # 刚刚重启
        )
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_OFFLINE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        svc._apply_decision.assert_called_once()
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status is None  # 老 bot 重启也保持 PENDING

    @pytest.mark.asyncio
    async def test_stale_pending_offline_becomes_offline(self):
        """pending_since 超 10min 仍 ALL_OFFLINE → 判 OFFLINE 兜底(避免永久卡 PENDING)。"""
        from agentclaw.community.core.desktop_bot.lifecycle import _PENDING_TIMEOUT
        old = (datetime.now() - _PENDING_TIMEOUT - timedelta(minutes=1)).isoformat()
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot = _make_bot("b1", status="PENDING", ext={"pending_since": old})
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_OFFLINE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        svc._apply_decision.assert_called_once()
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status == "OFFLINE"

    @pytest.mark.asyncio
    async def test_missing_pending_since_stays_pending(self):
        """老数据没有 ext.pending_since → 保守视为未超时,保持 PENDING 不误判。"""
        lifecycle, bot_repo, svc = _make_lifecycle()
        long_ago = (datetime.now() - timedelta(days=7)).isoformat()
        bot = _make_bot(
            "b1", status="PENDING",
            gmt_create=long_ago,
            ext={},  # 没有 pending_since
        )
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_OFFLINE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        svc._apply_decision.assert_called_once()
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status is None  # 缺失=不超时,保持 PENDING

    @pytest.mark.asyncio
    async def test_recent_pending_online_becomes_active(self):
        """重启完成:PENDING + 设备 ALL_ONLINE → 正常转 ACTIVE。"""
        lifecycle, bot_repo, svc = _make_lifecycle()
        bot = _make_bot(
            "b1", status="PENDING",
            ext={"pending_since": datetime.now().isoformat()},
        )
        bot_repo.search_bots.return_value = (1, [bot])
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_ONLINE",
        }

        with patch("agentclaw.community.core.desktop_bot.lifecycle.asyncio.sleep"):
            await lifecycle._scan_all_bots()

        svc._apply_decision.assert_called_once()
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status == "ACTIVE"



class TestNeedsCheck:
    """_needs_check: 纯内存判断 bot 是否需要查 BaaS(并发前的同步过滤)。"""

    def test_released_bot_skipped(self):
        lifecycle, _, _ = _make_lifecycle()
        bot = _make_bot("b1", status="RELEASED")
        assert lifecycle._needs_check(bot, datetime.now()) is False

    def test_no_device_id_skipped(self):
        lifecycle, _, _ = _make_lifecycle()
        bot = _make_bot("b1", device_id="")
        assert lifecycle._needs_check(bot, datetime.now()) is False

    def test_recently_checked_skipped(self):
        lifecycle, _, _ = _make_lifecycle()
        recent = datetime.now().isoformat()
        bot = _make_bot("b1", ext={"last_health_check": recent})
        assert lifecycle._needs_check(bot, datetime.now()) is False

    def test_normal_active_bot_needs_check(self):
        lifecycle, _, _ = _make_lifecycle()
        bot = _make_bot("b1", status="ACTIVE")
        assert lifecycle._needs_check(bot, datetime.now()) is True

    def test_old_health_check_needs_check(self):
        lifecycle, _, _ = _make_lifecycle()
        old = (datetime.now() - timedelta(seconds=_SCAN_SKIP_IF_CHECKED_WITHIN_SECONDS + 60)).isoformat()
        bot = _make_bot("b1", ext={"last_health_check": old})
        assert lifecycle._needs_check(bot, datetime.now()) is True


class TestCheckOneBot:
    """_check_one_bot: 单个 bot 的查询→决策→应用,返回 tally tag。"""

    @pytest.mark.asyncio
    async def test_active_bot_applied(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_ONLINE",
        }
        bot = _make_bot("b1", status="OFFLINE")
        sem = asyncio.Semaphore(20)

        tag = await lifecycle._check_one_bot(
            bot, sem, datetime.now(),
            lifecycle._scan_cfg.apply_owner_whitelist,
            lifecycle._scan_cfg.global_dry_run,
        )

        assert tag == "applied"
        svc._apply_decision.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_failure_returns_failed(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        svc.query_device_status.side_effect = DesktopBotServiceError("timeout")
        bot = _make_bot("b1", status="ACTIVE")
        sem = asyncio.Semaphore(20)

        tag = await lifecycle._check_one_bot(
            bot, sem, datetime.now(),
            lifecycle._scan_cfg.apply_owner_whitelist,
            lifecycle._scan_cfg.global_dry_run,
        )

        assert tag == "failed"
        svc._apply_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_orphan_applied_released(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        svc.query_device_status.side_effect = DesktopBotOrphanError("not found")
        bot = _make_bot("b1", status="ACTIVE")
        sem = asyncio.Semaphore(20)

        tag = await lifecycle._check_one_bot(
            bot, sem, datetime.now(),
            lifecycle._scan_cfg.apply_owner_whitelist,
            lifecycle._scan_cfg.global_dry_run,
        )

        assert tag == "applied"
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status == "RELEASED"
        assert decision.soft_delete is True

    @pytest.mark.asyncio
    async def test_log_only_not_applied(self):
        cfg = DesktopBotPeriodicScanConfig(
            enabled=True, apply_owner_whitelist=frozenset(), global_dry_run=False,
        )
        lifecycle, bot_repo, svc = _make_lifecycle(scan_cfg=cfg)
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_OFFLINE",
        }
        bot = _make_bot("b1", status="ACTIVE")
        sem = asyncio.Semaphore(20)

        tag = await lifecycle._check_one_bot(
            bot, sem, datetime.now(),
            cfg.apply_owner_whitelist, cfg.global_dry_run,
        )

        assert tag == "log_only"
        svc._apply_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_pending_transition_timeout_to_offline(self):
        from agentclaw.community.core.desktop_bot.lifecycle import _PENDING_TIMEOUT
        lifecycle, bot_repo, svc = _make_lifecycle()
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_OFFLINE",
        }
        old = (datetime.now() - _PENDING_TIMEOUT - timedelta(minutes=1)).isoformat()
        bot = _make_bot("b1", status="PENDING", ext={"pending_since": old})
        sem = asyncio.Semaphore(20)

        tag = await lifecycle._check_one_bot(
            bot, sem, datetime.now(),
            lifecycle._scan_cfg.apply_owner_whitelist,
            lifecycle._scan_cfg.global_dry_run,
        )

        assert tag == "applied"
        decision = svc._apply_decision.call_args[0][4]
        assert decision.target_status == "OFFLINE"


class TestConcurrentScan:
    """_scan_all_bots 并发版:每页有界并发,行为等价串行版。"""

    @pytest.mark.asyncio
    async def test_all_bots_checked_concurrently(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        bots = [_make_bot(f"b{i}", status="ACTIVE") for i in range(10)]
        bot_repo.search_bots.return_value = (10, bots)
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_ONLINE",
        }

        await lifecycle._scan_all_bots()

        assert svc.query_device_status.call_count == 10
        assert svc._apply_decision.call_count == 10

    @pytest.mark.asyncio
    async def test_concurrency_capped_at_limit(self):
        from agentclaw.community.core.desktop_bot.lifecycle import _SCAN_CONCURRENCY
        import threading
        # log_only 配置:走过滤+查询但不调 _apply_decision,
        # 避免每个 bot 第二次 to_thread 放大线程池压力,聚焦验证查询并发上限。
        cfg = DesktopBotPeriodicScanConfig(
            enabled=True, apply_owner_whitelist=frozenset(), global_dry_run=False,
        )
        lifecycle, bot_repo, svc = _make_lifecycle(scan_cfg=cfg)
        bots = [_make_bot(f"b{i}", status="ACTIVE") for i in range(_SCAN_CONCURRENCY * 2)]
        bot_repo.search_bots.return_value = (len(bots), bots)

        lock = threading.Lock()
        state = {"current": 0, "peak": 0}

        def slow_query(device_id):
            with lock:
                state["current"] += 1
                if state["current"] > state["peak"]:
                    state["peak"] = state["current"]
            time.sleep(0.05)
            with lock:
                state["current"] -= 1
            return {"bot_status": "ACTIVE", "device_status": "ALL_ONLINE"}

        svc.query_device_status.side_effect = slow_query

        await lifecycle._scan_all_bots()

        # 峰值并发不得超过 Semaphore 上限
        assert state["peak"] <= _SCAN_CONCURRENCY
        # 但确实发生了并发(不是串行的 1)
        assert state["peak"] > 1

    @pytest.mark.asyncio
    async def test_single_failure_does_not_block_others(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        bots = [_make_bot("b1", status="ACTIVE"), _make_bot("b2", status="ACTIVE")]
        bots[0]["device_id"] = "dev-1"
        bots[1]["device_id"] = "dev-2"
        bot_repo.search_bots.return_value = (2, bots)

        def query(device_id):
            if device_id == "dev-1":
                raise DesktopBotServiceError("boom")
            return {"bot_status": "ACTIVE", "device_status": "ALL_ONLINE"}

        svc.query_device_status.side_effect = query

        await lifecycle._scan_all_bots()

        assert svc._apply_decision.call_count == 1

    @pytest.mark.asyncio
    async def test_paginates_across_pages(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        page1 = [_make_bot(f"b{i}", status="ACTIVE") for i in range(_SCAN_BATCH_SIZE)]
        page2 = [_make_bot(f"b{_SCAN_BATCH_SIZE + i}", status="ACTIVE") for i in range(3)]
        bot_repo.search_bots.side_effect = [
            (_SCAN_BATCH_SIZE, page1),
            (3, page2),
        ]
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_ONLINE",
        }

        await lifecycle._scan_all_bots()

        assert svc.query_device_status.call_count == _SCAN_BATCH_SIZE + 3
        assert bot_repo.search_bots.call_count == 2

    @pytest.mark.asyncio
    async def test_filtered_bots_not_queried(self):
        lifecycle, bot_repo, svc = _make_lifecycle()
        recent = datetime.now().isoformat()
        bots = [
            _make_bot("b1", status="RELEASED"),
            _make_bot("b2", device_id=""),
            _make_bot("b3", ext={"last_health_check": recent}),
            _make_bot("b4", status="ACTIVE"),
        ]
        bot_repo.search_bots.return_value = (4, bots)
        svc.query_device_status.return_value = {
            "bot_status": "ACTIVE", "device_status": "ALL_ONLINE",
        }

        await lifecycle._scan_all_bots()

        assert svc.query_device_status.call_count == 1


class TestEnvScopedKeys:
    """prod / pre / dev share one ZCache instance with no namespace isolation.
    Without an env prefix on the scan lock + timestamp keys, prod's frequent
    scans stamp the shared last_ts every ~2min and pre/dev skip forever. The
    keys MUST be env-scoped so each environment coordinates independently.
    """

    def _lc_for_env(self, env_value: str):
        with patch(
            "agentclaw.community.core.desktop_bot.lifecycle.get_current_env",
            return_value=env_value,
        ):
            return DesktopBotLifecycle(
                bot_repo=MagicMock(),
                desktop_bot_service=MagicMock(),
                scan_config=_make_lifecycle()[0]._scan_cfg,
                cache=MagicMock(),
            )

    def test_keys_carry_env_suffix(self):
        lc = self._lc_for_env("pre")
        assert lc._scan_lock_key == "desktop_bot_periodic_scan:pre"
        assert lc._scan_ts_key == "desktop_bot_periodic_scan:last_ts:pre"

    def test_prod_and_pre_keys_differ(self):
        prod = self._lc_for_env("prod")
        pre = self._lc_for_env("pre")
        # The whole point: prod and pre must NOT collide on either key.
        assert prod._scan_lock_key != pre._scan_lock_key
        assert prod._scan_ts_key != pre._scan_ts_key

    @pytest.mark.asyncio
    async def test_pre_not_starved_by_prod_timestamp(self):
        """A timestamp stamped under prod's key must not throttle pre's scan."""
        cache = MagicMock()
        cache.acquire_lock.return_value = "tok"
        cache.release_lock.return_value = True
        # Cache holds a fresh ts ONLY under prod's key; pre's key is empty.
        prod_ts_key = "desktop_bot_periodic_scan:last_ts:prod"
        recent = datetime.now().isoformat()
        cache.get.side_effect = lambda k: recent if k == prod_ts_key else None

        with patch(
            "agentclaw.community.core.desktop_bot.lifecycle.get_current_env",
            return_value="pre",
        ):
            lc = DesktopBotLifecycle(
                bot_repo=MagicMock(),
                desktop_bot_service=MagicMock(),
                scan_config=_make_lifecycle()[0]._scan_cfg,
                cache=cache,
            )
            lc._bot_repo.search_bots.return_value = (0, [])
            await lc._guarded_scan()

        # pre read its OWN (empty) ts key, saw no recent scan, and ran.
        cache.get.assert_called_once_with(lc._scan_ts_key)
        assert lc._bot_repo.search_bots.called  # scan actually ran
