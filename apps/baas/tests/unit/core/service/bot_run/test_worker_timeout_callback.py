"""Deterministic regressions for timeout/result/callback ownership."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from secbaas.community.core.service.bot_run._executor import (
    RequeuedToPendingError,
    ResultGuardExecutor,
)
from secbaas.community.core.service.callback import HttpCallback

from .test_worker import _insert, _worker
from .test_worker import _mock_db_manager as _mock_db_manager
from .test_worker import db as db
from .test_worker import queue as queue
from .test_worker import repo as repo


class _ControlledExecutor:
    def __init__(self, repo):
        self.repo = repo
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finish = "complete"

    async def execute(self, record):
        self.repo.update_status(record.run_id, "RUNNING")
        self.started.set()
        await self.release.wait()
        if self.finish == "requeue":
            raise RequeuedToPendingError(record.run_id, "session-1")
        self.repo.update_result(record.run_id, "done", None)


def _setup(repo, queue):
    run_id = _insert(repo, queue, "bot-1", meta={"callback_function": "cb"})
    record = queue.claim_pending_by_bot("bot-1", "worker-1")
    executor = _ControlledExecutor(repo)
    sent = []

    async def callback(rid):
        sent.append(repo.get_by_run_id(rid).status)
        await asyncio.sleep(0)  # HTTP I/O yields after reading the payload.

    worker = _worker(
        queue,
        repo,
        ResultGuardExecutor(executor, repo),
        post_run_callback_factories={"cb": callback},
    )
    return worker, record, executor, sent


def _start(worker, record):
    worker._active += 1
    task = asyncio.create_task(worker._run_one(record))
    worker._running_tasks[record.run_id] = task
    return task


async def test_local_timeout_sends_only_terminal_callback(repo, queue):
    worker, record, executor, sent = _setup(repo, queue)
    task = _start(worker, record)
    try:
        await asyncio.wait_for(executor.started.wait(), 1)
        record.meta["timeout"] = -1
        await worker._terminate_local_timeout(record)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sent == ["TIME_OUT"]
        assert queue.get_by_run_id(record.run_id).status == "DONE"
        assert worker.active_count == 0
        assert record.run_id not in worker._running_tasks
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_execution_watchers_stopped_before_slow_callback(repo, queue):
    worker, record, executor, sent = _setup(repo, queue)
    guardian_stopped = asyncio.Event()
    poll_stopped = asyncio.Event()
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def guardian(*args):
        try:
            await asyncio.Event().wait()
        finally:
            guardian_stopped.set()

    async def poll(*args):
        try:
            await asyncio.Event().wait()
        finally:
            poll_stopped.set()

    async def callback(rid):
        assert guardian_stopped.is_set()
        assert poll_stopped.is_set()
        assert rid not in worker._running_tasks
        sent.append(repo.get_by_run_id(rid).status)
        callback_started.set()
        await release_callback.wait()

    worker._callback_factories["cb"] = callback
    with (
        patch.object(worker, "_run_guardian", guardian),
        patch.object(worker, "_abort_poll_loop", poll),
    ):
        task = _start(worker, record)
        try:
            await asyncio.wait_for(executor.started.wait(), 1)
            executor.release.set()
            await asyncio.wait_for(callback_started.wait(), 1)
            record.meta["timeout"] = -1
            release_callback.set()
            await asyncio.wait_for(task, 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert sent == ["COMPLETED"]
    assert queue.get_by_run_id(record.run_id).status == "DONE"


async def test_completed_result_wins_over_late_timeout(repo, queue):
    worker, record, executor, sent = _setup(repo, queue)
    task = _start(worker, record)
    await asyncio.wait_for(executor.started.wait(), 1)
    repo.update_result(record.run_id, "done", None)
    record.meta["timeout"] = -1
    await worker._terminate_local_timeout(record)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sent == ["COMPLETED"]
    assert queue.get_by_run_id(record.run_id).status == "DONE"


async def test_requeue_does_not_finalize_or_callback(repo, queue):
    worker, record, executor, sent = _setup(repo, queue)
    executor.finish = "requeue"
    task = _start(worker, record)
    await asyncio.wait_for(executor.started.wait(), 1)
    executor.release.set()
    await task
    assert sent == []
    assert queue.get_by_run_id(record.run_id).status == "PENDING"


async def test_timeout_persistence_failure_preserves_queue_and_skips_callback(
    repo, queue
):
    worker, record, executor, sent = _setup(repo, queue)
    task = _start(worker, record)
    await asyncio.wait_for(executor.started.wait(), 1)
    record.meta["timeout"] = -1
    with patch.object(repo, "update_timeout", side_effect=RuntimeError("write failed")):
        await worker._terminate_local_timeout(record)
        with pytest.raises(RuntimeError, match="write failed"):
            await task
    assert sent == []
    assert repo.get_by_run_id(record.run_id).status == "RUNNING"
    assert queue.get_by_run_id(record.run_id).status == "RUNNING"
    assert worker.active_count == 0


async def test_real_guardian_deadline_sends_once(repo, queue):
    worker, record, executor, sent = _setup(repo, queue)
    deadline_ready = asyncio.Event()

    async def guardian(rec, deadline):
        await deadline_ready.wait()
        await type(worker)._run_guardian(
            worker, rec, datetime.now() - timedelta(seconds=1)
        )

    with patch.object(worker, "_run_guardian", guardian):
        task = _start(worker, record)
        await asyncio.wait_for(executor.started.wait(), 1)
        record.meta["timeout"] = -1
        deadline_ready.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    assert sent == ["TIME_OUT"]
    assert queue.get_by_run_id(record.run_id).status == "DONE"


@pytest.mark.parametrize("status", ["PENDING", "RUNNING"])
async def test_http_callback_rejects_nonterminal_result(repo, queue, status):
    run_id = "http-nonterminal"
    repo.insert_run(
        run_id=run_id,
        bot_id="bot-1",
        api_key_prefix="sk-",
        message_long="m",
        metadata={"callback_url": "https://example.invalid/callback"},
    )
    repo.update_status(run_id, status)
    callback = HttpCallback(repo)
    with patch.object(callback, "_send", new=AsyncMock()) as send:
        await callback(run_id)
    send.assert_not_called()


async def test_pending_timeout_scan_sends_terminal_callback_once(repo, queue):
    run_id = _insert(
        repo, queue, "bot-1", meta={"timeout": -1, "callback_function": "cb"}
    )
    executor = _ControlledExecutor(repo)
    sent = []

    async def callback(rid):
        sent.append(repo.get_by_run_id(rid).status)

    worker = _worker(
        queue,
        repo,
        ResultGuardExecutor(executor, repo),
        post_run_callback_factories={"cb": callback},
    )
    await worker._timeout_scan_once()
    await worker._timeout_scan_once()
    assert sent == ["TIME_OUT"]
    assert queue.get_by_run_id(run_id).status == "DONE"
    assert not executor.started.is_set()


async def test_stale_timeout_write_failure_preserves_queue_and_skips_callback(
    repo, queue
):
    worker, record, executor, sent = _setup(repo, queue)
    record.last_heartbeat = datetime.now() - timedelta(seconds=300)
    with (
        patch.object(queue, "scan_timeout", return_value=[record]),
        patch.object(repo, "update_timeout", side_effect=RuntimeError("write failed")),
    ):
        with pytest.raises(RuntimeError, match="write failed"):
            await worker._timeout_scan_once()
    assert sent == []
    assert queue.get_by_run_id(record.run_id).status == "RUNNING"


async def test_abort_waits_for_engine_notifier_before_finalizing(repo, queue):
    worker, record, executor, sent = _setup(repo, queue)
    notifier_started = asyncio.Event()
    release_notifier = asyncio.Event()
    notifier_finished = asyncio.Event()

    async def notifier(session_id, run_id):
        notifier_started.set()
        await release_notifier.wait()
        notifier_finished.set()

    worker._engine_abort_notifier = notifier
    task = _start(worker, record)
    try:
        await asyncio.wait_for(executor.started.wait(), 1)
        repo.update_aborted(record.run_id, "test abort")
        queue.update_meta(record.run_id, {"abort_requested": True})
        await asyncio.wait_for(notifier_started.wait(), 3)
        assert not task.done()
        release_notifier.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert notifier_finished.is_set()
        assert sent == ["ABORTED"]
        assert queue.get_by_run_id(record.run_id).status == "DONE"
    finally:
        release_notifier.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
