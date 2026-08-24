"""Domain-to-record serialization for the shared task graph store."""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    NodeAction,
    NodeActionEvent,
    Relation,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)


def _acceptance_to_dict(value: AcceptanceResult | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "verdict": value.verdict.value,
        "acceptances_metric": list(value.acceptances_metric),
        "gaps": list(value.gaps),
    }


def _acceptance_from_dict(value: dict[str, Any] | None) -> AcceptanceResult | None:
    if value is None:
        return None
    return AcceptanceResult(
        verdict=AcceptanceVerdict(value["verdict"]),
        acceptances_metric=list(value.get("acceptances_metric", [])),
        gaps=list(value.get("gaps", [])),
    )


def task_spec_to_dict(spec: TaskSpec) -> dict[str, Any]:
    return {
        "metadata": {
            "task_id": spec.metadata.task_id,
            "title": spec.metadata.title,
            "instruction": spec.metadata.instruction,
        },
        "context": {
            "background": spec.context.background,
            "extend_props": dict(spec.context.extend_props),
        },
        "goal": {
            "objective": spec.goal.objective,
            "acceptances": [
                {"id": item.id, "description": item.description}
                for item in spec.goal.acceptances
            ],
        },
    }


def task_spec_from_dict(value: dict[str, Any]) -> TaskSpec:
    metadata = value.get("metadata", {})
    context = value.get("context", {})
    goal = value.get("goal", {})
    return TaskSpec(
        metadata=Metadata(
            task_id=str(metadata.get("task_id", "")),
            title=str(metadata.get("title", "")),
            instruction=str(metadata.get("instruction", "")),
        ),
        context=Context(
            background=str(context.get("background", "")),
            extend_props=dict(context.get("extend_props", {})),
        ),
        goal=Goal(
            objective=str(goal.get("objective", "")),
            acceptances=[
                AcceptanceCriteria(
                    id=str(item.get("id", "")),
                    description=str(item.get("description", "")),
                )
                for item in goal.get("acceptances", [])
            ],
        ),
    )


def runtime_to_dict(runtime: RuntimeInfo) -> dict[str, Any]:
    return {
        "run_mode": runtime.run_mode,
        "assignee": runtime.assignee,
        "start_time": runtime.start_time,
        "end_time": runtime.end_time,
        "output": dict(runtime.output),
        "acceptance_result": _acceptance_to_dict(runtime.acceptance_result),
        "extend_props": dict(runtime.extend_props),
    }


def runtime_from_dict(value: dict[str, Any] | None) -> RuntimeInfo:
    value = value or {}
    return RuntimeInfo(
        run_mode=value.get("run_mode"),
        assignee=value.get("assignee"),
        start_time=value.get("start_time"),
        end_time=value.get("end_time"),
        output=dict(value.get("output", {})),
        acceptance_result=_acceptance_from_dict(value.get("acceptance_result")),
        extend_props=dict(value.get("extend_props", {})),
    )


def graph_to_dict(graph: TaskExecutionGraph) -> dict[str, Any]:
    return {
        "run_id": graph.run_id,
        "task_id": graph.task_id,
        "loop_round": graph.loop_round,
        "status": graph.status.value,
        "output": dict(graph.output),
        "extend_props": dict(graph.extend_props),
        "tasks": [
            {
                "node_id": node.node_id,
                "task_id": node.task_id,
                "status": node.status.value,
                "task_spec": task_spec_to_dict(node.task_spec),
                "run_info": runtime_to_dict(node.run_info),
            }
            for node in graph.tasks
        ],
        "relations": [
            {
                "src_id": relation.src_id,
                "dst_id": relation.dst_id,
                "type": relation.type.value,
                "extend_props": dict(relation.extend_props),
            }
            for relation in graph.relations
        ],
    }


def graph_from_parts(
    *,
    task_id: str,
    run_id: str | int | None,
    loop_round: int,
    status: Status,
    output: dict[str, Any] | None,
    extend_props: dict[str, Any] | None,
    nodes: list[tuple[str, Status, dict[str, Any], RuntimeInfo]],
    relations: list[Relation],
) -> TaskExecutionGraph:
    try:
        parsed_run_id = int(run_id) if run_id is not None else 0
    except (TypeError, ValueError):
        parsed_run_id = 0
    graph = TaskExecutionGraph(
        run_id=parsed_run_id,
        loop_round=loop_round,
        status=status,
        output=dict(output or {}),
        extend_props=dict(extend_props or {}),
        task_id=task_id,
        relations=relations,
    )
    graph.tasks = [
        TaskNode(
            node_id=node_id,
            task_id=task_id,
            status=node_status,
            task_spec=task_spec_from_dict(task_spec),
            run_info=runtime,
            node_run_graph=graph,
        )
        for node_id, node_status, task_spec, runtime in nodes
    ]
    return graph


def action_to_dict(event: NodeActionEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "ts": event.ts,
        "action": event.action.value,
        "loop_round": event.loop_round,
        "attempt": event.attempt,
        "status_from": event.status_from.value if event.status_from else None,
        "status_to": event.status_to.value if event.status_to else None,
        "payload": dict(event.payload),
    }


def action_from_dict(value: dict[str, Any]) -> NodeActionEvent:
    return NodeActionEvent(
        seq=int(value["seq"]),
        ts=int(value.get("ts", 0)),
        action=NodeAction(value["action"]),
        loop_round=int(value.get("loop_round", 0)),
        attempt=int(value.get("attempt", 0)),
        status_from=Status(value["status_from"]) if value.get("status_from") else None,
        status_to=Status(value["status_to"]) if value.get("status_to") else None,
        payload=dict(value.get("payload", {})),
    )
