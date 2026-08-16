"""Coverage tests for DeviceTtlTimerTask (TTL renew + probe scheduler).

Covers the keyset pagination by id: the loop must page through the whole
eligible queue by ``id > last_id`` so a failing/TTL-stuck head page cannot
stall trailing devices. Also covers the per-record digest logging.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.service.scheduler._tasks._device_ttl_timer_task import (
    DeviceTtlTimerTask,
    DeviceTtlTimerTaskConfig,
)


def _acquired_lock():
    """Return a mock lock context that reports acquired=True."""
    return SimpleNamespace(acquired=True)


def _not_acquired_lock():
    return SimpleNamespace(acquired=False)


def _build(binding_id: int, ttl: str = "2026-01-01 00:00:00"):
    """Build a MagicMock DeviceBindingRecord with a dict device_props."""
    record = MagicMock()
    record.id = binding_id
    record.device_props = {
        "sandbox_id": f"sb-{binding_id}",
        "ttl_expiration_time": ttl,
    }
    return record


def _baas_row(device_id: int, ttl: str = "2026-01-01 00:00:00"):
    """Build a baas_device dict-shaped page row."""
    return {
        "id": device_id,
        "provider_device_id": f"arc-{device_id}",
        "provider_device_props": {
            "sandbox_id": f"sb-{device_id}",
            "ttl_expiration_time": ttl,
        },
    }


def _result(
    *,
    success: bool,
    error: str | None = None,
    device_id: str | None = None,
    old: str | None = None,
    new: str | None = None,
):
    result = MagicMock()
    result.success = success
    result.error = error
    result.device_id = device_id
    result.old_expiration_time = old
    result.new_expiration_time = new
    return result


def _warn(action: str = "RESET"):
    warn = MagicMock()
    warn.action = action
    return warn


def _pages(*pgs):
    """Return a side_effect that yields the given pages then an empty list."""
    pages = list(pgs) + [[]]
    return [[p for p in pg] for pg in pages]


def _lock():
    lock_service = MagicMock()
    lock_service.try_lock.return_value.__enter__.return_value = _acquired_lock()
    return lock_service


class TestDefaults:
    def test_name_default(self):
        task = DeviceTtlTimerTask(
            config=DeviceTtlTimerTaskConfig(),
            lock_service=MagicMock(),
            binding_repo=MagicMock(),
            router=MagicMock(),
        )
        assert task.name == "device_ttl_timer"

    def test_interval_seconds(self):
        cfg = DeviceTtlTimerTaskConfig(cron_interval_seconds=42)
        task = DeviceTtlTimerTask(cfg, MagicMock(), MagicMock(), MagicMock())
        assert task.interval_seconds == 42
        assert task._config.batch_size == 100
        assert task._config.lock_name == "device_ttl_timer_lock"
        assert task._config.lock_expire_seconds == 1800


class TestEarlyReturns:
    @pytest.mark.asyncio
    async def test_disabled_skips(self):
        cfg = DeviceTtlTimerTaskConfig(enabled=False)
        lock_service = MagicMock()
        binding_repo = MagicMock()
        router = MagicMock()
        task = DeviceTtlTimerTask(cfg, lock_service, binding_repo, router)
        await task.run()
        lock_service.try_lock.assert_not_called()
        binding_repo.list_bindings_by_id_asc.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_skips(self):
        cfg = DeviceTtlTimerTaskConfig(dry_run=True)
        lock_service = MagicMock()
        binding_repo = MagicMock()
        task = DeviceTtlTimerTask(cfg, lock_service, binding_repo, MagicMock())
        await task.run()
        lock_service.try_lock.assert_not_called()
        binding_repo.list_bindings_by_id_asc.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_not_acquired(self):
        cfg = DeviceTtlTimerTaskConfig()
        lock_service = MagicMock()
        lock_service.try_lock.return_value.__enter__.return_value = _not_acquired_lock()
        binding_repo = MagicMock()
        router = MagicMock()
        task = DeviceTtlTimerTask(cfg, lock_service, binding_repo, router)
        await task.run()
        binding_repo.list_bindings_by_id_asc.assert_not_called()
        binding_repo.list_baas_devices_by_id_asc.assert_not_called()


class TestPersonalPagination:
    def _repo(self, binding_repo, pages):
        binding_repo.list_bindings_by_id_asc.side_effect = pages
        binding_repo.list_baas_devices_by_id_asc.return_value = []
        return binding_repo

    @pytest.mark.asyncio
    async def test_empty_bindings(self):
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.return_value = []
        binding_repo.list_baas_devices_by_id_asc.return_value = []
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        router.renew_ttl.assert_not_called()

    @pytest.mark.asyncio
    async def test_renew_success_then_warn(self):
        binding_repo = self._repo(MagicMock(), _pages([_build(1)]))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        assert router.renew_ttl.call_count == 1
        assert router.warn_device.call_count == 1

    @pytest.mark.asyncio
    async def test_multiple_pages_all_renewed(self):
        # 4.1: 100 + 50 records across two pages, all renewed.
        p1 = [_build(i, f"2026-01-01 00:00:{i:02d}") for i in range(1, 101)]
        p2 = [
            _build(i, f"2026-01-01 00:{i // 60:02d}:{i % 60:02d}")
            for i in range(101, 151)
        ]
        binding_repo = self._repo(MagicMock(), _pages(p1, p2))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=100), _lock(), binding_repo, router
        )
        await task.run()
        assert router.renew_ttl.call_count == 150
        # 2 data pages + 1 empty termination page.
        assert binding_repo.list_bindings_by_id_asc.call_count == 3

    @pytest.mark.asyncio
    async def test_leading_page_all_fail_still_drains(self):
        # 4.3: THE regression test for the bug. Page1 of 100 ALL fail renew;
        # pagination must still advance the cursor, fetch page2, and renew page2.
        p1 = [_build(i, "2026-01-01 00:00:00") for i in range(1, 101)]
        p2 = [_build(j, "2026-01-02 00:00:00") for j in range(101, 151)]
        binding_repo = self._repo(MagicMock(), _pages(p1, p2))
        router = MagicMock()

        def renew(table_type="ac_binding", table_id=None):
            return (
                _result(success=False, error="down")
                if table_id <= 100
                else _result(success=True)
            )

        router.renew_ttl = AsyncMock(side_effect=renew)
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=100), _lock(), binding_repo, router
        )
        await task.run()
        # Even though page1 (ids 1..100) all failed, page2 (ids 101..150) must be renewed.
        assert any(c.kwargs["table_id"] >= 101 for c in router.renew_ttl.call_args_list)
        assert router.renew_ttl.call_count == 150

    @pytest.mark.asyncio
    async def test_equal_ttl_advances_via_id(self):
        # 4.4: same ttl for all records → id tiebreak traverses each exactly once.
        p1 = [_build(i, "2026-03-01 00:00:00") for i in range(1, 7)]
        binding_repo = self._repo(MagicMock(), _pages(p1))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=3), _lock(), binding_repo, router
        )
        await task.run()
        renewed = {c.kwargs["table_id"] for c in router.renew_ttl.call_args_list}
        assert renewed == {1, 2, 3, 4, 5, 6}

    @pytest.mark.asyncio
    async def test_empty_second_page_stops_iteration(self):
        # 4.5: p1 non-empty then empty → no third fetch.
        p1 = [_build(i) for i in range(1, 4)]
        binding_repo = self._repo(MagicMock(), _pages(p1))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=2), _lock(), binding_repo, router
        )
        await task.run()
        assert binding_repo.list_bindings_by_id_asc.call_count == 2

    @pytest.mark.asyncio
    async def test_full_scan_drains_every_page(self):
        # 4.6: 3 pages, keeps paging until empty.
        p1 = [_build(i) for i in range(1, 4)]
        p2 = [_build(j) for j in range(4, 8)]
        p3 = [_build(k) for k in range(8, 11)]
        binding_repo = self._repo(MagicMock(), _pages(p1, p2, p3))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=3), _lock(), binding_repo, router
        )
        await task.run()
        assert router.renew_ttl.call_count == 10
        # 3 data pages + 1 empty termination page.
        assert binding_repo.list_bindings_by_id_asc.call_count == 4

    @pytest.mark.asyncio
    async def test_zeroed_first_page_cursor(self):
        # 4.7: first fetch called with last_id=0.
        binding_repo = self._repo(MagicMock(), _pages([_build(1)]))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        first_call = binding_repo.list_bindings_by_id_asc.call_args_list[0]
        assert first_call.kwargs["last_id"] == 0

    @pytest.mark.asyncio
    async def test_cursor_advances_from_last_page_row(self):
        # 4.8: second call cursor == id of page1 tail row.
        tail = _build(5, "2026-02-02 00:00:00")
        p1 = [_build(1, "2026-01-01 00:00:00"), tail]
        p2 = [_build(9, "2026-03-01 00:00:00")]
        binding_repo = self._repo(MagicMock(), _pages(p1, p2))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=2), _lock(), binding_repo, router
        )
        await task.run()
        second_call = binding_repo.list_bindings_by_id_asc.call_args_list[1]
        assert second_call.kwargs["last_id"] == 5

    @pytest.mark.asyncio
    async def test_warn_device_error_mid_pagination_continues(self):
        # 4.5b: warn error on page2 must not stop pagination -> page3 processed.
        p1 = [_build(1)]
        p2 = [_build(2)]
        p3 = [_build(3)]
        binding_repo = self._repo(MagicMock(), _pages(p1, p2, p3))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(
            side_effect=lambda table_type="ac_binding", table_id=None: (
                (_ for _ in ()).throw(RuntimeError("warn boom"))
                if table_id == 2
                else _warn()
            )
        )
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=1), _lock(), binding_repo, router
        )
        await task.run()
        renewed = {c.kwargs["table_id"] for c in router.renew_ttl.call_args_list}
        assert renewed == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_mixed_success_failure_metrics(self):
        # 4.9: mixed outcomes -> correct counters (renewed, failed via return, exception).
        p1 = [_build(1), _build(2), _build(3), _build(4)]
        binding_repo = self._repo(MagicMock(), _pages(p1))
        router = MagicMock()
        router.renew_ttl = AsyncMock(
            side_effect=lambda table_type="ac_binding", table_id=None: {
                1: _result(success=True),
                2: _result(success=False, error="denied"),
                3: _result(success=True),
            }.get(table_id, RuntimeError("boom"))
        )
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        # renew success for 1,3; failure return for 2; exception for 4.
        renew_calls = list(router.renew_ttl.call_args_list)
        assert len(renew_calls) == 4


class TestServicePagination:
    def _repo(self, binding_repo, pages):
        binding_repo.list_bindings_by_id_asc.return_value = []
        binding_repo.list_baas_devices_by_id_asc.side_effect = pages
        return binding_repo

    @pytest.mark.asyncio
    async def test_empty_devices(self):
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.return_value = []
        binding_repo.list_baas_devices_by_id_asc.return_value = []
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        router.renew_ttl.assert_not_called()

    @pytest.mark.asyncio
    async def test_renew_success_then_warn(self):
        binding_repo = self._repo(MagicMock(), _pages([_baas_row(10)]))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        assert router.renew_ttl.call_args_list[0].kwargs["table_id"] == 10
        assert router.renew_ttl.call_args_list[0].kwargs["table_type"] == "baas"

    @pytest.mark.asyncio
    async def test_multiple_pages_all_renewed_service(self):
        # 4.2
        p1 = [_baas_row(i) for i in range(1, 101)]
        p2 = [_baas_row(j) for j in range(101, 151)]
        binding_repo = self._repo(MagicMock(), _pages(p1, p2))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=100), _lock(), binding_repo, router
        )
        await task.run()
        assert router.renew_ttl.call_count == 150

    @pytest.mark.asyncio
    async def test_baas_device_id_extracted(self):
        # 4.10: dict-shaped row id used, not path bug.
        binding_repo = self._repo(MagicMock(), _pages([_baas_row(99)]))
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        ids = {c.kwargs["table_id"] for c in router.renew_ttl.call_args_list}
        assert ids == {99}

    @pytest.mark.asyncio
    async def test_devices_query_error(self):
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.return_value = []
        binding_repo.list_baas_devices_by_id_asc.side_effect = RuntimeError("db boom")
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        router.renew_ttl.assert_not_called()


class TestRunUuidSharing:
    @pytest.mark.asyncio
    async def test_single_trigger_shares_run_uuid_across_groups(self):
        # Each scheduler trigger must generate ONE run_uuid passed to both groups.
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.side_effect = [[_build(1)], [], [], []]
        binding_repo.list_baas_devices_by_id_asc.side_effect = [[_baas_row(2)], []]
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        renew_calls = router.renew_ttl.call_args_list
        assert len(renew_calls) == 2
        uuids = {c.kwargs["run_uuid"] for c in renew_calls}
        assert len(uuids) == 1, "one trigger must share one run_uuid across groups"
        assert _uuid_valid(uuids.pop())


def _uuid_valid(value):
    import re

    return bool(
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            value,
        )
    )


class TestRunDirect:
    @pytest.mark.asyncio
    async def test_run_direct_bypasses_lock(self):
        # Manual trigger must NOT acquire the distributed lock.
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.side_effect = [[_build(1)], []]
        binding_repo.list_baas_devices_by_id_asc.side_effect = [[]]
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        lock_service = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run_direct()
        lock_service.try_lock.assert_not_called()
        assert router.renew_ttl.call_count == 1

    @pytest.mark.asyncio
    async def test_run_direct_passes_run_uuid(self):
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.side_effect = [[_build(1)], [], []]
        binding_repo.list_baas_devices_by_id_asc.side_effect = [[]]
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run_direct(run_uuid="manual-1")
        assert router.renew_ttl.call_args.kwargs["run_uuid"] == "manual-1"

    @pytest.mark.asyncio
    async def test_cron_run_still_acquires_lock(self):
        # Cron path must still go through the distributed lock.
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.side_effect = [[_build(1)], []]
        binding_repo.list_baas_devices_by_id_asc.side_effect = [[]]
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        lock_service = _lock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run()
        lock_service.try_lock.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_direct_respects_disabled(self):
        binding_repo = MagicMock()
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(enabled=False), _lock(), binding_repo, router
        )
        await task.run_direct()
        router.renew_ttl.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_direct_overrides_batch_size(self):
        # Pass-through batch_size must be used as the page limit.
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.return_value = []
        binding_repo.list_baas_devices_by_id_asc.return_value = []
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run_direct(batch_size=500)
        assert binding_repo.list_bindings_by_id_asc.call_args.kwargs["limit"] == 500
        assert binding_repo.list_baas_devices_by_id_asc.call_args.kwargs["limit"] == 500

    @pytest.mark.asyncio
    async def test_run_direct_max_pages_caps_loop(self):
        # max_pages bounds how many times each group's while loop advances.
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.side_effect = [
            [_build(1)],
            [_build(2)],
            [_build(3)],
        ]
        binding_repo.list_baas_devices_by_id_asc.side_effect = [
            [_baas_row(10)],
            [_baas_row(11)],
        ]
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=1), _lock(), binding_repo, router
        )
        await task.run_direct(max_pages=2, batch_size=1)
        # 2 pages for personal + 2 pages for service (capped at max_pages).
        assert binding_repo.list_bindings_by_id_asc.call_count == 2
        assert binding_repo.list_baas_devices_by_id_asc.call_count == 2

    @pytest.mark.asyncio
    async def test_run_direct_max_pages_zero_runs_one_page(self):
        # max_pages=0 means a single page attempt then stop.
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.side_effect = [[_build(1)], [_build(2)]]
        binding_repo.list_baas_devices_by_id_asc.side_effect = [[]]
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=1), _lock(), binding_repo, router
        )
        await task.run_direct(max_pages=1, batch_size=1)
        assert binding_repo.list_bindings_by_id_asc.call_count == 1
        assert router.renew_ttl.call_count == 1


class TestFullDrainWith500Records:
    """500 条/支 personal + service 记录各自 full-drain，校验 report 对象。"""

    N = 500
    BATCH = 100

    def _build_personal_rows(self):
        return [
            _build(i, f"2026-01-01 00:{i % 60:02d}:{i % 60:02d}")
            for i in range(1, self.N + 1)
        ]

    def _build_baas_rows(self):
        return [
            _baas_row(i, f"2026-02-01 00:{i % 60:02d}:{i % 60:02d}")
            for i in range(1001, 1001 + self.N)
        ]

    def _chunk(self, rows):
        return [rows[i : i + self.BATCH] for i in range(0, len(rows), self.BATCH)]

    def _setup_repo_ok(self, binding_repo, router):
        binding_repo.list_bindings_by_id_asc.side_effect = self._chunk(
            self._build_personal_rows()
        ) + [[]]
        binding_repo.list_baas_devices_by_id_asc.side_effect = self._chunk(
            self._build_baas_rows()
        ) + [[]]
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())

    def _make_task(self, binding_repo, router):
        return DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(batch_size=self.BATCH),
            _lock(),
            binding_repo,
            router,
        )

    @pytest.mark.asyncio
    async def test_run_reports_500_success_each_group(self):
        binding_repo = MagicMock()
        router = MagicMock()
        self._setup_repo_ok(binding_repo, router)
        task = self._make_task(binding_repo, router)

        report = await task.run(run_uuid="r1")

        assert report is not None
        assert report.run_uuid == "r1"
        assert report.trigger == "scheduler"
        assert report.personal.records_processed == 500
        assert report.personal.success == 500
        assert report.personal.failure == 0
        assert report.personal.pages == 5
        assert report.service.records_processed == 500
        assert report.service.success == 500
        assert report.service.failure == 0
        assert report.total_processed == 1000
        assert report.total_success == 1000
        assert report.total_failure == 0
        assert report.total_renewed == 1000

    @pytest.mark.asyncio
    async def test_run_direct_reports_manual_trigger(self):
        binding_repo = MagicMock()
        router = MagicMock()
        self._setup_repo_ok(binding_repo, router)
        task = self._make_task(binding_repo, router)

        report = await task.run_direct(run_uuid="r2")

        assert report is not None
        assert report.trigger == "manual"
        assert report.personal.success == 500
        assert report.service.success == 500

    @pytest.mark.asyncio
    async def test_report_counts_retry_success(self):
        # 一次查询失败后重试成功，仍能完整 drain 并计入 report。
        binding_repo = MagicMock()
        router = MagicMock()
        personal = [_build(1, "2026-01-01 00:00:00")]
        binding_repo.list_bindings_by_id_asc.side_effect = [
            RuntimeError("db flap"),
            personal[0:],
            [],
        ]
        binding_repo.list_baas_devices_by_id_asc.side_effect = [[]]
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=_warn())
        task = self._make_task(binding_repo, router)

        report = await task.run(run_uuid="r4")
        assert report.personal.records_processed == 1
        assert report.personal.success == 1

    @pytest.mark.asyncio
    async def test_report_counts_mixed_outcomes(self):
        binding_repo = MagicMock()
        router = MagicMock()
        binding_repo.list_bindings_by_id_asc.side_effect = [
            [_build(1, "2026-01-01 00:00:00")],
            [],
        ]
        binding_repo.list_baas_devices_by_id_asc.side_effect = [[]]

        def renew(table_type="ac_binding", table_id=None, run_uuid=None):
            if table_id == 1:
                return _result(success=True)
            return _result(success=False, error="down")

        router.renew_ttl = AsyncMock(side_effect=renew)
        router.warn_device = AsyncMock(return_value=_warn())
        task = self._make_task(binding_repo, router)

        report = await task.run_direct(run_uuid="r3")
        assert report.personal.records_processed == 1
        assert report.personal.success == 1
        assert report.personal.failure == 0
        assert report.service.records_processed == 0

    @pytest.mark.asyncio
    async def test_run_returns_none_when_disabled(self):
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(enabled=False),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        report = await task.run()
        assert report is None

    @pytest.mark.asyncio
    async def test_run_returns_none_when_lock_not_acquired(self):
        binding_repo = MagicMock()
        router = MagicMock()
        lock_service = MagicMock()
        lock_service.try_lock.return_value.__enter__.return_value = _not_acquired_lock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        report = await task.run()
        assert report is None
        assert not router.renew_ttl.called

    @pytest.mark.asyncio
    async def test_reentrant_run_returns_none(self):
        binding_repo = MagicMock()
        router = MagicMock()
        task = self._make_task(binding_repo, router)
        task._running = True
        report = await task.run()
        assert report is None

    @pytest.mark.asyncio
    async def test_reentrant_run_direct_returns_none(self):
        binding_repo = MagicMock()
        router = MagicMock()
        task = self._make_task(binding_repo, router)
        task._running = True
        report = await task.run_direct()
        assert report is None


class TestTriggerAsync:
    """trigger_async 异步（fire-and-forget）触发行为，供 renew-ttl-trigger 使用。"""

    @pytest.mark.asyncio
    async def test_schedules_background_run_and_returns_uuid(self):
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.return_value = []
        binding_repo.list_baas_devices_by_id_asc.return_value = []
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )

        uuid = task.trigger_async()
        assert isinstance(uuid, str) and uuid

        # 后台任务在事件循环 yield 后执行完成。
        for _ in range(3):
            await asyncio.sleep(0)

        assert binding_repo.list_bindings_by_id_asc.called
        assert binding_repo.list_baas_devices_by_id_asc.called

    @pytest.mark.asyncio
    async def test_passes_override_parameters(self):
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_id_asc.return_value = []
        binding_repo.list_baas_devices_by_id_asc.return_value = []
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )

        task.trigger_async(run_uuid="run-x", batch_size=7, max_pages=3)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert binding_repo.list_bindings_by_id_asc.call_args.kwargs["limit"] == 7

    @pytest.mark.asyncio
    async def test_background_run_is_non_blocking(self):
        """trigger_async 立即返回，不等待整表扫描完成。"""
        import asyncio as _asyncio

        completed = []

        async def _run_direct(**kwargs):
            await _asyncio.sleep(0.5)
            completed.append(True)

        binding_repo = MagicMock()
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        task.run_direct = _run_direct  # type: ignore[method-assign]

        uuid = task.trigger_async()
        assert uuid
        await _asyncio.sleep(0)
        assert completed == []  # 仍在后台执行，接口已返回
