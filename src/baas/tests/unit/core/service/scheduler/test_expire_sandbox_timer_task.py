"""Coverage tests for ExpireSandboxTimerTask (expired ACK pod sweep).

Covers keyset pagination, disabled/dry_run/lock short-circuits, per-row bot stop via
BotManageService.stop_bot, device->bot resolution, and bounded page concurrency.
"""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from secbaas.community.core.service.scheduler._tasks._expire_sandbox_timer_task import (
    ExpireSandboxTimerTask,
    ExpireSandboxTimerTaskConfig,
    parse_whitelist_bot_uuids,
)


def _acquired_lock():
    return SimpleNamespace(acquired=True)


def _not_acquired_lock():
    return SimpleNamespace(acquired=False)


def _lock():
    lock_service = MagicMock()
    lock_service.try_lock.return_value.__enter__.return_value = _acquired_lock()
    return lock_service


def _row(
    device_id: int,
    tenant: str = "tenant-x",
    device_uuid: str | None = None,
    env: str = "prod",
):
    return {
        "id": device_id,
        "tenant": tenant,
        "env": env,
        "device_uuid": device_uuid or f"DEVICE-{device_id:032x}",
        "provider_device_id": f"arc-{device_id}",
    }


def _pages(*pgs):
    pages = list(pgs) + [[]]
    return [list(pg) for pg in pages]


def _bot_rel(bot_id: int):
    return SimpleNamespace(bot_id=bot_id)


def _bot_record(bot_uuid: str):
    return SimpleNamespace(bot_uuid=bot_uuid)


def _task(
    config=None,
    *,
    repo=None,
    bot_manage=None,
    bot_repo=None,
    rel_repo=None,
    lock_service=None,
    system_config_service=None,
):
    if config is None:
        config = ExpireSandboxTimerTaskConfig(enabled=True, arca_provider="aliyun_ack")
    elif config.arca_provider == "stub":
        # Tests that only intend to exercise the enabled path default to "stub",
        # so normalize it to the eligible provider unless a non-ack variant is
        # explicitly requested.
        config.arca_provider = "aliyun_ack"
    if system_config_service is None:
        system_config_service = MagicMock()
        system_config_service.get_config.return_value = None
    return ExpireSandboxTimerTask(
        config=config,
        lock_service=lock_service or _lock(),
        device_repo=repo or MagicMock(),
        bot_manage_service=bot_manage or MagicMock(),
        bot_repo=bot_repo or MagicMock(),
        bot_device_rel_repo=rel_repo or MagicMock(),
        system_config_service=system_config_service,
    )


class TestDefaults:
    def test_name(self):
        task = _task()
        assert task.name == "expire_sandbox_timer"

    def test_defaults(self):
        cfg = ExpireSandboxTimerTaskConfig()
        assert cfg.enabled is False
        assert cfg.default_ttl_minutes == 10080
        assert cfg.lock_name == "expire_sandbox_timer_lock"
        assert cfg.modifier == "expire_sandbox_timer"

    def test_provider_is_aliyun_ack(self):
        assert (
            ExpireSandboxTimerTaskConfig(
                arca_provider="aliyun_ack"
            ).provider_is_aliyun_ack()
            is True
        )
        for variant in ("arca_sdk", "stub", "local_proc"):
            assert (
                ExpireSandboxTimerTaskConfig(
                    arca_provider=variant
                ).provider_is_aliyun_ack()
                is False
            )

    def test_interval_seconds(self):
        cfg = ExpireSandboxTimerTaskConfig(cron_interval_seconds=42)
        task = _task(cfg)
        assert task.interval_seconds == 42


class TestResolvedLockName:
    def test_env_suffix(self):
        with patch(
            "secbaas.community.core.service.scheduler._tasks._expire_sandbox_timer_task.get_current_env",
            return_value="prod",
        ):
            assert (
                ExpireSandboxTimerTaskConfig().resolved_lock_name()
                == "expire_sandbox_timer_lock_prod"
            )


