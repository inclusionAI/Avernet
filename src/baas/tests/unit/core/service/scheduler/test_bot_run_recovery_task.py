"""Unit tests for ``BotRunRecoveryTask`` (S6 / Issue 2-C).

Covers the orphans session-lock reaping extension:
- happy path: ``reset_stale_running`` + ``delete_expired_locks_by_prefix`` both called
- reaping failure does not block the main reset path
- lock not acquired: skip both reset and reap
- ``lock_repo`` not injected: graceful no-op

Mock pattern mirrors ``test_deadline_renewal_task.py`` (deep-mock of lock_service
+ repos and the SimpleNamespace lock context).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.service.scheduler import (
    BotRunRecoveryTask,
    BotRunRecoveryTaskConfig,
)

DEFAULT_SESSION_LOCK_PREFIX = "botrun:session:"


def _acquired_lock():
    return SimpleNamespace(acquired=True)


def _not_acquired_lock():
    return SimpleNamespace(acquired=False)


def _lock_service_acquired():
    svc = MagicMock()
    svc.try_lock.return_value.__enter__.return_value = _acquired_lock()
    return svc


def _lock_service_not_acquired():
    svc = MagicMock()
    svc.try_lock.return_value.__enter__.return_value = _not_acquired_lock()
    return svc


def _make_task(
    *,
    enabled: bool = True,
    dry_run: bool = False,
    lock_acquired: bool = True,
    lock_repo: MagicMock | None = None,
) -> tuple[BotRunRecoveryTask, MagicMock, MagicMock, MagicMock]:
    queue_repo = MagicMock()
    queue_repo.reset_stale_running.return_value = 7

    if lock_repo is None:
        lock_repo = MagicMock()
        lock_repo.delete_expired_locks_by_prefix.return_value = 0

    if lock_acquired:
        lock_service = _lock_service_acquired()
    else:
        lock_service = _lock_service_not_acquired()

    config = BotRunRecoveryTaskConfig(
        enabled=enabled,
        dry_run=dry_run,
        stale_seconds=120,
    )
    task = BotRunRecoveryTask(
        config=config,
        lock_service=lock_service,
        queue_repo=queue_repo,
        lock_repo=lock_repo,
    )
    return task, lock_service, queue_repo, lock_repo


# ── happy path ────────────────────────────────────────────────────


async def test_run_resets_and_reaps_orphan_session_locks():
    """S6: 抢到 lock 时 reset_stale_running + delete_expired_locks_by_prefix 都调用。"""
    task, _lock_service, queue_repo, lock_repo = _make_task()
    lock_repo.delete_expired_locks_by_prefix.return_value = 3

    await task.run()

    queue_repo.reset_stale_running.assert_called_once_with(120)
    lock_repo.delete_expired_locks_by_prefix.assert_called_once()
    args, kwargs = lock_repo.delete_expired_locks_by_prefix.call_args
    # 接受 (prefix, now=...) 或 (prefix, now) 两种调用形态
    positional_prefix = args[0] if args else kwargs.get("prefix")
    assert positional_prefix == DEFAULT_SESSION_LOCK_PREFIX
    assert "now" in kwargs


async def test_run_uses_configured_session_lock_prefix():
    """S6: 自定义 session_lock_prefix 时正确透传到 lock_repo。"""
    queue_repo = MagicMock()
    queue_repo.reset_stale_running.return_value = 1
    lock_repo = MagicMock()
    lock_repo.delete_expired_locks_by_prefix.return_value = 0

    config = BotRunRecoveryTaskConfig(
        enabled=True,
        stale_seconds=120,
        session_lock_prefix="custom:prefix:",
    )
    task = BotRunRecoveryTask(
        config=config,
        lock_service=_lock_service_acquired(),
        queue_repo=queue_repo,
        lock_repo=lock_repo,
    )

    await task.run()

    args, kwargs = lock_repo.delete_expired_locks_by_prefix.call_args
    prefix = args[0] if args else kwargs.get("prefix")
    assert prefix == "custom:prefix:"


# ── failure isolation ─────────────────────────────────────────────


async def test_reaping_failure_does_not_block_reset():
    """S6: lock_repo.delete_expired_locks_by_prefix 抛异常时仅 log，不阻塞主流程。"""
    task, _lock_service, queue_repo, lock_repo = _make_task()
    lock_repo.delete_expired_locks_by_prefix.side_effect = RuntimeError("db error")

    # run 不应抛
    await task.run()

    queue_repo.reset_stale_running.assert_called_once_with(120)
    assert lock_repo.delete_expired_locks_by_prefix.called


# ── lock not acquired ─────────────────────────────────────────────


async def test_run_skips_when_lock_not_acquired():
    """S6: 未能抢到 bot_run_recovery_lock 时 reset 与 reap 都跳过。"""
    task, _lock_service, queue_repo, lock_repo = _make_task(lock_acquired=False)

    await task.run()

    queue_repo.reset_stale_running.assert_not_called()
    lock_repo.delete_expired_locks_by_prefix.assert_not_called()


# ── no lock_repo injected ──────────────────────────────────────────


async def test_run_without_lock_repo_skips_reap_gracefully():
    """S6: 旧装配未注入 lock_repo 时重置仍正常工作，reap 静默跳过。"""
    queue_repo = MagicMock()
    queue_repo.reset_stale_running.return_value = 5

    config = BotRunRecoveryTaskConfig(enabled=True, stale_seconds=120)
    task = BotRunRecoveryTask(
        config=config,
        lock_service=_lock_service_acquired(),
        queue_repo=queue_repo,
        lock_repo=None,
    )

    await task.run()

    queue_repo.reset_stale_running.assert_called_once_with(120)


async def test_run_reset_stale_running_raises_propagates():
    """S6: reset_stale_running 抛异常时按既有契约向上抛（保持原行为）。"""
    task, _lock_service, queue_repo, lock_repo = _make_task()
    queue_repo.reset_stale_running.side_effect = RuntimeError("reset boom")

    with pytest.raises(RuntimeError, match="reset boom"):
        await task.run()

    # reap 未被执行（reset 抛异常后 raise 直接跳出）
    lock_repo.delete_expired_locks_by_prefix.assert_not_called()