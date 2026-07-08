"""
Unit tests for engine.community.core.cron.services.notify

Tests cover:
- _get_default_user_ids: env-var path, credentials-file path, fallback to [].
- resolve_user_ids: job.notify.user_ids precedence, fallback to defaults.
- send_cron_notification: delegates to injected NotificationService.
"""
import os
import pytest
from unittest.mock import AsyncMock

from engine.community.plugin_api.cron.models import CronJob, CronNotifyConfig, CronRunRecord
from engine.community.plugin_api.notification.recipients import (
    get_default_user_ids,
    resolve_user_ids,
)
from engine.community.core.cron.services.notify import send_cron_notification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(notify: CronNotifyConfig | None = None) -> CronJob:
    return CronJob(
        id="job-001",
        name="test-job",
        schedule={"kind": "cron", "expr": "0 8 * * *"},
        payload={"kind": "agentTurn", "message": "hi"},
        created_at_ms=1_000_000,
        updated_at_ms=1_000_001,
        notify=notify,
    )


def _make_run(status: str = "ok", output: str | None = None, error: str | None = None) -> CronRunRecord:
    return CronRunRecord(
        job_id="job-001",
        started_at_ms=1_700_000_000_000,
        finished_at_ms=1_700_000_005_000,
        status=status,
        duration_ms=5000,
        output=output,
        error=error,
    )


# ---------------------------------------------------------------------------
# _get_default_user_ids
# ---------------------------------------------------------------------------

class TestGetDefaultUserIds:
    def test_returns_staff_id_from_env(self, monkeypatch):
        monkeypatch.setenv("STAFF_ID", "staff-abc")
        result = get_default_user_ids()
        assert result == ["staff-abc"]

    def test_reads_from_credentials_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STAFF_ID", raising=False)
        creds = tmp_path / "credentials"
        creds.write_text("CLIENT_ID=staff_12345_extra\n", encoding="utf-8")
        monkeypatch.setenv("CREDENTIALS_PATH", str(creds))

        result = get_default_user_ids()
        assert result == ["12345"]

    def test_credentials_line_not_matching_pattern_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STAFF_ID", raising=False)
        creds = tmp_path / "credentials"
        creds.write_text("CLIENT_ID=notstaff_id\n", encoding="utf-8")
        monkeypatch.setenv("CREDENTIALS_PATH", str(creds))

        result = get_default_user_ids()
        assert result == []

    def test_returns_empty_when_no_env_no_file(self, monkeypatch):
        monkeypatch.delenv("STAFF_ID", raising=False)
        monkeypatch.setenv("CREDENTIALS_PATH", "/nonexistent/path/creds")

        result = get_default_user_ids()
        assert result == []

    def test_credentials_file_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STAFF_ID", raising=False)
        monkeypatch.setenv("CREDENTIALS_PATH", str(tmp_path / "no_such_file"))

        result = get_default_user_ids()
        assert result == []


# ---------------------------------------------------------------------------
# resolve_user_ids
# ---------------------------------------------------------------------------

class TestResolveUserIds:
    def test_uses_job_notify_user_ids_when_present(self, monkeypatch):
        monkeypatch.delenv("STAFF_ID", raising=False)
        job = _make_job(notify=CronNotifyConfig(enabled=True, user_ids=["u1", "u2"]))
        assert resolve_user_ids(job) == ["u1", "u2"]

    def test_falls_back_to_default_when_no_user_ids(self, monkeypatch):
        monkeypatch.setenv("STAFF_ID", "default-user")
        job = _make_job(notify=CronNotifyConfig(enabled=True, user_ids=None))
        assert resolve_user_ids(job) == ["default-user"]

    def test_falls_back_to_default_when_notify_is_none(self, monkeypatch):
        monkeypatch.setenv("STAFF_ID", "default-user")
        job = _make_job(notify=None)
        assert resolve_user_ids(job) == ["default-user"]

    def test_returns_empty_when_no_user_ids_and_no_defaults(self, monkeypatch):
        monkeypatch.delenv("STAFF_ID", raising=False)
        monkeypatch.setenv("CREDENTIALS_PATH", "/nonexistent")
        job = _make_job(notify=CronNotifyConfig(enabled=True, user_ids=None))
        assert resolve_user_ids(job) == []

    def test_uses_job_notify_user_ids_even_if_env_set(self, monkeypatch):
        monkeypatch.setenv("STAFF_ID", "env-user")
        job = _make_job(notify=CronNotifyConfig(enabled=True, user_ids=["explicit-user"]))
        assert resolve_user_ids(job) == ["explicit-user"]


# ---------------------------------------------------------------------------
# send_cron_notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSendCronNotification:
    async def test_delegates_to_injected_service(self):
        job = _make_job()
        run = _make_run()
        service = AsyncMock()
        service.send_cron_notification = AsyncMock(return_value=True)

        result = await send_cron_notification(service, job, run, timeout_secs=5.0)

        assert result is True
        service.send_cron_notification.assert_awaited_once_with(
            job, run, timeout_secs=5.0
        )

    async def test_returns_service_result(self):
        job = _make_job()
        run = _make_run()
        service = AsyncMock()
        service.send_cron_notification = AsyncMock(return_value=False)

        result = await send_cron_notification(service, job, run, timeout_secs=5.0)

        assert result is False
