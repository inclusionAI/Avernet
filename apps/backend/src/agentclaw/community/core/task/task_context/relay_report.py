"""Atomic graph transition for the new two-event Relay reporting contract."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import asdict
from enum import Enum
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    NodeAction,
    NodeActionEvent,
    Relation,
    RelationType,
    RuntimeInfo,
    Status,
    TaskNode,
    TaskNodePatch,
    TaskReportCommand,
    TaskReportEventType,
    TaskReportResult,
    TaskReportStatus,
)
from agentclaw.community.core.task.task_context.task_graph_support import (
    _relay_graph_output,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_publish import (
    TaskArtifactPublishService,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

# 产物发布 capture(§5.1):纯构造 + contextvar 暂存,随 graph_service 的事务消费。
_ARTIFACT_PUBLISH = TaskArtifactPublishService()


def _planned_relay_group_formation(value: Any) -> dict[str, Any]:
    """Validate the reviewed HIT_MULTI_BOTS plan without accepting a group ID."""
    if not isinstance(value, dict):
        raise TaskStateError("planned coop_group requires pending_group_formation")
    try:
        formation = GroupFormation.from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskStateError("invalid pending_group_formation") from exc

    bot_ids = [str(item).strip() for item in formation.bot_ids]
    if len(bot_ids) < 2 or len(set(bot_ids)) != len(bot_ids):
        raise TaskStateError("planned coop_group requires at least two distinct bots")

    if formation.collab_mode != "manager_worker":
        raise TaskStateError(
            "Relay coop_group plans require manager_worker collaboration"
        )

    manager_bot_id = str(formation.extend_props.get("manager_bot_id") or "").strip()
    if not manager_bot_id or manager_bot_id not in bot_ids:
        raise TaskStateError("planned coop_group requires a manager bot in bot_ids")
    if set(formation.extend_props) != {"manager_bot_id"}:
        raise TaskStateError(
            "pending_group_formation extend_props supports only manager_bot_id"
        )

    members = formation.members_info if formation.members_info is not None else []
    member_managers: list[str] = []
    if not isinstance(members, list):
        raise TaskStateError("pending_group_formation members_info must be a list")
    for member in members:
        if not isinstance(member, dict):
            raise TaskStateError(
                "pending_group_formation members_info entries must be objects"
            )
        role = str(member.get("role") or "").strip().lower()
        if role in {"manager", "master"}:
            member_managers.append(str(member.get("bot_id") or ""))
    if member_managers != [manager_bot_id]:
        raise TaskStateError(
            "planned coop_group requires exactly one manager/master member"
        )

    return formation.to_dict()


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _fingerprint(command: TaskReportCommand) -> str:
    # An unpersisted planning node must not carry a graph reference. This also
    # keeps the canonical content independent of a Backend-generated node ID.
    event = command.event
    if isinstance(event, TaskNode) and event.node_run_graph is not None:
        raise TaskStateError("planned relay node cannot carry a graph reference")
    canonical = {
        "task_id": command.task_id,
        "node_id": command.node_id,
        "event_id": command.event_id,
        "relay_token": command.relay_token,
        "event_type": command.event_type.value,
        "event": asdict(event) if event is not None else None,
        "progress_reason": command.progress_reason,
        "failure_reason": command.failure_reason,
        "task_acceptance_result": (
            asdict(command.task_acceptance_result)
            if command.task_acceptance_result is not None
            else None
        ),
        "trace": asdict(command.trace) if command.trace is not None else None,
    }
    raw = json.dumps(
        _json_value(canonical),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _result_from_record(record: dict[str, Any]) -> TaskReportResult:
    result = record["result"]
    return TaskReportResult(
        status=TaskReportStatus(result["status"]),
        target_node_id=result.get("target_node_id"),
        task_completed=bool(result.get("task_completed")),
        task_hung=bool(result.get("task_hung")),
        bbs_publish_pending=bool(result.get("bbs_publish_pending")),
    )


def accepted_plan_report(graph: Any, node_id: str) -> TaskReportResult | None:
    record = (graph.extend_props.get("_relay_report_receipts") or {}).get(
        f"{node_id}:PLAN_RESULT"
    )
    return _result_from_record(record) if record else None


def apply_relay_report(
    graph_service: Any, command: TaskReportCommand
) -> TaskReportResult:
    """Persist the graph transition and its replay receipt in one graph write."""
    if not command.task_id or not command.node_id:
        raise TaskStateError("relay report requires task_id and node_id")
    if not command.event_id or not command.relay_token:
        raise TaskStateError("relay report requires event_id and relay_token")
    if not isinstance(command.event_type, TaskReportEventType):
        raise TaskStateError("unsupported relay report event type")
    digest = _fingerprint(command)
    now = int(time.time() * 1000)

    def mutation(graph):
        if not graph.is_relay:
            raise TaskStateError("task is not in relay mode")
        node = graph_service._require_node(graph, command.node_id)
        event_records = dict(graph.extend_props.get("_relay_report_event_ids") or {})
        prior_event = event_records.get(command.event_id)
        if prior_event is not None:
            if prior_event["fingerprint"] != digest:
                raise TaskStateError("relay event_id conflicts with an accepted event")
            receipt = (graph.extend_props.get("_relay_report_receipts") or {}).get(
                prior_event["receipt_key"]
            )
            if receipt is None:
                raise TaskStateError("relay event receipt is missing")
            return _result_from_record(receipt), None, False
        current_token = str(node.run_info.extend_props.get("relay_token") or "")
        if not current_token or not hmac.compare_digest(
            current_token, command.relay_token
        ):
            raise TaskStateError("relay execution attempt has expired")
        attempt = int(node.run_info.extend_props.get("harness_retries", 0) or 0)
        key = (
            f"{command.node_id}:EXECUTION_RESULT:{attempt}"
            if command.event_type == TaskReportEventType.EXECUTION_RESULT
            else f"{command.node_id}:PLAN_RESULT"
        )
        receipts = dict(graph.extend_props.get("_relay_report_receipts") or {})
        previous = receipts.get(key)
        if previous is not None:
            if previous["fingerprint"] != digest:
                raise TaskStateError("relay report conflicts with an accepted event")
            return _result_from_record(previous), None, False
        has_successor = any(edge.src_id == command.node_id for edge in graph.relations)
        if node.status != Status.RUNNING:
            raise TaskStateError(
                "relay report requires a RUNNING origin node "
                f"(task_id={command.task_id} node_id={command.node_id} "
                f"status={node.status.value} handed_off={has_successor})"
            )
        if has_successor:
            raise TaskStateError("relay origin has already handed off a successor")

        action_events: list[NodeActionEvent] = []
        # NB: 不再把 trace 注入 action_log payload——trace 的持久载体已改为
        # 上报时逐条独立落 task_trajectory_events 行(mixin._emit_trace_step_rows),
        # 表行即事实,action_log 里再存一份汇总会双源。trace 仍入 _fingerprint
        # (幂等:同 event 不同 trace = 冲突)。
        next_seq = len(node.run_info.action_log) + 1
        graph_repo = getattr(graph_service, "_graph_repo", None)
        if graph_repo is not None:
            next_seq = max(
                next_seq,
                graph_repo.next_action_seq(command.task_id, command.node_id),
            )

        def record_action(
            action: NodeAction,
            payload: dict[str, Any],
            *,
            status_from: Status | None = None,
            status_to: Status | None = None,
        ) -> None:
            nonlocal next_seq
            event_payload = _json_value(dict(payload))
            event_payload["__node_id"] = command.node_id
            event = NodeActionEvent(
                seq=next_seq,
                ts=now,
                action=action,
                loop_round=graph.loop_round,
                attempt=attempt,
                status_from=status_from,
                status_to=status_to,
                payload=event_payload,
            )
            next_seq += 1
            node.run_info.action_log.append(event)
            action_events.append(event)

        if command.event_type == TaskReportEventType.EXECUTION_RESULT:
            patch = command.event
            if not isinstance(patch, TaskNodePatch):
                raise TaskStateError("EXECUTION_RESULT requires TaskNodePatch")
            if patch.task_id != command.task_id or patch.node_id != command.node_id:
                raise TaskStateError("execution patch targets another node")
            if command.task_acceptance_result is not None:
                raise TaskStateError("execution report cannot carry task acceptance")
            decision = str(patch.execution_decision or "").upper()
            if decision not in {"ACCEPTED", "DECLINED"}:
                raise TaskStateError("execution_decision must be ACCEPTED or DECLINED")
            if decision == "ACCEPTED":
                if patch.actual_goal is None or patch.local_acceptance_result is None:
                    raise TaskStateError(
                        "accepted execution requires goal and local acceptance"
                    )
                node.run_info.actual_goal = patch.actual_goal
                node.run_info.acceptance_result = patch.local_acceptance_result
                node.run_info.output.update(patch.output_patch or {})
                # 产物发布点 ①(spec §5.1):ACCEPTED 写点 fold 之后 capture;
                # attempt 失配在 capture 内拒绝,整个回调不落任何产物行。
                _ARTIFACT_PUBLISH.capture_node(
                    graph,
                    node,
                    artifact_patch=getattr(patch, "artifact_patch", None),
                    now_ms=now,
                )
            elif (
                patch.actual_goal is not None
                or patch.local_acceptance_result is not None
                or patch.output_patch
                or getattr(patch, "artifact_patch", None) is not None
            ):
                # DECLINED 夹带 artifacts 属非法事件(§4):整体拒绝,不静默丢弃。
                raise TaskStateError("declined execution cannot carry business facts")
            node.run_info.extend_props["execution_decision"] = decision
            node.run_info.progress_reason = command.progress_reason or None
            node.run_info.failure_reason = (
                patch.failure_reason or command.failure_reason
            )
            record_action(
                NodeAction.EXECUTE,
                {
                    "success": decision == "ACCEPTED",
                    "execution_decision": decision,
                    "actual_goal": asdict(patch.actual_goal)
                    if patch.actual_goal
                    else None,
                    "output": dict(patch.output_patch or {}),
                    "failure_reason": node.run_info.failure_reason,
                },
                status_from=node.status,
                status_to=node.status,
            )
            if decision == "ACCEPTED" and patch.local_acceptance_result is not None:
                record_action(
                    NodeAction.VERIFY,
                    {
                        "verdict": patch.local_acceptance_result.verdict.value,
                        "done_items": list(patch.local_acceptance_result.done_items),
                        "gap_items": list(patch.local_acceptance_result.gap_items),
                    },
                    status_from=node.status,
                    status_to=node.status,
                )
            result = TaskReportResult(TaskReportStatus.APPLIED)
        else:
            if f"{command.node_id}:EXECUTION_RESULT:{attempt}" not in receipts:
                raise TaskStateError("PLAN_RESULT requires accepted execution first")
            next_node = command.event
            acceptance = command.task_acceptance_result
            failure = str(command.failure_reason or "").strip()
            branches = (
                int(isinstance(next_node, TaskNode))
                + int(acceptance is not None)
                + int(bool(failure))
            )
            if branches != 1:
                raise TaskStateError(
                    "PLAN_RESULT requires exactly one plan, completion, or failure"
                )
            if acceptance is not None:
                if acceptance.verdict.value != "DONE":
                    raise TaskStateError("task completion requires passing acceptance")
                node.run_info.progress_reason = command.progress_reason or None
                node.run_info.end_time = now
                node.status = Status.SUCCESS
                graph.status = Status.DONE
                graph.output = _relay_graph_output(graph)
                graph.extend_props["gaps"] = []
                graph.extend_props["task_acceptance_result"] = _json_value(
                    asdict(acceptance)
                )
                # 产物发布点 ②(spec §5.1):completion 写点在同一闭包内 capture;
                # output 未变 → 内容相同由 select-first 查重吸收,不产生重复行。
                _ARTIFACT_PUBLISH.capture_node(
                    graph, node, artifact_patch=None, now_ms=now
                )
                record_action(
                    NodeAction.PLAN,
                    {
                        "completed": True,
                        "task_acceptance_result": asdict(acceptance),
                        "progress_reason": command.progress_reason,
                    },
                    status_from=Status.RUNNING,
                    status_to=Status.SUCCESS,
                )
                record_action(
                    NodeAction.TRANSITION,
                    {
                        "reason": "task_completed",
                    },
                    status_from=Status.RUNNING,
                    status_to=Status.SUCCESS,
                )
                result = TaskReportResult(TaskReportStatus.APPLIED, task_completed=True)
            elif failure:
                node.run_info.progress_reason = command.progress_reason or None
                node.run_info.end_time = now
                node.status = Status.HUNG
                node.run_info.failure_reason = failure
                graph.status = Status.HUNG
                graph.extend_props["hung_reason"] = failure
                record_action(
                    NodeAction.PLAN,
                    {
                        "completed": False,
                        "failure_reason": failure,
                        "progress_reason": command.progress_reason,
                    },
                    status_from=Status.RUNNING,
                    status_to=Status.HUNG,
                )
                record_action(
                    NodeAction.TRANSITION,
                    {
                        "reason": "planning_failed",
                        "failure_reason": failure,
                    },
                    status_from=Status.RUNNING,
                    status_to=Status.HUNG,
                )
                result = TaskReportResult(TaskReportStatus.APPLIED, task_hung=True)
            else:
                assert isinstance(next_node, TaskNode)
                if (
                    next_node.task_id != command.task_id
                    or next_node.node_id is not None
                    or next_node.status is not None
                ):
                    raise TaskStateError("PLAN_RESULT requires an unpersisted TaskNode")
                if not next_node.task_spec.goal.objective.strip():
                    raise TaskStateError("planned relay node requires a goal")
                mode = next_node.run_info.run_mode
                assignee = next_node.run_info.assignee
                config = graph.extend_props.get("execution_config") or {}
                allowed = (graph.extend_props.get("runtime_profile") or {}).get(
                    "allowed_run_modes"
                ) or ["single_bot", "coop_group", "bbs"]
                if mode not in allowed:
                    raise TaskStateError("planned run mode is not allowed")
                if mode == "coop_group" and config.get("intake_session_id"):
                    raise TaskStateError(
                        "Group relay handoff is not supported in the first release"
                    )
                planned_group_formation: dict[str, Any] | None = None
                if mode == "single_bot":
                    if not assignee:
                        raise TaskStateError("single_bot plan requires a bot assignee")
                    if (
                        next_node.run_info.extend_props.get("pending_group_formation")
                        is not None
                    ):
                        raise TaskStateError(
                            "single_bot plan cannot carry a group formation"
                        )
                elif mode == "coop_group":
                    if assignee:
                        raise TaskStateError(
                            "Relay coop_group plan cannot preselect an existing group"
                        )
                    planned_group_formation = _planned_relay_group_formation(
                        next_node.run_info.extend_props.get("pending_group_formation")
                    )
                elif mode == "bbs":
                    if assignee:
                        raise TaskStateError("BBS plan cannot preselect an assignee")
                    if (
                        next_node.run_info.extend_props.get("pending_group_formation")
                        is not None
                    ):
                        raise TaskStateError("BBS plan cannot carry a group formation")
                max_rounds = int(config.get("MAX_LOOP", 3))
                node.run_info.progress_reason = command.progress_reason or None
                node.run_info.end_time = now
                if graph.loop_round >= max_rounds:
                    node.status = Status.HUNG
                    node.run_info.failure_reason = "relay round limit reached"
                    graph.status = Status.HUNG
                    graph.extend_props["hung_reason"] = node.run_info.failure_reason
                    record_action(
                        NodeAction.PLAN,
                        {
                            "completed": False,
                            "failure_reason": node.run_info.failure_reason,
                            "progress_reason": command.progress_reason,
                        },
                        status_from=Status.RUNNING,
                        status_to=Status.HUNG,
                    )
                    record_action(
                        NodeAction.TRANSITION,
                        {
                            "reason": "relay_round_limit",
                        },
                        status_from=Status.RUNNING,
                        status_to=Status.HUNG,
                    )
                    result = TaskReportResult(TaskReportStatus.APPLIED, task_hung=True)
                else:
                    node.status = Status.DONE
                    graph.status = Status.RUNNING
                    target_id = f"relay-{uuid.uuid4().hex[:8]}"
                    child = TaskNode(
                        node_id=target_id,
                        task_id=command.task_id,
                        status=Status.PENDING,
                        task_spec=next_node.task_spec,
                        run_info=RuntimeInfo(
                            run_mode=mode,
                            assignee=assignee,
                            progress_reason=command.progress_reason or None,
                            failure_reason=next_node.run_info.failure_reason,
                            extend_props={
                                **next_node.run_info.extend_props,
                                **(
                                    {"pending_group_formation": planned_group_formation}
                                    if planned_group_formation is not None
                                    else {}
                                ),
                                "relay_token": uuid.uuid4().hex,
                            },
                        ),
                        node_run_graph=graph,
                    )
                    child.run_info.extend_props["relay_planned_by"] = command.node_id
                    graph.tasks.append(child)
                    graph.relations.append(
                        Relation(command.node_id, target_id, RelationType.DEPENDENCY)
                    )
                    graph.loop_round += 1
                    if mode == "bbs":
                        graph.extend_props["bbs_mode"] = True
                        graph.extend_props["bbs_node_id"] = target_id
                    record_action(
                        NodeAction.PLAN,
                        {
                            "completed": False,
                            "target_node_id": target_id,
                            "next_task_spec": next_node.task_spec.to_dict(),
                            "run_mode": mode,
                            "assignee": assignee,
                            "pending_group_formation": planned_group_formation,
                            "progress_reason": command.progress_reason,
                        },
                        status_from=Status.RUNNING,
                        status_to=Status.DONE,
                    )
                    record_action(
                        NodeAction.TRANSITION,
                        {
                            "reason": "relay_handoff",
                            "target_node_id": target_id,
                        },
                        status_from=Status.RUNNING,
                        status_to=Status.DONE,
                    )
                    result = TaskReportResult(
                        TaskReportStatus.APPLIED,
                        target_node_id=target_id,
                        bbs_publish_pending=mode == "bbs",
                    )
        receipts[key] = {"fingerprint": digest, "result": _json_value(asdict(result))}
        graph.extend_props["_relay_report_receipts"] = receipts
        event_records[command.event_id] = {
            "fingerprint": digest,
            "receipt_key": key,
        }
        graph.extend_props["_relay_report_event_ids"] = event_records
        return result, action_events, True

    return graph_service._mutate_with_version_retry(command.task_id, mutation)
