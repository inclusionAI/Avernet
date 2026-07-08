"""
Unit tests for engine.community.core.cron.services.systemevent_monitor

Tests cover:
- SystemEventMonitorService construction.
- start / stop lifecycle.
- _init_monitored_jobs: discovers systemEvent jobs, ignores others.
- _check_and_replace: calls _replace_job for unhandled systemEvent jobs,
  skips already-replaced ones, handles list_jobs timeouts and errors.
- _replace_job: full replacement flow, empty-text skip, delete-failure abort,
  model injection, notify passthrough.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from engine.community.plugin_api.cron.models import CronJob, CronNotifyConfig, CreateJobRequest
from engine.community.core.cron.services.systemevent_monitor import (
    SystemEventMonitorService,
    _MonitoredJob,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(
    job_id: str = "job-001",
    name: str = "test",
    payload: dict | None = None,
    enabled: bool = True,
    notify: CronNotifyConfig | None = None,
) -> CronJob:
    return CronJob(
        id=job_id,
        name=name,
        schedule={"kind": "cron", "expr": "0 8 * * *"},
        payload=payload or {"kind": "agentTurn", "message": "hi"},
        created_at_ms=1000,
        updated_at_ms=1001,
        enabled=enabled,
        notify=notify,
    )


def _systemevent_job(
    job_id: str = "se-job-001",
    text: str = "run report",
    notify: CronNotifyConfig | None = None,
) -> CronJob:
    return _make_job(
        job_id=job_id,
        name="se-task",
        payload={"kind": "systemEvent", "text": text},
        notify=notify,
    )


def _make_mock_api(jobs=None):
    api = AsyncMock()
    api.list_jobs = AsyncMock(return_value=jobs or [])
    api.remove_job = AsyncMock(return_value=True)
    api.add_job = AsyncMock(return_value=_make_job(job_id="new-job-001", name="replaced"))
    return api


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestSystemEventMonitorServiceInit:
    def test_default_attributes(self):
        api = _make_mock_api()
        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        assert svc.engine == "openclaw"
        assert svc.poll_interval == 60
        assert svc.default_timeout_secs == 86400
        assert svc.default_model is None
        assert svc._running is False
        assert svc._task is None
        assert svc._monitored_jobs == {}

    def test_custom_params(self):
        api = _make_mock_api()
        svc = SystemEventMonitorService(
            engine="moltis",
            cron_api=api,
            poll_interval_secs=30,
            default_timeout_secs=3600,
            default_model="gpt-4",
        )
        assert svc.poll_interval == 30
        assert svc.default_timeout_secs == 3600
        assert svc.default_model == "gpt-4"


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestStartStop:
    async def test_start_sets_running_and_creates_task(self):
        api = _make_mock_api()
        svc = SystemEventMonitorService(engine="openclaw", cron_api=api, poll_interval_secs=3600)
        await svc.start()
        try:
            assert svc._running is True
            assert svc._task is not None
        finally:
            await svc.stop(graceful=False)

    async def test_stop_sets_running_false(self):
        api = _make_mock_api()
        svc = SystemEventMonitorService(engine="openclaw", cron_api=api, poll_interval_secs=3600)
        await svc.start()
        await svc.stop(graceful=False)
        assert svc._running is False

    async def test_stop_without_task_does_not_raise(self):
        api = _make_mock_api()
        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        svc._running = False
        await svc.stop()  # _task is None

    async def test_stop_graceful_timeout_cancels_task(self):
        api = _make_mock_api()
        svc = SystemEventMonitorService(engine="openclaw", cron_api=api, poll_interval_secs=3600)
        await svc.start()
        await asyncio.sleep(0.02)

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            with patch.object(svc._task, "cancel", wraps=svc._task.cancel) as mock_cancel:
                await svc.stop(graceful=True)
            mock_cancel.assert_called()

    async def test_stop_non_graceful_cancels_immediately(self):
        api = _make_mock_api()
        svc = SystemEventMonitorService(engine="openclaw", cron_api=api, poll_interval_secs=3600)
        await svc.start()
        await svc.stop(graceful=False)
        assert not svc._running


# ---------------------------------------------------------------------------
# _init_monitored_jobs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestInitMonitoredJobs:
    async def test_records_systemevent_jobs(self):
        se_job = _systemevent_job(job_id="se-001")
        regular_job = _make_job(job_id="reg-001")
        api = _make_mock_api(jobs=[se_job, regular_job])

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._init_monitored_jobs()

        assert "se-001" in svc._monitored_jobs
        assert "reg-001" not in svc._monitored_jobs

    async def test_ignores_non_systemevent_jobs(self):
        job = _make_job(payload={"kind": "agentTurn", "message": "x"})
        api = _make_mock_api(jobs=[job])

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._init_monitored_jobs()

        assert svc._monitored_jobs == {}

    async def test_handles_list_jobs_exception(self):
        api = _make_mock_api()
        api.list_jobs = AsyncMock(side_effect=RuntimeError("conn failed"))

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._init_monitored_jobs()  # should not raise
        assert svc._monitored_jobs == {}


# ---------------------------------------------------------------------------
# _check_and_replace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCheckAndReplace:
    async def test_calls_replace_for_new_systemevent_job(self):
        se_job = _systemevent_job()
        api = _make_mock_api(jobs=[se_job])

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        svc._replace_job = AsyncMock()

        await svc._check_and_replace()

        svc._replace_job.assert_called_once_with(se_job)

    async def test_skips_already_replaced_job(self):
        se_job = _systemevent_job(job_id="se-001")
        api = _make_mock_api(jobs=[se_job])

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        svc._monitored_jobs["se-001"] = _MonitoredJob(job_id="se-001", replaced=True)
        svc._replace_job = AsyncMock()

        await svc._check_and_replace()

        svc._replace_job.assert_not_called()

    async def test_skips_non_systemevent_jobs(self):
        regular_job = _make_job()
        api = _make_mock_api(jobs=[regular_job])

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        svc._replace_job = AsyncMock()

        await svc._check_and_replace()

        svc._replace_job.assert_not_called()

    async def test_handles_list_jobs_timeout(self):
        api = _make_mock_api()
        api.list_jobs = AsyncMock(side_effect=asyncio.TimeoutError)

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._check_and_replace()  # should not raise

    async def test_handles_list_jobs_exception(self):
        api = _make_mock_api()
        api.list_jobs = AsyncMock(side_effect=RuntimeError("error"))

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._check_and_replace()  # should not raise


# ---------------------------------------------------------------------------
# _replace_job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestReplaceJob:
    async def test_successful_replacement(self):
        se_job = _systemevent_job(job_id="se-001", text="do something")
        new_job = _make_job(job_id="new-001")
        api = _make_mock_api()
        api.add_job = AsyncMock(return_value=new_job)

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._replace_job(se_job)

        api.remove_job.assert_called_once_with("se-001")
        api.add_job.assert_called_once()
        created_req: CreateJobRequest = api.add_job.call_args[0][0]
        assert created_req.payload["kind"] == "agentTurn"
        assert created_req.payload["message"] == "do something"
        assert created_req.payload["deliver"] is False

        # Both old and new IDs should be in monitored_jobs and marked replaced
        assert svc._monitored_jobs["se-001"].replaced is True
        assert svc._monitored_jobs["new-001"].replaced is True

    async def test_skips_when_no_text(self):
        se_job = _systemevent_job(job_id="se-001", text="")
        api = _make_mock_api()

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._replace_job(se_job)

        api.remove_job.assert_not_called()
        api.add_job.assert_not_called()
        # Marked as replaced to avoid reprocessing
        assert svc._monitored_jobs["se-001"].replaced is True

    async def test_aborts_when_remove_returns_false(self):
        se_job = _systemevent_job(text="run me")
        api = _make_mock_api()
        api.remove_job = AsyncMock(return_value=False)

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._replace_job(se_job)

        api.add_job.assert_not_called()

    async def test_injects_model_when_default_model_set(self):
        se_job = _systemevent_job(text="task")
        new_job = _make_job(job_id="new-001")
        api = _make_mock_api()
        api.add_job = AsyncMock(return_value=new_job)

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api, default_model="claude-3")
        await svc._replace_job(se_job)

        req: CreateJobRequest = api.add_job.call_args[0][0]
        assert req.payload["model"] == "claude-3"

    async def test_no_model_in_payload_when_default_model_is_none(self):
        se_job = _systemevent_job(text="task")
        new_job = _make_job(job_id="new-001")
        api = _make_mock_api()
        api.add_job = AsyncMock(return_value=new_job)

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api, default_model=None)
        await svc._replace_job(se_job)

        req: CreateJobRequest = api.add_job.call_args[0][0]
        assert "model" not in req.payload

    async def test_uses_default_timeout_secs(self):
        se_job = _systemevent_job(text="go")
        new_job = _make_job(job_id="new-001")
        api = _make_mock_api()
        api.add_job = AsyncMock(return_value=new_job)

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api, default_timeout_secs=3600)
        await svc._replace_job(se_job)

        req: CreateJobRequest = api.add_job.call_args[0][0]
        assert req.payload["timeout_secs"] == 3600

    async def test_passes_through_notify_config(self):
        notify = CronNotifyConfig(enabled=True, user_ids=["u1"])
        se_job = _systemevent_job(text="go", notify=notify)
        new_job = _make_job(job_id="new-001")
        api = _make_mock_api()
        api.add_job = AsyncMock(return_value=new_job)

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._replace_job(se_job)

        req: CreateJobRequest = api.add_job.call_args[0][0]
        assert req.notify == notify

    async def test_preserves_job_name_and_schedule(self):
        se_job = _make_job(
            job_id="se-001",
            name="weekly-digest",
            payload={"kind": "systemEvent", "text": "digest"},
        )
        new_job = _make_job(job_id="new-001")
        api = _make_mock_api()
        api.add_job = AsyncMock(return_value=new_job)

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._replace_job(se_job)

        req: CreateJobRequest = api.add_job.call_args[0][0]
        assert req.name == "weekly-digest"
        assert req.schedule == se_job.schedule

    async def test_handles_add_job_exception(self):
        se_job = _systemevent_job(text="task")
        api = _make_mock_api()
        api.add_job = AsyncMock(side_effect=RuntimeError("engine down"))

        svc = SystemEventMonitorService(engine="openclaw", cron_api=api)
        await svc._replace_job(se_job)  # should not raise
