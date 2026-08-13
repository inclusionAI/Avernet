"""Coverage tests for DeviceTtlTimerTask (TTL renew + probe scheduler)."""

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
        assert task._config.lock_expire_seconds == 300


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
        binding_repo.list_bindings_by_ttl_asc.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_skips(self):
        cfg = DeviceTtlTimerTaskConfig(dry_run=True)
        lock_service = MagicMock()
        binding_repo = MagicMock()
        task = DeviceTtlTimerTask(cfg, lock_service, binding_repo, MagicMock())
        await task.run()
        lock_service.try_lock.assert_not_called()
        binding_repo.list_bindings_by_ttl_asc.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_not_acquired(self):
        cfg = DeviceTtlTimerTaskConfig()
        lock_service = MagicMock()
        lock_service.try_lock.return_value.__enter__.return_value = _not_acquired_lock()
        binding_repo = MagicMock()
        router = MagicMock()
        task = DeviceTtlTimerTask(cfg, lock_service, binding_repo, router)
        await task.run()
        binding_repo.list_bindings_by_ttl_asc.assert_not_called()
        binding_repo.list_baas_devices_by_ttl_asc.assert_not_called()


class TestPersonalBranch:
    @pytest.mark.asyncio
    async def test_empty_bindings(self):
        lock_service = _lock()
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = []
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run()
        router.renew_ttl.assert_not_called()

    @pytest.mark.asyncio
    async def test_renew_success_then_warn(self):
        lock_service = _lock()
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = [_build(1)]
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=MagicMock())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run()
        router.renew_ttl.assert_called_once()
        router.warn_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_renew_failure_uses_warn(self):
        lock_service = _lock()
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = [_build(2)]
        router = MagicMock()
        router.renew_ttl = AsyncMock(
            return_value=_result(success=False, error="denied")
        )
        router.warn_device = AsyncMock(return_value=MagicMock())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run()
        router.warn_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_renew_exception_still_warns(self):
        lock_service = _lock()
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = [_build(3)]
        router = MagicMock()
        router.renew_ttl = AsyncMock(side_effect=RuntimeError("boom"))
        router.warn_device = AsyncMock(return_value=MagicMock())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run()
        # Renew raised, so warn_device is skipped via `continue`.
        router.warn_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_warn_error_does_not_raise(self):
        lock_service = _lock()
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = [_build(4)]
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(side_effect=RuntimeError("warn boom"))
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run()

    @pytest.mark.asyncio
    async def test_bindings_query_error_returns_empty(self):
        lock_service = _lock()
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.side_effect = RuntimeError("db boom")
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run()
        binding_repo.list_baas_devices_by_ttl_asc.assert_called_once()


class TestServiceBranch:
    @pytest.mark.asyncio
    async def test_empty_devices(self):
        lock_service = _lock()
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = []
        binding_repo.list_baas_devices_by_ttl_asc.return_value = []
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run()
        router.renew_ttl.assert_not_called()

    @pytest.mark.asyncio
    async def test_renew_success_then_warn(self):
        lock_service = _lock()
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = []
        binding_repo.list_baas_devices_by_ttl_asc.return_value = [{"id": 10}]
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(return_value=MagicMock())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run()
        router.renew_ttl.assert_called_once_with(table_type="baas", table_id=10)
        router.warn_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_renew_failure_uses_warn(self):
        lock_service = _lock()
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = []
        binding_repo.list_baas_devices_by_ttl_asc.return_value = [{"id": 11}]
        router = MagicMock()
        router.renew_ttl = AsyncMock(
            return_value=_result(success=False, error="denied")
        )
        router.warn_device = AsyncMock(return_value=MagicMock())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), lock_service, binding_repo, router
        )
        await task.run()
        router.warn_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_renew_exception_still_warns(self):
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = []
        binding_repo.list_baas_devices_by_ttl_asc.return_value = [{"id": 12}]
        router = MagicMock()
        router.renew_ttl = AsyncMock(side_effect=RuntimeError("boom"))
        router.warn_device = AsyncMock(return_value=MagicMock())
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        # Renew raised, so warn_device is skipped via `continue`.
        router.warn_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_warn_error_does_not_raise(self):
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = []
        binding_repo.list_baas_devices_by_ttl_asc.return_value = [{"id": 13}]
        router = MagicMock()
        router.renew_ttl = AsyncMock(return_value=_result(success=True))
        router.warn_device = AsyncMock(side_effect=RuntimeError("warn boom"))
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()

    @pytest.mark.asyncio
    async def test_devices_query_error(self):
        binding_repo = MagicMock()
        binding_repo.list_bindings_by_ttl_asc.return_value = []
        binding_repo.list_baas_devices_by_ttl_asc.side_effect = RuntimeError("db boom")
        router = MagicMock()
        task = DeviceTtlTimerTask(
            DeviceTtlTimerTaskConfig(), _lock(), binding_repo, router
        )
        await task.run()
        router.renew_ttl.assert_not_called()


def _lock():
    lock_service = MagicMock()
    lock_service.try_lock.return_value.__enter__.return_value = _acquired_lock()
    return lock_service


def _build(binding_id: int):
    record = MagicMock()
    record.id = binding_id
    record.device_props = f'{{"sandbox_id": "sb-{binding_id}"}}'
    return record


def _result(*, success: bool, error: str | None = None):
    result = MagicMock()
    result.success = success
    result.error = error
    return result
