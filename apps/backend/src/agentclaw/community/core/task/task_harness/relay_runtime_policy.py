"""Pure policy helpers for Relay runtime recovery.

These helpers turn the authoritative Bot/Relay state graph into two recovery
rules:

1.  how long a silently successful runtime may remain unreported after the
    service first observes terminal success before a recovery is scheduled, and
2.  which task-loop step a same-session resume should anchor to.

The module deliberately contains pure helpers only.  Transport, probing, and the
state-machine RecoveryLoop remain in their own modules.
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.models import TaskNode

_RELAY_TERMINAL_SUCCESS_UNREPORTED_SECONDS = (360.0, 600.0, 900.0)

_RECOVERY_ANCHOR_TO_RESUME_STEP = {
    "S1_S3": "S1-S3",
    "S4A_CONTINUE": "S4a",
    "S4A_WAIT": "S4a-wait",
    "S4A_RECONCILE": "S4a-reconcile",
    "S4B_REPORT": "S4b",
    "S5A_REFRESH_GAPS": "S5a",
    "S5B_PLAN_NEXT_NODE": "S5b",
    "S5C_SEARCH_CANDIDATES": "S5c",
    "S5D_SELECT_EXECUTOR": "S5d",
    "S5E_REPORT_PLAN": "S5e",
    "S6_DISPATCH": "S6",
    "S6_FINALIZE": "S6",
}


def _projection_resume_step(node: TaskNode) -> str | None:
    props = getattr(getattr(node, "run_info", None), "extend_props", {}) or {}
    projection = props.get("relay_recovery_projection")
    if not isinstance(projection, dict):
        return None
    anchor = projection.get("recovery_anchor")
    return _RECOVERY_ANCHOR_TO_RESUME_STEP.get(anchor)


def terminal_success_unreported_seconds(attempts: int) -> float:
    """Return the current silent-success timeout in seconds."""
    attempts = max(int(attempts), 0)
    index = min(
        attempts,
        len(_RELAY_TERMINAL_SUCCESS_UNREPORTED_SECONDS) - 1,
    )
    return _RELAY_TERMINAL_SUCCESS_UNREPORTED_SECONDS[index]


def resolve_relay_resume_step(
    task_id: str,
    node: TaskNode,
    graph: Any,
) -> str | None:
    """Anchor a same-session resume to the authoritative current Relay stage.

    The returned caller-local label is not persisted and is not a task-loop
    protocol step.  A graph read failure falls back to ``None`` so recovery can
    still send the unchanged generic prompt.
    """
    # Backend accepted state is authoritative and must win over a stale local
    # projection. The projection is consulted only when the accepted graph state
    # does not identify a later phase.
    try:
        context = graph.get_node_context(task_id, node.node_id)
    except Exception:  # noqa: BLE001 - graph history is optional at this boundary
        context = None
    if context is not None:
        if context.accepted_plan_report is not None:
            return "S6"
        if context.accepted_execution_patch is not None:
            return "S5"
    return _projection_resume_step(node) or "S1-S4a"


def relay_resume_attempts(
    node: TaskNode,
    current: dict[str, Any],
) -> int:
    """Return the already delivered same-session resume budget for this node."""
    persisted = getattr(node.run_info, "extend_props", {}).get(
        "relay_harness_resume_attempts"
    )
    if persisted is not None:
        try:
            return int(persisted)
        except (TypeError, ValueError):
            pass
    try:
        return int(current.get("resume_attempts") or 0)
    except (TypeError, ValueError):
        return 0
