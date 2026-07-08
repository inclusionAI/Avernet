"""
Unit tests for engine.community.core.cron.models

Validates Pydantic model construction, defaults, optional fields,
and type coercion/validation errors.
"""
import pytest
from pydantic import ValidationError

from engine.community.plugin_api.cron.models import (
    CronJob,
    CronNotifyConfig,
    CronNotifyPatch,
    CronRunRecord,
    CronStatus,
    CreateJobRequest,
    UpdateJobRequest,
)


# ---------------------------------------------------------------------------
# CronNotifyConfig
# ---------------------------------------------------------------------------

class TestCronNotifyConfig:
    def test_defaults(self):
        cfg = CronNotifyConfig()
        assert cfg.enabled is True
        assert cfg.user_ids is None

    def test_explicit_values(self):
        cfg = CronNotifyConfig(enabled=False, user_ids=["u1", "u2"])
        assert cfg.enabled is False
        assert cfg.user_ids == ["u1", "u2"]

    def test_invalid_enabled_type_raises(self):
        # Pydantic v2 coerces strings to bool, but a dict cannot be coerced.
        with pytest.raises(ValidationError):
            CronNotifyConfig(enabled={"nested": "dict"})

    def test_empty_user_ids_list(self):
        cfg = CronNotifyConfig(user_ids=[])
        assert cfg.user_ids == []


# ---------------------------------------------------------------------------
# CronJob
# ---------------------------------------------------------------------------

class TestCronJob:
    def _make_job(self, **overrides):
        base = dict(
            id="job-001",
            name="daily-report",
            schedule={"kind": "cron", "expr": "0 8 * * *", "tz": "Asia/Shanghai"},
            payload={"kind": "agentTurn", "message": "Hello"},
            created_at_ms=1_700_000_000_000,
            updated_at_ms=1_700_000_001_000,
        )
        base.update(overrides)
        return CronJob(**base)

    def test_minimal_construction(self):
        job = self._make_job()
        assert job.id == "job-001"
        assert job.name == "daily-report"
        assert job.enabled is True  # default
        assert job.session_target == "isolated"  # default
        assert job.state == {}  # default_factory
        assert job.notify is None  # optional

    def test_custom_fields(self):
        notify = CronNotifyConfig(enabled=True, user_ids=["abc"])
        job = self._make_job(
            enabled=False,
            session_target="persistent",
            state={"run_count": 5},
            notify=notify,
        )
        assert job.enabled is False
        assert job.session_target == "persistent"
        assert job.state == {"run_count": 5}
        assert job.notify.user_ids == ["abc"]

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            CronJob(
                name="x",
                schedule={},
                payload={},
                created_at_ms=0,
                updated_at_ms=0,
                # id is missing
            )

    def test_state_default_factory_is_independent(self):
        job1 = self._make_job(id="j1")
        job2 = self._make_job(id="j2")
        job1.state["key"] = "value"
        assert "key" not in job2.state


# ---------------------------------------------------------------------------
# CronRunRecord
# ---------------------------------------------------------------------------

class TestCronRunRecord:
    def _make_run(self, **overrides):
        base = dict(
            job_id="job-001",
            started_at_ms=1_700_000_000_000,
            finished_at_ms=1_700_000_005_000,
            status="ok",
            duration_ms=5000,
        )
        base.update(overrides)
        return CronRunRecord(**base)

    def test_minimal_construction(self):
        run = self._make_run()
        assert run.status == "ok"
        assert run.error is None
        assert run.output is None
        assert run.input_tokens is None
        assert run.output_tokens is None

    def test_error_status(self):
        run = self._make_run(status="error", error="timeout occurred")
        assert run.status == "error"
        assert run.error == "timeout occurred"

    def test_skipped_status_with_output(self):
        run = self._make_run(status="skipped", output="already done")
        assert run.status == "skipped"
        assert run.output == "already done"

    def test_token_counts(self):
        run = self._make_run(input_tokens=100, output_tokens=200)
        assert run.input_tokens == 100
        assert run.output_tokens == 200

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            CronRunRecord(job_id="j", started_at_ms=0, finished_at_ms=1, status="ok")
            # duration_ms missing


# ---------------------------------------------------------------------------
# CronStatus
# ---------------------------------------------------------------------------

class TestCronStatus:
    def test_running_with_next_run(self):
        status = CronStatus(running=True, job_count=5, enabled_count=3, next_run_at_ms=12345)
        assert status.running is True
        assert status.next_run_at_ms == 12345

    def test_not_running_no_next_run(self):
        status = CronStatus(running=False, job_count=0, enabled_count=0)
        assert status.next_run_at_ms is None


# ---------------------------------------------------------------------------
# CreateJobRequest
# ---------------------------------------------------------------------------

class TestCreateJobRequest:
    def test_defaults(self):
        req = CreateJobRequest(
            name="job",
            schedule={"kind": "cron", "expr": "* * * * *"},
            payload={"kind": "agentTurn"},
        )
        assert req.session_target == "isolated"
        assert req.enabled is True
        assert req.notify is None

    def test_with_notify(self):
        req = CreateJobRequest(
            name="job",
            schedule={},
            payload={},
            notify=CronNotifyConfig(enabled=True, user_ids=["u1"]),
        )
        assert req.notify.user_ids == ["u1"]

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            CreateJobRequest(schedule={}, payload={})  # name missing


# ---------------------------------------------------------------------------
# CronNotifyPatch
# ---------------------------------------------------------------------------

class TestCronNotifyPatch:
    def test_all_none_defaults(self):
        patch = CronNotifyPatch()
        assert patch.enabled is None
        assert patch.user_ids is None

    def test_partial_update_enabled_only(self):
        patch = CronNotifyPatch(enabled=True)
        assert patch.enabled is True
        assert patch.user_ids is None

    def test_partial_update_user_ids_only(self):
        patch = CronNotifyPatch(user_ids=["x", "y"])
        assert patch.enabled is None
        assert patch.user_ids == ["x", "y"]


# ---------------------------------------------------------------------------
# UpdateJobRequest
# ---------------------------------------------------------------------------

class TestUpdateJobRequest:
    def test_empty_update(self):
        req = UpdateJobRequest()
        assert req.name is None
        assert req.schedule is None
        assert req.payload is None
        assert req.enabled is None
        assert req.notify is None

    def test_partial_update(self):
        req = UpdateJobRequest(name="new-name", enabled=False)
        assert req.name == "new-name"
        assert req.enabled is False
        assert req.schedule is None

    def test_update_with_notify_patch(self):
        req = UpdateJobRequest(notify=CronNotifyPatch(enabled=False))
        assert req.notify.enabled is False

# NOTE: tests for the api-layer request schemas (CreateTaskRequest /
# NotifyRequest / UpdateTaskRequest / NotifyUpdateRequest) moved to
# engine.community.api.tests.test_cron_schemas — a core test must not import engine.community.api.*
# (api > core layering, enforced by import-linter).
