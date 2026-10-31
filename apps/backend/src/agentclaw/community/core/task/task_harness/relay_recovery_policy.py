"""Policies that turn Relay recovery prerequisites into harness patches."""

from __future__ import annotations

import logging
from typing import Any

from agentclaw.community.core.task.domain.models import Status, TaskNodePatch
from agentclaw.community.core.task.task_harness.relay_runtime_policy import (
    relay_resume_attempts,
)


logger = logging.getLogger("task.harness")


def relay_session_precondition_failure(
    exc: Exception,
    node: Any,
    current: dict[str, Any],
    *,
    max_attempts: int,
    reason: str,
    detail: dict[str, Any],
    elapsed: float,
    sla: float,
) -> TaskNodePatch | None:
    """Count a permanent business-precondition miss without transport retries.

    A missing same-session identity or an invalid assignee is not a resume-send
    transport failure. Repeated sends cannot repair the node, so reaching the
    recovery budget returns a direct terminal HUNG patch.
    """
    count = int(current.get("resume_precondition_failures", 0) or 0) + 1
    current["resume_precondition_failures"] = count
    error_msg = f"{type(exc).__name__}: {exc}"[:500]
    attempts = relay_resume_attempts(node, current)
    logger.error(
        "[task][harness] relay session precondition failed task=%s node=%s "
        "precondition_failures=%d/%d error=%s",
        node.task_id,
        node.node_id,
        count,
        max_attempts,
        error_msg,
    )
    if count < max_attempts:
        return None
    failure_reason = f"relay_harness_precondition_exhausted: {reason}; {exc}"[:500]
    terminal_reason = "relay_harness_precondition_exhausted"
    return TaskNodePatch(
        task_id=node.task_id,
        node_id=node.node_id,
        status=Status.HUNG,
        failure_reason=failure_reason,
        extend_props_patch={
            "relay_harness_precondition_failures": count,
            "harness_probe": {
                **detail,
                "reason": reason,
                "elapsed_seconds": elapsed,
                "sla_seconds": sla,
                "precondition_failures": count,
                "resume_attempts": attempts,
                "resume_send_failures": current.get("resume_send_failures", 0),
                "terminal_reason": terminal_reason,
                "terminal_error": error_msg,
            },
        },
    )

def relay_session_send_failure(
    exc: Exception,
    node: Any,
    current: dict[str, Any],
    *,
    max_send_failures: int,
    reason: str,
    detail: dict[str, Any],
    elapsed: float,
    sla: float,
) -> TaskNodePatch | None:
    """Bound failure_counted recovery without consuming same-session attempts."""
    count = int(current.get("resume_send_failures", 0) or 0) + 1
    current["resume_send_failures"] = count
    error_msg = f"{type(exc).__name__}: {exc}"[:500]
    attempts = relay_resume_attempts(node, current)
    logger.warning(
        "[task][harness] relay session resume send failed task=%s node=%s "
        "send_failures=%d/%d delivered_attempts=%d error=%s",
        node.task_id,
        node.node_id,
        count,
        max_send_failures,
        attempts,
        error_msg,
    )
    if count < max_send_failures:
        return None
    failure_reason = f"relay_harness_transport_exhausted: {reason}; {exc}"[:500]
    terminal_reason = "relay_harness_transport_exhausted"
    return TaskNodePatch(
        task_id=node.task_id,
        node_id=node.node_id,
        status=Status.HUNG,
        failure_reason=failure_reason,
        extend_props_patch={
            "relay_harness_resume_send_failures": count,
            "harness_probe": {
                **detail,
                "reason": reason,
                "elapsed_seconds": elapsed,
                "sla_seconds": sla,
                "resume_send_failures": count,
                "resume_attempts": attempts,
                "terminal_reason": terminal_reason,
                "terminal_error": error_msg,
            },
        },
    )

def relay_probe_unavailable_exhaustion_patch(
    node: Any,
    current: dict[str, Any],
    *,
    max_probe_cycles: int,
    detail: dict[str, Any],
    elapsed: float,
    sla: float,
) -> TaskNodePatch:
    """Build the independently bounded terminal patch for a dead probe path."""
    count = int(current.get("probe_unavailable", 0) or 0)
    terminal_reason = "relay_harness_probe_unavailable_exhausted"
    logger.error(
        "[task][harness] runtime probe unavailable exhausted task=%s node=%s "
        "probe_unavailable=%d/%d",
        node.task_id,
        node.node_id,
        count,
        max_probe_cycles,
    )
    return TaskNodePatch(
        task_id=node.task_id,
        node_id=node.node_id,
        status=Status.HUNG,
        failure_reason=terminal_reason,
        extend_props_patch={
            "relay_harness_probe_unavailable_count": count,
            "harness_probe": {
                **detail,
                "reason": "runtime_probe_unavailable",
                "elapsed_seconds": elapsed,
                "sla_seconds": sla,
                "probe_unavailable": count,
                "resume_attempts": relay_resume_attempts(node, current),
                "terminal_reason": terminal_reason,
            },
        },
    )


def relay_resume_exhaustion_patch(
    node: Any,
    current: dict[str, Any],
    *,
    max_attempts: int,
    reason: str,
    detail: dict[str, Any],
    elapsed: float,
    sla: float,
) -> TaskNodePatch:
    """Build the terminal patch for exhausted delivered same-session resumes."""
    attempts = relay_resume_attempts(node, current)
    terminal_reason = f"relay_harness_resume_exhausted: {reason}"
    logger.error(
        "[task][harness] relay session resume exhausted task=%s node=%s "
        "reason=%s attempts=%d/%d",
        node.task_id,
        node.node_id,
        reason,
        attempts,
        max_attempts,
    )
    return TaskNodePatch(
        task_id=node.task_id,
        node_id=node.node_id,
        status=Status.HUNG,
        failure_reason=terminal_reason,
        extend_props_patch={
            "relay_harness_resume_attempts": attempts,
            "relay_harness_precondition_failures": current.get(
                "resume_precondition_failures", 0
            ),
            "harness_probe": {
                **detail,
                "reason": reason,
                "elapsed_seconds": elapsed,
                "sla_seconds": sla,
                "unknown_count": current.get("unknown", 0),
                "no_progress_count": current.get("no_progress", 0),
                "success_unreported_count": current.get("success_unreported", 0),
                "resume_attempts": attempts,
                "terminal_reason": terminal_reason,
            },
        },
    )
