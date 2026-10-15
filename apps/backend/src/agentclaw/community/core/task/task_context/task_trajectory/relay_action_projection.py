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


def project_trace_steps(
    source: dict[str, Any] | None,
) -> list[dict[str, Any]] | None:
    """``trace.steps`` → 归一的 ``trace_steps``(step/status/detail 三槽)。

    数据源为 Bot 随上报携带的执行检查点(S1~S5;S6 派发侧推导,不在此)。``source``
    既可是表行 ext_info(W2 写时发射随 payload 落入 ext_info.trace)也可是 action
    payload 本体——两种形态都认。防御式:缺素材/形态不合 → ``None``(缺字段=无信号),
    绝不抛。
    """
    if not isinstance(source, dict):
        return None
    trace = source.get("trace")
    trace = trace if isinstance(trace, dict) else source
    steps = trace.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    projected: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict) or not step.get("step"):
            continue
        detail = step.get("detail")
        projected.append({
            "step": str(step["step"]),
            "status": str(step.get("status") or ""),
            "detail": dict(detail) if isinstance(detail, dict) else {},
        })
    return projected or None


def relay_action_event_fields(
    event: NodeActionEvent,
    *,
    relay_event_id: str | None = None,
) -> dict[str, Any]:
    """``NodeActionEvent`` → ``emit_trajectory_event`` kwargs(写时发射的转换层)。

    与读时投影 ``_to_trajectory_event`` 同源——同一动作经 ``_result``/``_error_type``
    判定,两条路径产生**同形**事件(读时 ``merge_relay_action_log`` 依 6 元组签名去重,
    同秒即归一,写时已落表的行不再被投影重复拼装)。辅助键处理:

    * ``__node_id``(record_action 注入的定位键)→ 以 ``fields["node_id"]`` 返回,
      不进 ext_info;
    * ``__holder_id``(record_action 注入的执行人)→ 归位 ``ext_info["holder_id"]``
      (表行执行人的 assembler 白名单投影来源,对齐投影路径的 ``holder_id``);
    * ``relay_event_id`` 调用方可附带(上报 event_id,表行→relay 回执可追溯)。

    ``now_ms`` 固定取 ``event.ts``——发射行 ``gmt_create`` 与动作时间一致,与投影
    签名天然对齐。
    """
    payload = event.payload
    ext_info = {
        key: value
        for key, value in payload.items()
        if key not in {"__node_id", "__holder_id"}
    }
    holder = payload.get("__holder_id")
    if holder:
        ext_info["holder_id"] = holder
    if relay_event_id:
        ext_info["relay_event_id"] = relay_event_id
    failure = payload.get("failure_reason") or payload.get("error")
    return {
        "node_id": str(payload.get("__node_id") or ""),
        "action_input": (
            str(payload["query"]) if payload.get("query") is not None else None
        ),
        "action_result": _result(event),
        "attempt": event.attempt,
        "status_from": event.status_from,
        "status_to": event.status_to,
        "error_type": _error_type(event),
        "error_msg": str(failure) if failure else None,
        "boost_reason": (
            str(payload["progress_reason"])
            if payload.get("progress_reason")
            else None
        ),
        "ext_info": ext_info,
        "now_ms": event.ts,
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
        trace_steps=project_trace_steps(payload),
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

    # 去重签名在毫秒上取**秒粒度**:写时发射(TaskServiceRelayMixin.report)已把上报
    # 动作落成表行,但 MySQL ``gmt_create`` 是秒精度 timestamp——ms 落库即被截断,
    # 表行重读回来的 gmt_create 与动作原始 ts 差一个 ms 尾差。签名降级到秒后,
    # "写时已落表的行"与"投影候选项"同秒即归一,时间线不再出现双份;同秒同键的
    # 碰撞语义与既有设计一致(同秒并列为 spec 接受的边界)。
    existing = {
        (
            event.node_id,
            event.action_type.value,
            event.attempt,
            event.gmt_create // 1000,
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
                projected.gmt_create // 1000,
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
