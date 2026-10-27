"""Pure policy helpers for Relay runtime recovery.

These helpers turn the authoritative Bot/Relay state graph into three recovery
rules:

1.  how many ``active`` observations without progress are allowed before a
    same-session resume,
2.  how long a silently successful runtime may remain unreported before a
    recovery is scheduled, and
3.  which task-loop step a same-session resume should anchor to.

The module deliberately contains pure helpers only.  Transport, probing, and the
state-machine RecoveryLoop remain in their own modules.
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.models import TaskNode

_RELAY_ACTIVE_NO_PROGRESS_CYCLES = (4, 6, 8)
_RELAY_TERMINAL_SUCCESS_UNREPORTED_SECONDS = (360.0, 600.0, 900.0)


def active_no_progress_cycle(attempts: int) -> int:
    """Return the current ``active`` no-progress threshold in cycles."""
    attempts = max(int(attempts), 0)
    index = min(
        attempts,
        len(_RELAY_ACTIVE_NO_PROGRESS_CYCLES) - 1,
    )
    return _RELAY_ACTIVE_NO_PROGRESS_CYCLES[index]


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
    try:
        context = graph.get_node_context(task_id, node.node_id)
    except Exception:  # noqa: BLE001 - graph history is optional at this boundary
        return None
    if context.accepted_plan_report is not None:
        return "S6"
    if context.accepted_execution_patch is not None:
        return "S5"
    return "S1-S4a"


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
