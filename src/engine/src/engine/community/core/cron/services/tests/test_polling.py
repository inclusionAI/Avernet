"""
Unit tests for engine.community.core.cron.services.polling

Tests cover:
- CronPollingService construction and lifecycle (start/stop).
- _init_last_run_times: initialises from latest run per job.
- _check_all_jobs: filters jobs without notify, handles timeouts and errors.
- _check_single_job: new run detection, duplicate filtering, queue management.
- _process_run / _process_run_safe: callback invocation, timeout, cancellation.
- _cleanup_pending_notifications: removes done tasks.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from engine.community.plugin_api.cron.models import CronJob, CronNotifyConfig, CronRunRecord
from engine.community.core.cron.services.polling import CronPollingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(
    job_id: str = "job-aaa",
    name: str = "test",
    notify: CronNotifyConfig | None = None,
) -> CronJob:
    return CronJob(
        id=job_id,
        name=name,
        schedule={"kind": "cron", "expr": "0 8 * * *"},
        payload={"kind": "agentTurn", "message": "hi"},
        created_at_ms=1000,
        updated_at_ms=1001,
        notify=notify,
    )


def _make_run(finished_at_ms: int = 2000, status: str = "ok") -> CronRunRecord:
    return CronRunRecord(
        job_id="job-aaa",
        started_at_ms=1000,
        finished_at_ms=finished_at_ms,
        status=status,
        duration_ms=1000,
    )


def _make_mock_api(jobs=None, runs=None):
    api = AsyncMock()
    api.list_jobs = AsyncMock(return_value=jobs or [])
    api.get_runs = AsyncMock(return_value=runs or [])
    return api


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestCronPollingServiceInit:
    def test_default_attributes(self):
        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api)
        assert svc.engine == "openclaw"
        assert svc.poll_interval == 30
        assert svc._running is False
        assert svc._task is None
        assert svc.notify_callback is None

    def test_custom_interval_and_callback(self):
        api = _make_mock_api()
        cb = AsyncMock()
        svc = CronPollingService(engine="moltis", cron_api=api, poll_interval_secs=10, notify_callback=cb)
        assert svc.poll_interval == 10
        assert svc.notify_callback is cb


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestStartStop:
    async def test_start_sets_running_and_creates_task(self):
        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api, poll_interval_secs=3600)
        await svc.start()
        try:
            assert svc._running is True
            assert svc._task is not None
        finally:
            await svc.stop(graceful=False)

    async def test_stop_sets_running_false(self):
        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api, poll_interval_secs=3600)
        await svc.start()
        await svc.stop(graceful=False)
        assert svc._running is False

    async def test_stop_graceful_waits_for_task(self):
        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api, poll_interval_secs=3600)
        await svc.start()
        svc._running = False  # signal loop to exit on next iteration
        await svc.stop(graceful=True)
        assert not svc._running

    async def test_stop_graceful_timeout_cancels_task(self):
        """If the poll loop doesn't finish within 5s, it should be force-cancelled."""
        api = _make_mock_api()

        async def slow_check():
            await asyncio.sleep(60)

        api.list_jobs = slow_check
        svc = CronPollingService(engine="openclaw", cron_api=api, poll_interval_secs=1)

        await svc.start()
        # Give the loop a moment to enter the slow call
        await asyncio.sleep(0.05)

        with patch.object(svc._task, "cancel", wraps=svc._task.cancel) as mock_cancel:
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                await svc.stop(graceful=True)
            mock_cancel.assert_called()

    async def test_stop_without_task_does_not_raise(self):
        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api)
        svc._running = False
        await svc.stop()  # _task is None


# ---------------------------------------------------------------------------
# _init_last_run_times
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestInitLastRunTimes:
    async def test_records_latest_finished_at_ms(self):
        job = _make_job(notify=CronNotifyConfig(enabled=True))
        run = _make_run(finished_at_ms=5000)
        api = _make_mock_api(jobs=[job], runs=[run])

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._init_last_run_times()

        assert svc._last_run_times[job.id] == 5000

    async def test_skips_jobs_with_notify_disabled(self):
        job = _make_job(notify=CronNotifyConfig(enabled=False))
        api = _make_mock_api(jobs=[job])

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._init_last_run_times()

        assert job.id not in svc._last_run_times

    async def test_skips_jobs_with_no_notify(self):
        job = _make_job(notify=None)
        api = _make_mock_api(jobs=[job])

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._init_last_run_times()

        assert job.id not in svc._last_run_times

    async def test_handles_get_runs_exception(self):
        job = _make_job(notify=CronNotifyConfig(enabled=True))
        api = _make_mock_api(jobs=[job])
        api.get_runs = AsyncMock(side_effect=RuntimeError("network error"))

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._init_last_run_times()  # should not raise

        assert job.id not in svc._last_run_times

    async def test_handles_list_jobs_exception(self):
        api = _make_mock_api()
        api.list_jobs = AsyncMock(side_effect=RuntimeError("conn failed"))

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._init_last_run_times()  # should not raise


# ---------------------------------------------------------------------------
# _check_all_jobs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCheckAllJobs:
    async def test_skips_jobs_without_notify_enabled(self):
        job_no_notify = _make_job(job_id="j1", notify=None)
        job_disabled = _make_job(job_id="j2", notify=CronNotifyConfig(enabled=False))
        api = _make_mock_api(jobs=[job_no_notify, job_disabled])

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._check_all_jobs()

        api.get_runs.assert_not_called()

    async def test_checks_jobs_with_notify_enabled(self):
        job = _make_job(notify=CronNotifyConfig(enabled=True))
        api = _make_mock_api(jobs=[job], runs=[])

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._check_all_jobs()

        api.get_runs.assert_called_once_with(job.id, limit=5)

    async def test_handles_list_jobs_timeout(self):
        api = _make_mock_api()
        api.list_jobs = AsyncMock(side_effect=asyncio.TimeoutError)

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._check_all_jobs()  # should not raise

    async def test_handles_list_jobs_exception(self):
        api = _make_mock_api()
        api.list_jobs = AsyncMock(side_effect=RuntimeError("error"))

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._check_all_jobs()  # should not raise


