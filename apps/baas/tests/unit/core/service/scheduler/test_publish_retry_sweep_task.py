"""Unit tests for the publish retry sweep task."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.core.service.scheduler import (
    PublishRetrySweepConfig,
    PublishRetrySweepTask,
)


def _make_task(
    *,
    enabled: bool = True,
    dry_run: bool = False,
    acquired: bool = True,
    stale: list | None = None,
) -> PublishRetrySweepTask:
    config = PublishRetrySweepConfig(
        enabled=enabled,
        dry_run=dry_run,
        cron_interval_seconds=60,
        attempt_timeout_seconds=1800,
        batch_limit=100,
    )
    lock_service = MagicMock()
    lock_ctx = MagicMock()
    lock_ctx.acquired = acquired
    lock_service.try_lock.return_value.__enter__ = MagicMock(return_value=lock_ctx)
    lock_service.try_lock.return_value.__exit__ = MagicMock(return_value=False)

    record_repo = MagicMock()
    record_repo.list_stale_processing_records_across_tenants.return_value = stale or []

    publish_service = MagicMock()
    publish_service.sweep_record_attempt = AsyncMock(return_value=True)

    return PublishRetrySweepTask(
        config=config,
        lock_service=lock_service,
        record_repo=record_repo,
        publish_service=publish_service,
    )


def _patch_env():
    return patch(
        "secbaas.community.core.service.scheduler._tasks"
        "._publish_retry_sweep_task.get_current_env",
        return_value="test",
    )


class TestSweepTaskLifecycle:
    def test_name_and_interval(self):
        task = _make_task()
        assert task.name == "publish_retry_sweep"
        assert task.interval_seconds == 60

    async def test_disabled_task_does_nothing(self):
        task = _make_task(enabled=False)
        with _patch_env():
            await task.run()
        task._record_repo.list_stale_processing_records_across_tenants.assert_not_called()

    async def test_dry_run_does_nothing(self):
        task = _make_task(dry_run=True)
        with _patch_env():
            await task.run()
        task._record_repo.list_stale_processing_records_across_tenants.assert_not_called()

    async def test_lock_not_acquired_skips(self):
        task = _make_task(acquired=False)
        with _patch_env():
            await task.run()
        task._record_repo.list_stale_processing_records_across_tenants.assert_not_called()

    async def test_no_stale_records_is_noop(self):
        task = _make_task(stale=[])
        with _patch_env():
            await task.run()
        task._publish_service.sweep_record_attempt.assert_not_awaited()


class TestSweepTaskDetection:
    async def test_stale_record_is_swept(self):
        rec = MagicMock()
        rec.id = 7
        task = _make_task(stale=[rec])
        with _patch_env():
            await task.run()
        task._publish_service.sweep_record_attempt.assert_awaited_once()
        kwargs = task._publish_service.sweep_record_attempt.call_args.kwargs
        assert kwargs["record"] is rec
        assert kwargs["tenant"] == rec.tenant

    async def test_multiple_stale_records_all_swept(self):
        recs = []
        for i in range(3):
            r = MagicMock()
            r.id = i
            recs.append(r)
        task = _make_task(stale=recs)
        with _patch_env():
            await task.run()
        assert task._publish_service.sweep_record_attempt.await_count == 3

    async def test_batch_limit_caps_records(self):
        recs = []
        for i in range(5):
            r = MagicMock()
            r.id = i
            recs.append(r)
        config = PublishRetrySweepConfig(batch_limit=2)
        lock_service = MagicMock()
        lock_ctx = MagicMock()
        lock_ctx.acquired = True
        lock_service.try_lock.return_value.__enter__ = MagicMock(return_value=lock_ctx)
        lock_service.try_lock.return_value.__exit__ = MagicMock(return_value=False)
        record_repo = MagicMock()
        record_repo.list_stale_processing_records_across_tenants.return_value = recs
        publish_service = MagicMock()
        publish_service.sweep_record_attempt = AsyncMock(return_value=True)
        task = PublishRetrySweepTask(
            config=config,
            lock_service=lock_service,
            record_repo=record_repo,
            publish_service=publish_service,
        )
        with _patch_env():
            await task.run()
        assert publish_service.sweep_record_attempt.await_count == 2

    async def test_one_failure_does_not_stop_the_rest(self):
        recs = []
        for i in range(3):
            r = MagicMock()
            r.id = i
            recs.append(r)
        task = _make_task(stale=recs)
        task._publish_service.sweep_record_attempt = AsyncMock(
            side_effect=[RuntimeError("boom"), True, True]
        )
        with _patch_env():
            await task.run()
        assert task._publish_service.sweep_record_attempt.await_count == 3


class TestNoDuplicateAttempts:
    async def test_already_claimed_record_is_not_started_twice(self):
        rec = MagicMock()
        rec.id = 11
        task = _make_task(stale=[rec])
        task._publish_service.sweep_record_attempt = AsyncMock(return_value=False)
        with _patch_env():
            await task.run()
        task._publish_service.sweep_record_attempt.assert_awaited_once()

    async def test_sweep_delegates_claim_decision_to_service(self):
        rec = MagicMock()
        rec.id = 12
        task = _make_task(stale=[rec])
        with _patch_env():
            await task.run()
        kwargs = task._publish_service.sweep_record_attempt.call_args.kwargs
        assert set(kwargs.keys()) == {"record", "tenant"}