class TestEarlyReturns:
    @pytest.mark.asyncio
    async def test_disabled_skips(self):
        task = _task(ExpireSandboxTimerTaskConfig(enabled=False))
        report = await task.run()
        assert report is None

    @pytest.mark.asyncio
    async def test_non_ack_provider_skips(self):
        repo = MagicMock()
        bot_manage = MagicMock()
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True, arca_provider="arca_sdk"),
            repo=repo,
            bot_manage=bot_manage,
        )
        report = await task.run()
        assert report is None
        repo.list_expired_paginated.assert_not_called()
        bot_manage.stop_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_skips(self):
        repo = MagicMock()
        bot_manage = MagicMock()
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True, dry_run=True),
            repo=repo,
            bot_manage=bot_manage,
        )
        await task.run()
        repo.list_expired_paginated.assert_not_called()
        bot_manage.stop_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_not_acquired(self):
        repo = MagicMock()
        bot_manage = MagicMock()
        lock_service = MagicMock()
        lock_service.try_lock.return_value.__enter__.return_value = _not_acquired_lock()
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True),
            repo=repo,
            bot_manage=bot_manage,
            lock_service=lock_service,
        )
        await task.run()
        repo.list_expired_paginated.assert_not_called()
        bot_manage.stop_bot.assert_not_called()


