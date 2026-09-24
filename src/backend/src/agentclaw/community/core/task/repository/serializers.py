"""Domain-to-record serialization for the shared task graph store."""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
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
        "done_items": list(value.done_items),
        "gap_items": list(value.gap_items),
    }


def _acceptance_from_dict(value: dict[str, Any] | None) -> AcceptanceResult | None:
    if value is None:
        return None
    # Recover the canonical fields first, then historical field names. The
    # compatibility order is: done_items, acceptances_metric and gap_items,
    # gaps. The intermediate ``done`` field was never deployed, so it is not
    # accepted. Persisted writes always emit only ``done_items`` and
    # ``gap_items``. Legacy prompts sometimes placed passed=false metrics in
    # acceptances_metric; those are not completed items, so drop them.
    done_items = (
        list(value["done_items"])
        if "done_items" in value
        else [
            item
            for item in value.get("acceptances_metric", [])
            if not (isinstance(item, dict) and item.get("passed") is False)
        ]
    )
    gap_items = (
        list(value["gap_items"])
        if "gap_items" in value
        else list(value.get("gaps", []))
    )
    return AcceptanceResult(
        verdict=AcceptanceVerdict(value["verdict"]),
        done_items=done_items,
        gap_items=gap_items,
    )


def task_spec_to_dict(spec: TaskSpec) -> dict[str, Any]:
    return spec.to_dict()


def task_spec_from_dict(value: dict[str, Any]) -> TaskSpec:
    # Legacy persisted specs carried title/instruction/task_id under metadata.
    # Normalize once at the persistence boundary; the domain TaskSpec stays clean.
    metadata = value.get("metadata", {}) if isinstance(value, dict) else {}
    context = value.get("context", {}) if isinstance(value, dict) else {}
    goal = value.get("goal", {}) if isinstance(value, dict) else {}
    extend_props = dict(context.get("extend_props", {}))
    legacy_instruction = str(metadata.get("instruction", "")).strip()
    objective = str(goal.get("objective", "")).strip() or legacy_instruction
    return TaskSpec(
        context=Context(
            title=str(context.get("title", "") or metadata.get("title", "")),
            background=str(context.get("background", "")),
            extend_props=extend_props,
        ),
        goal=Goal(
            objective=objective,
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
    # ``output_artifact_ids`` / ``primary_output_artifact_id`` 为读时富化领域口径
    # (spec 2026-09-23-task-artifact-manifest §4):刻意不进持久化 dict,序列化/
    # 反序列化对此零感应(runtime_from_dict 同样不恢复,缺字段=无信号)。
    return {
        "run_mode": runtime.run_mode,
        "assignee": runtime.assignee,
        "start_time": runtime.start_time,
        "end_time": runtime.end_time,
        "actual_goal": runtime.actual_goal.to_dict() if runtime.actual_goal else None,
        "output": dict(runtime.output),
        "acceptance_result": _acceptance_to_dict(runtime.acceptance_result),
        "progress_reason": runtime.progress_reason,
        "failure_reason": runtime.failure_reason,
        "extend_props": dict(runtime.extend_props),
    }


def runtime_from_dict(value: dict[str, Any] | None) -> RuntimeInfo:
    value = value or {}
    return RuntimeInfo(
        run_mode=value.get("run_mode"),
        assignee=value.get("assignee"),
        start_time=value.get("start_time"),
        end_time=value.get("end_time"),
        actual_goal=(
            Goal(
                objective=str(value["actual_goal"].get("objective", "")),
                acceptances=[
                    AcceptanceCriteria(
                        id=str(item.get("id", "")),
                        description=str(item.get("description", "")),
                    )
                    for item in value["actual_goal"].get("acceptances", [])
                ],
            )
            if isinstance(value.get("actual_goal"), dict)
            else None
        ),
        output=dict(value.get("output", {})),
        acceptance_result=_acceptance_from_dict(value.get("acceptance_result")),
        progress_reason=value.get("progress_reason"),
        failure_reason=value.get("failure_reason"),
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
