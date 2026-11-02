"""Best-effort admission contains errors without exposing provider payloads."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.ownership_sync import notifications
from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncReason


@pytest.mark.parametrize("result", list(SubmissionResult))
def test_helper_submits_exact_scope_and_ignores_admission_result(result):
    sync = MagicMock()
    sync.request_ownership_sync.return_value = result
    assert (
        notifications.notify_bot_ownership_sync(
            sync,
            bot_pk=23,
            env="dev",
            avernet_tenant="teamclaw",
            reason=SyncReason.OWNER_HANDOFF,
        )
        is None
    )
    sync.request_ownership_sync.assert_called_once_with(
        bot_pk=23,
        env="dev",
        avernet_tenant="teamclaw",
        reason=SyncReason.OWNER_HANDOFF,
    )


def test_helper_sanitizes_secret_exception_payload(monkeypatch):
    sync, logger = MagicMock(), MagicMock()
    secret = "credential=test-secret; authorization=https://invalid.example/secret"
    sync.request_ownership_sync.side_effect = RuntimeError(secret)
    monkeypatch.setattr(notifications, "logger", logger, raising=False)
    assert (
        notifications.notify_bot_ownership_sync(
            sync,
            bot_pk=23,
            env="dev",
            avernet_tenant="teamclaw",
            reason=SyncReason.OWNER_HANDOFF,
        )
        is None
    )
    logger.error.assert_called_once_with(
        "bot ownership sync notification failed",
        extra={
            "bot_pk": 23,
            "env": "dev",
            "avernet_tenant": "teamclaw",
            "reason": "owner_handoff",
            "exception_type": "RuntimeError",
        },
    )
    assert secret not in str(logger.mock_calls)
    assert "test-secret" not in str(logger.mock_calls)
    assert "https://invalid.example/secret" not in str(logger.mock_calls)
    sync.request_ownership_sync.assert_called_once()