class TestRun:
    @pytest.mark.asyncio
    async def test_empty_queue(self):
        repo = MagicMock()
        repo.list_expired_paginated.return_value = []
        bot_manage = MagicMock()
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True),
            repo=repo,
            bot_manage=bot_manage,
        )
        report = await task.run()
        assert report.scanned == 0
        assert report.stopped == 0
        bot_manage.stop_bot.assert_not_called()

    def _resolving_mocks(self, bot_uuid="BOT-UUID-1"):
        bot_manage = MagicMock()
        bot_manage.stop_bot = AsyncMock(return_value=MagicMock())
        rel_repo = MagicMock()
        rel_repo.get_by_device_uuid.return_value = _bot_rel(7)
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = _bot_record(bot_uuid)
        return bot_manage, rel_repo, bot_repo

    @pytest.mark.asyncio
    async def test_stops_due_bot(self):
        repo = MagicMock()
        repo.list_expired_paginated.side_effect = _pages([_row(1)])
        bot_manage, rel_repo, bot_repo = self._resolving_mocks()
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True),
            repo=repo,
            bot_manage=bot_manage,
            bot_repo=bot_repo,
            rel_repo=rel_repo,
        )
        report = await task.run()
        assert report.stopped == 1
        bot_manage.stop_bot.assert_awaited_once_with(
            tenant="tenant-x",
            bot_uuid="BOT-UUID-1",
            operator="expire_sandbox_timer",
            request_id=ANY,
        )

    @pytest.mark.asyncio
    async def test_pagination_drains_all_pages(self):
        p1 = [_row(i) for i in range(1, 4)]
        p2 = [_row(j) for j in range(4, 7)]
        repo = MagicMock()
        repo.list_expired_paginated.side_effect = _pages(p1, p2)
        bot_manage, rel_repo, bot_repo = self._resolving_mocks()
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True, batch_size=3),
            repo=repo,
            bot_manage=bot_manage,
            bot_repo=bot_repo,
            rel_repo=rel_repo,
        )
        report = await task.run()
        assert report.scanned == 6
        assert report.stopped == 6
        assert bot_manage.stop_bot.await_count == 6

    @pytest.mark.asyncio
    async def test_cursor_advances_from_last_row(self):
        p1 = [_row(1), _row(5)]
        p2 = [_row(9)]
        repo = MagicMock()
        repo.list_expired_paginated.side_effect = _pages(p1, p2)
        bot_manage, rel_repo, bot_repo = self._resolving_mocks()
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True, batch_size=2),
            repo=repo,
            bot_manage=bot_manage,
            bot_repo=bot_repo,
            rel_repo=rel_repo,
        )
        await task.run()
        second_call = repo.list_expired_paginated.call_args_list[1]
        assert second_call.kwargs["last_id"] == 5

    @pytest.mark.asyncio
    async def test_passes_grace_and_default_ttl(self):
        repo = MagicMock()
        repo.list_expired_paginated.return_value = []
        task = _task(
            ExpireSandboxTimerTaskConfig(
                enabled=True, grace_seconds=300, default_ttl_minutes=1440
            ),
            repo=repo,
        )
        await task.run()
        call = repo.list_expired_paginated.call_args
        assert call.kwargs["grace_seconds"] == 300
        assert call.kwargs["default_ttl_minutes"] == 1440

    @pytest.mark.asyncio
    async def test_row_missing_identity_skipped(self):
        row = {"id": 1, "tenant": None, "env": "prod", "device_uuid": None}
        repo = MagicMock()
        repo.list_expired_paginated.side_effect = _pages([row])
        bot_manage = MagicMock()
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True),
            repo=repo,
            bot_manage=bot_manage,
        )
        report = await task.run()
        assert report.scanned == 1
        assert report.stopped == 0
        assert report.skipped == 1
        bot_manage.stop_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_bot_bound_skipped(self):
        repo = MagicMock()
        repo.list_expired_paginated.side_effect = _pages([_row(1)])
        bot_manage = MagicMock()
        bot_manage.stop_bot = AsyncMock(return_value=MagicMock())
        rel_repo = MagicMock()
        rel_repo.get_by_device_uuid.return_value = None
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True),
            repo=repo,
            bot_manage=bot_manage,
            rel_repo=rel_repo,
        )
        report = await task.run()
        assert report.scanned == 1
        assert report.stopped == 0
        assert report.skipped == 1
        bot_manage.stop_bot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rel_bound_bot_missing_skipped(self):
        repo = MagicMock()
        repo.list_expired_paginated.side_effect = _pages([_row(1)])
        bot_manage = MagicMock()
        bot_manage.stop_bot = AsyncMock(return_value=MagicMock())
        rel_repo = MagicMock()
        rel_repo.get_by_device_uuid.return_value = _bot_rel(7)
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = None
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True),
            repo=repo,
            bot_manage=bot_manage,
            bot_repo=bot_repo,
            rel_repo=rel_repo,
        )
        report = await task.run()
        assert report.scanned == 1
        assert report.stopped == 0
        assert report.skipped == 1
        bot_manage.stop_bot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cursor_not_advanced_breaks_loop(self):
        # A subsequent page whose last id fails to advance past last_id would
        # loop forever; the task must break to avoid an infinite drain loop.
        p1 = [_row(5), _row(2)]
        p2 = [_row(1)]
        repo = MagicMock()
        repo.list_expired_paginated.side_effect = _pages(p1, p2)
        bot_manage, rel_repo, bot_repo = self._resolving_mocks()
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True, batch_size=2),
            repo=repo,
            bot_manage=bot_manage,
            bot_repo=bot_repo,
            rel_repo=rel_repo,
        )
        report = await task.run()
        assert report.scanned == 3
        assert report.stopped == 3
        # Third call (empty page) is skipped: loop breaks when next_id (1) <= last_id (2).
        assert repo.list_expired_paginated.call_count == 2

    @pytest.mark.asyncio
    async def test_stop_failure_counted_not_raised(self):
        repo = MagicMock()
        repo.list_expired_paginated.side_effect = _pages([_row(1)])
        bot_manage = MagicMock()
        bot_manage.stop_bot = AsyncMock(side_effect=RuntimeError("down"))
        rel_repo = MagicMock()
        rel_repo.get_by_device_uuid.return_value = _bot_rel(7)
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = _bot_record("BOT-UUID-1")
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True),
            repo=repo,
            bot_manage=bot_manage,
            bot_repo=bot_repo,
            rel_repo=rel_repo,
        )
        report = await task.run()
        assert report.failed == 1
        assert report.stopped == 0

    @pytest.mark.asyncio
    async def test_query_error_aborts_run(self):
        repo = MagicMock()
        repo.list_expired_paginated.side_effect = RuntimeError("db down")
        bot_manage = MagicMock()
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True),
            repo=repo,
            bot_manage=bot_manage,
        )
        report = await task.run()
        assert report.scanned == 0
        bot_manage.stop_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_reentrant_run_returns_none(self):
        task = _task(ExpireSandboxTimerTaskConfig(enabled=True))
        task._running = True
        report = await task.run()
        assert report is None


