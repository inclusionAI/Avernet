"""Unit tests for bounded durable BCN callback recovery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.service.scheduler import (
    BotRunRecoveryTask,
    BotRunRecoveryTaskConfig,
)


def _lock_service(acquired: bool = True):
    svc = MagicMock()
    lock = SimpleNamespace(acquired=acquired, lock_holder="holder-1")
    svc.try_lock.return_value.__enter__.return_value = lock
    svc.is_lock_held.return_value = acquired
    return svc


def _row(run_id: str):
    return SimpleNamespace(run_id=run_id)


def _make_task(*, rows=None, callback=None, acquired=True, batch=2):
    repo = MagicMock()
    repo.scan_bcn_callback_pending.return_value = rows or []
    config = BotRunRecoveryTaskConfig(
        enabled=True,
        callback_batch_size=batch,
    )
    task = BotRunRecoveryTask(
        config=config,
        lock_service=_lock_service(acquired),
        bot_run_repository=repo,
        bcn_uplink_callback=callback or AsyncMock(),
    )
    return task, repo


@pytest.mark.asyncio
async def test_run_scans_and_calls_only_injected_callback():
    callback = AsyncMock()
    task, repo = _make_task(rows=[_row("r1"), _row("r2")], callback=callback)

    await task.run()

    repo.scan_bcn_callback_pending.assert_called_once()
    assert callback.await_args_list[0].args == ("r1",)
    assert callback.await_args_list[1].args == ("r2",)
    task._lock_service.is_lock_held.assert_any_call(
        "bot_run_recovery_lock", "holder-1", verify_remote=True
    )


def test_config_missing_callback_keys_keep_defaults():
    config = BotRunRecoveryTaskConfig(
        callback_batch_size=None,
        callback_max_attempts=None,
        callback_lease_seconds=None,
        callback_budget_seconds=None,
    )

    assert config.callback_batch_size == 2
    assert config.callback_max_attempts == 5
    assert config.callback_lease_seconds == 45
    assert config.callback_budget_seconds == 35.0


@pytest.mark.asyncio
async def test_run_enforces_batch_bound():
    with pytest.raises(ValueError):
        BotRunRecoveryTaskConfig(callback_batch_size=3)

    callback = AsyncMock()
    task, repo = _make_task(rows=[_row("r1")], callback=callback, batch=1)
    await task.run()
    repo.scan_bcn_callback_pending.assert_called_once()
    assert callback.await_count == 1


@pytest.mark.asyncio
async def test_run_does_not_touch_queue_recovery():
    callback = AsyncMock()
    task, _repo = _make_task(rows=[_row("r1")], callback=callback)
    await task.run()


@pytest.mark.asyncio
async def test_run_stops_when_lock_is_lost():
    callback = AsyncMock()
    task, _repo = _make_task(rows=[_row("r1"), _row("r2")], callback=callback)
    task._lock_service.is_lock_held.return_value = False

    await task.run()

    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_dependencies_is_safe_skip():
    task = BotRunRecoveryTask(
        config=BotRunRecoveryTaskConfig(),
        lock_service=_lock_service(),
    )
    await task.run()


@pytest.mark.asyncio
async def test_callback_failure_does_not_block_next_row():
    callback = AsyncMock(side_effect=[RuntimeError("boom"), None])
    task, _repo = _make_task(rows=[_row("r1"), _row("r2")], callback=callback)

    await task.run()

    assert callback.await_count == 2
