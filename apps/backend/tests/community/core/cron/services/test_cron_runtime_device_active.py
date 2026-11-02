"""Expected PENDING runtime devices should remain retryable but non-alarming."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.cron.services import cron_runtime_operations


def _service() -> CronRelayService:
    return CronRelayService(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )


@pytest.mark.asyncio
async def test_pending_runtime_device_keeps_conflict_and_logs_warning(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(cron_runtime_operations, "logger", log)
    svc = _service()

    with pytest.raises(CronRelayError, match="status=PENDING"):
        svc._ensure_runtime_device_active(
            bot_id="bot",
            runtime_stage="draft",
            device=SimpleNamespace(status="PENDING"),
        )

    log.warning.assert_called_once()
    log.error.assert_not_called()


def test_non_pending_runtime_device_still_logs_error(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(cron_runtime_operations, "logger", log)
    svc = _service()

    with pytest.raises(CronRelayError, match="status=RELEASED"):
        svc._ensure_runtime_device_active(
            bot_id="bot",
            runtime_stage="draft",
            device=SimpleNamespace(status="RELEASED"),
        )

    log.warning.assert_not_called()
    log.error.assert_called_once()