class TestWhitelist:
    def _cfg(self, value):
        return SimpleNamespace(conf_value=value)

    def _whitelisted_task(self, conf_value):
        repo = MagicMock()
        repo.list_expired_paginated.side_effect = _pages([_row(1)])
        bot_manage = MagicMock()
        bot_manage.stop_bot = AsyncMock(return_value=MagicMock())
        rel_repo = MagicMock()
        rel_repo.get_by_device_uuid.return_value = _bot_rel(7)
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = _bot_record("BOT-UUID-1")
        svc = MagicMock()
        svc.get_config.return_value = self._cfg(conf_value)
        task = _task(
            ExpireSandboxTimerTaskConfig(enabled=True),
            repo=repo,
            bot_manage=bot_manage,
            bot_repo=bot_repo,
            rel_repo=rel_repo,
            system_config_service=svc,
        )
        return task, bot_manage

    @pytest.mark.asyncio
    async def test_whitelisted_bot_skipped(self):
        task, bot_manage = self._whitelisted_task("BOT-UUID-1")
        report = await task.run()
        assert report.stopped == 0
        assert report.skipped == 1
        assert report.skipped_reasons["whitelisted"] == 1
        bot_manage.stop_bot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_whitelisted_bot_stopped(self):
        task, bot_manage = self._whitelisted_task("OTHER-UUID")
        report = await task.run()
        assert report.stopped == 1
        assert report.skipped == 0
        bot_manage.stop_bot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_config_empty_whitelist(self):
        task, bot_manage = self._whitelisted_task(None)
        # overridden to return None (missing row)
        task._system_config_service.get_config.return_value = None
        report = await task.run()
        assert report.stopped == 1
        bot_manage.stop_bot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_whitespace_config_empty_whitelist(self):
        task, bot_manage = self._whitelisted_task("")
        report = await task.run()
        assert report.stopped == 1
        bot_manage.stop_bot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_malformed_config_empty_whitelist(self):
        task, bot_manage = self._whitelisted_task(" , \n,,  \r\n ")
        report = await task.run()
        assert report.stopped == 1
        bot_manage.stop_bot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_delimiters(self):
        task, bot_manage = self._whitelisted_task("A1, B2\nC3\r\n D4 ,")
        # bot is BOT-UUID-1, not in list → stopped
        report = await task.run()
        assert report.stopped == 1
        bot_manage.stop_bot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_read_error_fails_open(self):
        task, bot_manage = self._whitelisted_task("BOT-UUID-1")
        task._system_config_service.get_config.side_effect = RuntimeError("db down")
        report = await task.run()
        assert report.stopped == 1
        bot_manage.stop_bot.assert_awaited_once()


class TestParseWhitelist:
    def test_none(self):
        assert parse_whitelist_bot_uuids(None) == set()

    def test_empty_string(self):
        assert parse_whitelist_bot_uuids("") == set()

    def test_comma_and_newline(self):
        assert parse_whitelist_bot_uuids("a1, b2\nc3\r\n d4 ,") == {
            "a1",
            "b2",
            "c3",
            "d4",
        }

    def test_whitespace_only(self):
        assert parse_whitelist_bot_uuids(" , \n, \r\n ") == set()