# ---------------------------------------------------------------------------
# _check_single_job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCheckSingleJob:
    async def test_no_new_runs_does_not_trigger_callback(self):
        callback = AsyncMock()
        job = _make_job(notify=CronNotifyConfig(enabled=True))
        run = _make_run(finished_at_ms=1000)
        api = _make_mock_api(jobs=[job], runs=[run])

        svc = CronPollingService(engine="openclaw", cron_api=api, notify_callback=callback)
        svc._last_run_times[job.id] = 1000  # already seen

        await svc._check_single_job(job)
        # Allow any spawned tasks to complete
        await asyncio.sleep(0.05)

        callback.assert_not_called()

    async def test_new_run_updates_last_run_time(self):
        callback = AsyncMock()
        job = _make_job(notify=CronNotifyConfig(enabled=True))
        run = _make_run(finished_at_ms=5000)
        api = _make_mock_api(jobs=[job], runs=[run])

        svc = CronPollingService(engine="openclaw", cron_api=api, notify_callback=callback)
        svc._last_run_times[job.id] = 0

        await svc._check_single_job(job)
        await asyncio.sleep(0.1)

        assert svc._last_run_times[job.id] == 5000

    async def test_handles_get_runs_timeout(self):
        job = _make_job(notify=CronNotifyConfig(enabled=True))
        api = _make_mock_api(jobs=[job])
        api.get_runs = AsyncMock(side_effect=asyncio.TimeoutError)

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._check_single_job(job)  # should not raise

    async def test_handles_get_runs_exception(self):
        job = _make_job(notify=CronNotifyConfig(enabled=True))
        api = _make_mock_api(jobs=[job])
        api.get_runs = AsyncMock(side_effect=RuntimeError("error"))

        svc = CronPollingService(engine="openclaw", cron_api=api)
        await svc._check_single_job(job)  # should not raise

    async def test_queue_full_cancels_old_tasks(self):
        """When pending notifications exceed MAX_PENDING_NOTIFICATIONS, old tasks are cancelled."""
        job = _make_job(notify=CronNotifyConfig(enabled=True))
        # 3 new runs to process
        runs = [_make_run(finished_at_ms=i) for i in range(1, 4)]
        api = _make_mock_api(runs=runs)

        svc = CronPollingService(engine="openclaw", cron_api=api)
        svc._last_run_times[job.id] = 0

        # Fill the pending queue with fake non-done tasks
        old_tasks = set()
        for _ in range(svc.MAX_PENDING_NOTIFICATIONS):
            fake_task = MagicMock(spec=asyncio.Task)
            fake_task.done.return_value = False
            fake_task.cancel = MagicMock()
            old_tasks.add(fake_task)
        svc._pending_notifications = old_tasks

        await svc._check_single_job(job)

        # Some old tasks should have been cancelled to make room
        cancelled = sum(1 for t in old_tasks if t.cancel.called)
        assert cancelled > 0


# ---------------------------------------------------------------------------
# _process_run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestProcessRun:
    async def test_invokes_notify_callback(self):
        callback = AsyncMock()
        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api, notify_callback=callback)

        job = _make_job()
        run = _make_run()
        await svc._process_run(job, run)

        callback.assert_called_once_with(job, run)

    async def test_handles_callback_exception(self):
        callback = AsyncMock(side_effect=RuntimeError("ding failed"))
        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api, notify_callback=callback)

        job = _make_job()
        run = _make_run()
        await svc._process_run(job, run)  # should not raise

    async def test_no_callback_does_not_raise(self):
        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api)

        job = _make_job()
        run = _make_run()
        await svc._process_run(job, run)  # should not raise


# ---------------------------------------------------------------------------
# _process_run_safe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestProcessRunSafe:
    async def test_timeout_does_not_raise(self):
        async def slow_callback(job, run):
            await asyncio.sleep(100)

        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api, notify_callback=slow_callback)

        job = _make_job()
        run = _make_run()

        def fake_wait_for(coro, timeout):
            # The mocked wait_for raises TimeoutError before awaiting, so close
            # the coroutine it was handed to avoid a "never awaited" leak.
            coro.close()
            raise asyncio.TimeoutError()

        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            await svc._process_run_safe(job, run)  # should not raise

    async def test_cancelled_error_is_reraised(self):
        """CancelledError propagates out of _process_run_safe (not swallowed)."""
        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api)

        job = _make_job()
        run = _make_run()

        async def cancel_on_process(j, r):
            raise asyncio.CancelledError()

        with patch.object(svc, "_process_run", new=AsyncMock(side_effect=cancel_on_process)):
            # _process_run_safe catches CancelledError then re-raises
            with pytest.raises(asyncio.CancelledError):
                await svc._process_run_safe(job, run)


# ---------------------------------------------------------------------------
# _cleanup_pending_notifications
# ---------------------------------------------------------------------------

class TestCleanupPendingNotifications:
    def test_removes_done_tasks(self):
        api = _make_mock_api()
        svc = CronPollingService(engine="openclaw", cron_api=api)

        done_task = MagicMock(spec=asyncio.Task)
        done_task.done.return_value = True
        pending_task = MagicMock(spec=asyncio.Task)
        pending_task.done.return_value = False

        svc._pending_notifications = {done_task, pending_task}
        svc._cleanup_pending_notifications()

        assert done_task not in svc._pending_notifications
        assert pending_task in svc._pending_notifications
