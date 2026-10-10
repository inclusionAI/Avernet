"""Execution diagnostics retain failure semantics without exposing payloads."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.work_orders import execution


@pytest.fixture
def diagnostics(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(execution, "logger", logger)
    monkeypatch.setattr(execution, "get_current_env", lambda: "test")
    ticks = iter([10.0, 10.125])
    monkeypatch.setattr(execution, "monotonic", lambda: next(ticks))
    return logger


def test_phase_logs_identity_timing_and_success(diagnostics):
    with execution.execution_phase("auto_finalize", work_order_id=31):
        diagnostics.error.assert_not_called()

    assert diagnostics.info.call_count == 2
    started, completed = diagnostics.info.call_args_list
    assert started.kwargs["extra"] == {
        "phase": "auto_finalize",
        "work_order_id": 31,
        "env": "test",
        "outcome": "started",
    }
    assert completed.kwargs["extra"] == {
        **started.kwargs["extra"],
        "outcome": "completed",
        "duration_ms": 125.0,
    }
    diagnostics.error.assert_not_called()


def test_phase_propagates_original_error_without_logging_sensitive_text(diagnostics):
    failure = RuntimeError("private callback response and credential")
    with pytest.raises(RuntimeError) as caught:
        with execution.execution_phase("event_create", work_order_id=None):
            raise failure

    assert caught.value is failure
    diagnostics.info.assert_called_once()
    diagnostics.error.assert_called_once()
    assert diagnostics.error.call_args.kwargs["extra"] == {
        "phase": "event_create",
        "work_order_id": None,
        "env": "test",
        "outcome": "failed",
        "exception_type": "RuntimeError",
        "duration_ms": 125.0,
    }
    assert str(failure) not in str(diagnostics.mock_calls)
    assert "exc_info" not in diagnostics.error.call_args.kwargs


@pytest.mark.parametrize("sync_fails", [False, True])
def test_bot_post_commit_sync_is_once_and_best_effort(diagnostics, sync_fails):
    collaborators = MagicMock()
    if sync_fails:
        collaborators.on_collaboration_changed.side_effect = RuntimeError(
            "private downstream response"
        )

    execution.notify_bot_collaboration_changed(
        collaborators, work_order_id=32, bot_id="bot-1", owner_id="owner-1"
    )

    collaborators.on_collaboration_changed.assert_called_once_with(
        "bot-1", "owner-1", "test"
    )
    started = diagnostics.info.call_args_list[0].kwargs["extra"]
    assert started == {
        "phase": "bot_post_commit_sync",
        "work_order_id": 32,
        "env": "test",
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "local_committed": True,
        "outcome": "started",
    }
    if sync_fails:
        diagnostics.error.assert_called_once()
        assert diagnostics.error.call_args.kwargs["extra"]["outcome"] == "failed"
        assert diagnostics.info.call_count == 1
        assert "private downstream response" not in str(diagnostics.mock_calls)
    else:
        diagnostics.error.assert_not_called()
        assert diagnostics.info.call_args.kwargs["extra"]["outcome"] == "completed"
