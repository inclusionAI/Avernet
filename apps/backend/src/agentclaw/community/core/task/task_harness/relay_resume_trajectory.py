"""Trajectory emission for Relay same-session harness recovery."""

from __future__ import annotations

import json
import logging
from typing import Any

from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_harness.relay_session_recovery import (
    RELAY_HARNESS_RESUME_PROMPT,
)

logger = logging.getLogger("task.harness")


def emit_relay_resume_trajectory(
    task_context_service: Any | None,
    task_id: str,
    node,
    *,
    attempt: int,
    reason: str,
    channel: str | None,
    error_msg: str | None,
    terminal: bool,
) -> None:
    """Emit the resume observability event without affecting task recovery."""
    if task_context_service is None:
        return
    status_value = "completed" if error_msg is None and not terminal else "failed"
    action_payload = {
        "status": status_value,
        "outcome": (
            "same_session_resume_sent"
            if status_value == "completed"
            else "same_session_resume_failed_or_exhausted"
        ),
        "reason": reason,
        "attempts": attempt,
        "channel": channel,
        "terminal": terminal,
    }
    try:
        action_result = json.dumps(action_payload, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - observation must not replace recovery
        action_result = str(action_payload)
    try:
        task_context_service.emit_trajectory_event(
            task_id,
            node.node_id,
            "harness-engine-relay-resume",
            action_result=action_result,
            action_input=RELAY_HARNESS_RESUME_PROMPT,
            status_from=Status.RUNNING,
            status_to=Status.HUNG if terminal else Status.RUNNING,
            attempt=attempt,
            boost_reason=(
                "Relay harness 仅在既有执行会话中发送自然语言续跑消息；"
                "不重新 prepare、不重新 dispatch、不轮换 relay_token。"
            ),
            error_msg=error_msg
            or ("relay_harness_resume_exhausted" if terminal else None),
        )
    except Exception as exc:  # noqa: BLE001 - trajectory is observability-only
        logger.warning(
            "[task][harness] relay resume trajectory emission failed "
            "task=%s node=%s error=%s",
            task_id,
            node.node_id,
            str(exc)[:500],
        )
