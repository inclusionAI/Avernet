"""Read-only projection from Relay graph action logs to trajectory events."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.models import (
    NodeAction,
    NodeActionEvent,
    TaskExecutionGraph,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryEvent,
)


def _result(event: NodeActionEvent) -> str:
    payload = event.payload
    explicit = str(payload.get("action_result") or "").strip()
    if explicit:
        return explicit
    if event.action is NodeAction.EXECUTE:
        return "success" if payload.get("success") else "failed"
    if event.action is NodeAction.VERIFY:
        verdict = str(payload.get("verdict") or "").upper()
        return "accept_pass" if verdict in {"DONE", "PASS"} else "accept_fail"
    if event.action is NodeAction.PLAN:
        if payload.get("completed"):
            return "completed"
        return "failed" if payload.get("failure_reason") else "success"
    if event.action is NodeAction.DISPATCH:
        return str(payload.get("phase") or "dispatched")
    if event.action is NodeAction.RESET:
        return str(payload.get("reason") or "reset")
    return "transitioned"


def _error_type(event: NodeActionEvent) -> ReasonCatalog | None:
    payload = event.payload
    if payload.get("failure_reason") or payload.get("error"):
        return ReasonCatalog.RELAY
    if event.action is NodeAction.VERIFY and _result(event) == "accept_fail":
        return ReasonCatalog.ACCEPTANCE_FAILED
    return None


def _search_probe(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("phase") != "search":
        return None
    return {
        key: payload[key]
        for key in (
            "query",
            "candidate_count",
            "candidates",
            "raw_item_count",
            "failed_keywords",
        )
        if key in payload
    }


def _to_trajectory_event(
    task_id: str,
    holder_id: str | None,
    event: NodeActionEvent,
) -> TrajectoryEvent:
    payload = event.payload
    node_id = str(payload.get("__node_id") or "")
    failure = payload.get("failure_reason") or payload.get("error")
    return TrajectoryEvent(
        task_id=task_id,
        node_id=node_id,
        action_type=TrajectoryActionType(event.action.value),
        action_result=_result(event),
        attempt=event.attempt,
        gmt_create=event.ts,
        gmt_modified=event.ts,
        action_input=(
            str(payload.get("query"))
            if payload.get("query") is not None
            else None
        ),
        status_from=event.status_from,
        status_to=event.status_to,
        error_type=_error_type(event),
        error_msg=str(failure) if failure else None,
        boost_reason=(
            str(payload.get("progress_reason"))
            if payload.get("progress_reason")
            else None
        ),
        holder_id=holder_id,
        search_probe=_search_probe(payload),
    )


def merge_relay_action_log(
    trajectory: TaskTrajectory,
    graph: TaskExecutionGraph,
) -> int:
    """Merge graph-transactional Relay actions into the read-side timeline.

    Centralized graphs intentionally remain on their existing trajectory
    emission path. Relay actions are deduplicated by their persisted node-local
    identity and the final timeline remains chronological.
    """
    if not graph.is_relay:
        return 0

    existing = {
        (
            event.node_id,
            event.action_type.value,
            event.attempt,
            event.gmt_create,
            event.status_from,
            event.status_to,
        )
        for event in trajectory.timeline
    }
    additions: list[TrajectoryEvent] = []
    for node in graph.tasks:
        for action in node.run_info.action_log:
            projected = _to_trajectory_event(
                graph.task_id,
                node.run_info.assignee or None,
                action,
            )
            key = (
                projected.node_id,
                projected.action_type.value,
                projected.attempt,
                projected.gmt_create,
                projected.status_from,
                projected.status_to,
            )
            if key in existing:
                continue
            existing.add(key)
            additions.append(projected)
    if not additions:
        return 0
    trajectory.timeline.extend(additions)
    trajectory.timeline.sort(
        key=lambda event: (
            event.gmt_create,
            event.node_id,
            event.action_type.value,
            event.attempt,
        )
    )
    trajectory.gmt_create = min(event.gmt_create for event in trajectory.timeline)
    trajectory.gmt_modified = max(event.gmt_modified for event in trajectory.timeline)
    return len(additions)
