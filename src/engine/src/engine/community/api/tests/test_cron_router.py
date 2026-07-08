"""
Unit tests for engine.community.api.cron.router

Exercises every endpoint via FastAPI TestClient with a fully-mocked CronService
injected through the module-level set_cron_api() helper that the router already
exposes for testing.  No real engine runtime is required.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from engine.community.api.cron.router import router, set_cron_api
from engine.community.plugin_api.cron.models import (
    CronJob,
    CronNotifyConfig,
    CronRunRecord,
    CronStatus,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_job(job_id: str = "job-1", **kwargs) -> CronJob:
    defaults = dict(
        id=job_id,
        name="Test Job",
        enabled=True,
        schedule={"kind": "cron", "expr": "0 8 * * *", "tz": "Asia/Shanghai"},
        payload={"kind": "agentTurn", "message": "do something", "timeout_secs": 3600},
        session_target="isolated",
        state={},
        notify=None,
        created_at_ms=1_700_000_000_000,
        updated_at_ms=1_700_000_001_000,
    )
    defaults.update(kwargs)
    return CronJob(**defaults)


def _make_status(**kwargs) -> CronStatus:
    defaults = dict(running=True, job_count=2, enabled_count=1, next_run_at_ms=None)
    defaults.update(kwargs)
    return CronStatus(**defaults)


def _make_run_record(job_id: str = "job-1") -> CronRunRecord:
    return CronRunRecord(
        job_id=job_id,
        started_at_ms=1_700_000_000_000,
        finished_at_ms=1_700_000_001_000,
        status="ok",
        error=None,
        duration_ms=1000,
        output="done",
    )


@pytest.fixture()
def mock_cron_api():
    """Return a fresh mock CronService and inject it into the router."""
    api = MagicMock()
    api.list_jobs = AsyncMock(return_value=[_make_job()])
    api.get_job = AsyncMock(return_value=_make_job())
    api.get_status = AsyncMock(return_value=_make_status())
    api.get_running_jobs = AsyncMock(return_value=[{"id": "run-1"}])
    api.add_job = AsyncMock(return_value=_make_job())
    api.update_job = AsyncMock(return_value=_make_job())
    api.remove_job = AsyncMock(return_value=True)
    api.run_job = AsyncMock(return_value={"ok": True})
    api.get_runs = AsyncMock(return_value=[_make_run_record()])
    set_cron_api(api)
    yield api
    # Tear down: clear the override so other tests are unaffected
    set_cron_api(None)  # type: ignore[arg-type]


@pytest.fixture()
def client(mock_cron_api) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── GET /api/cron ─────────────────────────────────────────────────────────────

class TestListTasks:
    def test_success(self, client, mock_cron_api):
        resp = client.get("/api/cron")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["total"] == 1
        assert len(body["data"]) == 1

    def test_service_error_returns_500(self, client, mock_cron_api):
        mock_cron_api.list_jobs.side_effect = RuntimeError("db down")
        resp = client.get("/api/cron")
        assert resp.status_code == 500
        assert "db down" in resp.json()["detail"]


# ── GET /api/cron/status ──────────────────────────────────────────────────────

class TestGetCronStatus:
    def test_success(self, client, mock_cron_api):
        resp = client.get("/api/cron/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["running"] is True
        assert body["data"]["job_count"] == 2

    def test_service_error_returns_500(self, client, mock_cron_api):
        mock_cron_api.get_status.side_effect = RuntimeError("boom")
        resp = client.get("/api/cron/status")
        assert resp.status_code == 500


# ── GET /api/cron/running ─────────────────────────────────────────────────────

class TestGetRunningTasks:
    def test_success(self, client, mock_cron_api):
        resp = client.get("/api/cron/running")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["total"] == 1

    def test_service_error_returns_500(self, client, mock_cron_api):
        mock_cron_api.get_running_jobs.side_effect = RuntimeError("oops")
        resp = client.get("/api/cron/running")
        assert resp.status_code == 500


# ── GET /api/cron/{task_id} ───────────────────────────────────────────────────

class TestGetTask:
    def test_found(self, client, mock_cron_api):
        resp = client.get("/api/cron/job-1")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == "job-1"

    def test_not_found_returns_404(self, client, mock_cron_api):
        mock_cron_api.get_job.return_value = None
        resp = client.get("/api/cron/missing-id")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Task not found"

    def test_service_error_returns_500(self, client, mock_cron_api):
        mock_cron_api.get_job.side_effect = RuntimeError("fail")
        resp = client.get("/api/cron/job-1")
        assert resp.status_code == 500


# ── POST /api/cron ────────────────────────────────────────────────────────────

class TestCreateTask:
    _PAYLOAD = {
        "name": "Daily Report",
        "schedule": "0 9 * * *",
        "command": "run report",
    }

    def test_minimal_create(self, client, mock_cron_api):
        resp = client.post("/api/cron", json=self._PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_full_create_with_model_and_notify(self, client, mock_cron_api):
        payload = {
            **self._PAYLOAD,
            "timezone": "America/New_York",
            "enabled": False,
            "timeout_secs": 120,
            "model": "gpt-4o",
            "notify": {"enabled": True, "user_ids": ["u1", "u2"]},
        }
        resp = client.post("/api/cron", json=payload)
        assert resp.status_code == 200
        # Verify the CreateJobRequest passed to add_job
        call_args = mock_cron_api.add_job.call_args[0][0]
        assert call_args.payload["model"] == "gpt-4o"
        assert call_args.payload["timeout_secs"] == 120
        assert call_args.notify is not None
        assert call_args.notify.user_ids == ["u1", "u2"]

    def test_default_timeout_used_when_not_provided(self, client, mock_cron_api):
        resp = client.post("/api/cron", json=self._PAYLOAD)
        assert resp.status_code == 200
        call_args = mock_cron_api.add_job.call_args[0][0]
        assert call_args.payload["timeout_secs"] == 86400

    def test_kind_passed_to_payload(self, client, mock_cron_api):
        """显式指定 kind 时，应传递到 payload"""
        payload = {**self._PAYLOAD, "kind": "autoInitiate"}
        resp = client.post("/api/cron", json=payload)
        assert resp.status_code == 200
        call_args = mock_cron_api.add_job.call_args[0][0]
        assert call_args.payload["kind"] == "autoInitiate"

    def test_kind_defaults_to_agent_turn_when_not_provided(self, client, mock_cron_api):
        """未指定 kind 时，payload.kind 默认为 agentTurn（relay 必填字段）"""
        resp = client.post("/api/cron", json=self._PAYLOAD)
        assert resp.status_code == 200
        call_args = mock_cron_api.add_job.call_args[0][0]
        assert call_args.payload["kind"] == "agentTurn"

    def test_service_error_returns_500(self, client, mock_cron_api):
        mock_cron_api.add_job.side_effect = RuntimeError("storage error")
        resp = client.post("/api/cron", json=self._PAYLOAD)
        assert resp.status_code == 500

    def test_append_message_passed_to_payload(self, client, mock_cron_api):
        """append_message 传入时，应透传到 payload"""
        payload = {**self._PAYLOAD, "append_message": "请优先处理核心逻辑"}
        resp = client.post("/api/cron", json=payload)
        assert resp.status_code == 200
        call_args = mock_cron_api.add_job.call_args[0][0]
        assert call_args.payload["append_message"] == "请优先处理核心逻辑"

    def test_no_append_message_omits_from_payload(self, client, mock_cron_api):
        """不传 append_message 时，payload 中不包含该字段"""
        resp = client.post("/api/cron", json=self._PAYLOAD)
        assert resp.status_code == 200
        call_args = mock_cron_api.add_job.call_args[0][0]
        assert "append_message" not in call_args.payload


# ── PUT /api/cron/{task_id} ───────────────────────────────────────────────────

class TestUpdateTask:
    def test_update_name_only(self, client, mock_cron_api):
        resp = client.put("/api/cron/job-1", json={"name": "New Name"})
        assert resp.status_code == 200
        call_args = mock_cron_api.update_job.call_args
        assert call_args[0][0] == "job-1"
        assert call_args[0][1].name == "New Name"

    def test_update_enabled_only(self, client, mock_cron_api):
        resp = client.put("/api/cron/job-1", json={"enabled": False})
        assert resp.status_code == 200
        assert mock_cron_api.update_job.call_args[0][1].enabled is False

    def test_update_schedule_and_timezone(self, client, mock_cron_api):
        resp = client.put("/api/cron/job-1", json={"schedule": "0 10 * * *", "timezone": "UTC"})
        assert resp.status_code == 200
        schedule = mock_cron_api.update_job.call_args[0][1].schedule
        assert schedule["expr"] == "0 10 * * *"
        assert schedule["tz"] == "UTC"

    def test_update_schedule_only(self, client, mock_cron_api):
        resp = client.put("/api/cron/job-1", json={"schedule": "0 12 * * *"})
        assert resp.status_code == 200
        schedule = mock_cron_api.update_job.call_args[0][1].schedule
        assert schedule["expr"] == "0 12 * * *"
        assert "tz" not in schedule

    def test_update_command_fetches_existing_job(self, client, mock_cron_api):
        resp = client.put("/api/cron/job-1", json={"command": "new command"})
        assert resp.status_code == 200
        mock_cron_api.get_job.assert_called_once_with("job-1")
        payload = mock_cron_api.update_job.call_args[0][1].payload
        assert payload["message"] == "new command"

    def test_update_command_job_not_found_returns_404(self, client, mock_cron_api):
        mock_cron_api.get_job.return_value = None
        resp = client.put("/api/cron/job-1", json={"command": "whatever"})
        assert resp.status_code == 404

    def test_update_timeout_secs(self, client, mock_cron_api):
        resp = client.put("/api/cron/job-1", json={"timeout_secs": 999})
        assert resp.status_code == 200
        payload = mock_cron_api.update_job.call_args[0][1].payload
        assert payload["timeout_secs"] == 999

    def test_update_model(self, client, mock_cron_api):
        resp = client.put("/api/cron/job-1", json={"model": "claude-3"})
        assert resp.status_code == 200
        payload = mock_cron_api.update_job.call_args[0][1].payload
        assert payload["model"] == "claude-3"

    def test_update_notify_full(self, client, mock_cron_api):
        resp = client.put("/api/cron/job-1", json={"notify": {"enabled": True, "user_ids": ["u1"]}})
        assert resp.status_code == 200
        notify = mock_cron_api.update_job.call_args[0][1].notify
        assert notify.enabled is True
        assert notify.user_ids == ["u1"]

    def test_update_notify_enabled_only(self, client, mock_cron_api):
        resp = client.put("/api/cron/job-1", json={"notify": {"enabled": False}})
        assert resp.status_code == 200
        notify = mock_cron_api.update_job.call_args[0][1].notify
        assert notify.enabled is False
        assert notify.user_ids is None

    def test_no_fields_returns_400(self, client, mock_cron_api):
        resp = client.put("/api/cron/job-1", json={})
        assert resp.status_code == 400
        assert "No fields" in resp.json()["detail"]

    def test_service_error_returns_500(self, client, mock_cron_api):
        mock_cron_api.update_job.side_effect = RuntimeError("update failed")
        resp = client.put("/api/cron/job-1", json={"name": "x"})
        assert resp.status_code == 500


# ── DELETE /api/cron/{task_id} ────────────────────────────────────────────────

class TestDeleteTask:
    def test_success(self, client, mock_cron_api):
        resp = client.delete("/api/cron/job-1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "deleted" in resp.json()["message"].lower()

    def test_not_found_returns_404(self, client, mock_cron_api):
        mock_cron_api.remove_job.return_value = False
        resp = client.delete("/api/cron/missing")
        assert resp.status_code == 404

    def test_service_error_returns_500(self, client, mock_cron_api):
        mock_cron_api.remove_job.side_effect = RuntimeError("gone")
        resp = client.delete("/api/cron/job-1")
        assert resp.status_code == 500


# ── POST /api/cron/{task_id}/run ─────────────────────────────────────────────

class TestRunTask:
    def test_run_default(self, client, mock_cron_api):
        resp = client.post("/api/cron/job-1/run")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_cron_api.run_job.assert_called_once_with("job-1", False)

    def test_run_with_force(self, client, mock_cron_api):
        resp = client.post("/api/cron/job-1/run?force=true")
        assert resp.status_code == 200
        mock_cron_api.run_job.assert_called_once_with("job-1", True)

    def test_service_error_returns_500(self, client, mock_cron_api):
        mock_cron_api.run_job.side_effect = RuntimeError("cannot run")
        resp = client.post("/api/cron/job-1/run")
        assert resp.status_code == 500


# ── GET /api/cron/{task_id}/runs ──────────────────────────────────────────────

class TestGetTaskRuns:
    def test_success(self, client, mock_cron_api):
        resp = client.get("/api/cron/job-1/runs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "runs" in body["data"]
        assert "input" in body["data"]
        assert body["data"]["input"] == "do something"

    def test_custom_limit(self, client, mock_cron_api):
        resp = client.get("/api/cron/job-1/runs?limit=5")
        assert resp.status_code == 200
        mock_cron_api.get_runs.assert_called_once_with("job-1", 5)

    def test_job_not_in_list_input_is_empty(self, client, mock_cron_api):
        # list_jobs returns jobs whose id doesn't match
        mock_cron_api.list_jobs.return_value = [_make_job("other-id")]
        resp = client.get("/api/cron/job-1/runs")
        assert resp.status_code == 200
        assert resp.json()["data"]["input"] == ""

    def test_service_error_returns_500(self, client, mock_cron_api):
        mock_cron_api.list_jobs.side_effect = RuntimeError("fail")
        resp = client.get("/api/cron/job-1/runs")
        assert resp.status_code == 500
