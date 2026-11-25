"""Recovery outcomes and queue admission contracts."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    RuntimeProjectionIssue, RuntimeProjectionResult, RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.services.desktop_skill_recovery import (
    DesktopSkillRecoveryService, DesktopSkillRecoveryTaskHandler,
)
from agentclaw.community.core.task_queue.types import Complete, Fail, Retry


def _bot(*, status: str) -> dict:
    return {"bot_type": "desktop", "status": status}


@pytest.mark.parametrize("status", ["ACTIVE", "PENDING"])
def test_ensure_uses_minimal_payload_and_configured_deadline(
    status: str,
) -> None:
    bots = MagicMock()
    bots.get_by_id_and_owner.return_value = _bot(status=status)
    tasks = MagicMock()
    service = DesktopSkillRecoveryService(
        bots=bots,
        tasks=tasks,
        task_deadline_seconds=600,
    )

    service.ensure(owner_id="owner-a", bot_id="bot-a")

    _, payload = tasks.enqueue.call_args.args
    assert payload == {"owner_id": "owner-a", "bot_id": "bot-a"}
    assert tasks.enqueue.call_args.kwargs["deadline_seconds"] == 600
    assert "delay_seconds" not in tasks.enqueue.call_args.kwargs


def test_recovery_service_rejects_non_positive_deadline() -> None:
    with pytest.raises(ValueError, match="task_deadline_seconds must be positive"):
        DesktopSkillRecoveryService(
            bots=MagicMock(),
            tasks=MagicMock(),
            task_deadline_seconds=0,
        )


def test_explicit_device_offline_result_retries_while_bot_status_may_be_stale() -> None:
    outcome = DesktopSkillRecoveryTaskHandler._outcome(
        RuntimeProjectionResult.pending(
            code="DESKTOP_DEVICE_OFFLINE",
            reason="offline",
        ),
        prepare_pending=False,
        prepare_retryable_error=False,
    )

    assert isinstance(outcome, Retry)


def test_unknown_retryable_issue_does_not_authorize_task_retry() -> None:
    outcome = DesktopSkillRecoveryTaskHandler._outcome(
        RuntimeProjectionResult.pending(
            code="UNKNOWN_RUNTIME_WAIT",
            reason="unknown",
        ),
        prepare_pending=False,
        prepare_retryable_error=False,
    )

    assert isinstance(outcome, Complete)


def test_engine_mapping_unsupported_fails_without_retry() -> None:
    outcome = DesktopSkillRecoveryTaskHandler._outcome(
        RuntimeProjectionResult(
            status=RuntimeProjectionStatus.DEGRADED,
            components={"skills": RuntimeProjectionStatus.DEGRADED},
            issues=(
                RuntimeProjectionIssue(
                    resource_type="RUNTIME",
                    code="ENGINE_SKILL_MAPPING_UNSUPPORTED",
                    reason="unsupported",
                    status=RuntimeProjectionStatus.DEGRADED,
                    retryable=False,
                ),
            ),
        ),
        prepare_pending=False,
        prepare_retryable_error=False,
    )

    assert isinstance(outcome, Fail)
    assert outcome.error == "ENGINE_SKILL_MAPPING_UNSUPPORTED"
