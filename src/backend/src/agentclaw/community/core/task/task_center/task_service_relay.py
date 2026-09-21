"""Skill-driven distributed relay operations for :class:`TaskService`."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Goal,
    Status,
    TaskGraphPatch,
    TaskNode,
    TaskNodePatch,
    NodeOpResult,
    TaskOpResult,
    TaskCallbackData,
    task_spec_instruction,
)
from agentclaw.community.core.task.repository.serializers import task_spec_from_dict
from agentclaw.community.core.task.task_center.relay import (
    RelayCoordinator,
    emit_relay_callback_error,
    emit_relay_event,
    emit_relay_callback_success,
    relay_attempt,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
)

logger = logging.getLogger("task.relay.search")


class TaskServiceRelayMixin:
    """Relay flow: report execution/plan/dispatch decisions, then dispatch once."""

    def _relay(self) -> RelayCoordinator:
        return RelayCoordinator(self._graph)

    def _emit_relay(self, **details: Any) -> None:
        emit_relay_event(self, **details)

    def record_relay_callback_success(self, **details: Any) -> None:
        emit_relay_callback_success(self, **details)

    def record_relay_callback_error(self, **details: Any) -> None:
        emit_relay_callback_error(self, **details)

    def _relay_attempt(self, task_id: str) -> int:
        return relay_attempt(self, task_id)

    def _report_fact(
        self, report_type: str, payload: dict[str, Any], *, event_id: str | None = None
    ) -> Any:
        """Submit Relay graph facts through TaskGraphService.report only."""
        envelope = {"report_type": report_type, "payload": payload}
        if event_id:
            envelope["event_id"] = event_id
        return self._graph.report(TaskCallbackData(data=envelope))

    def _report_node_patch(self, patch: TaskNodePatch) -> Any:
        return self._report_fact("NODE_PATCH", {"patch": patch})

    def _report_relay_turn(self, report_type: str, **payload: Any) -> Any:
        return self._report_fact(report_type, payload)

    def _report_graph_patch(self, task_id: str, patch: TaskGraphPatch) -> Any:
        return self._report_fact("GRAPH_PATCH", {"task_id": task_id, "patch": patch})

    def _report_add_nodes(
        self,
        task_id: str,
        nodes: list[TaskNode],
        *,
        parent_node_id: str | None,
        mark_parent_planning: bool,
    ) -> Any:
        return self._report_fact(
            "ADD_NODES",
            {
                "task_id": task_id,
                "nodes": nodes,
                "parent_node_id": parent_node_id,
                "mark_parent_planning": mark_parent_planning,
            },
        )

    def _bootstrap_relay(
        self, task_id: str, owner_bot_id: str, run_id: int
    ) -> TaskOpResult:
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=task_id,
                status=Status.RUNNING,
                run_mode="single_bot",
                assignee=owner_bot_id,
                progress_reason="主 Bot 已确认任务，直接执行首棒",
                extend_props_patch={"relay_root": True},
            )
        )
        self._emit_relay(
            task_id=task_id,
            node_id=task_id,
            action_result="bootstrap",
            status_to=Status.RUNNING,
            ext_info={"assignee": owner_bot_id, "relay_root": True, "run_id": run_id},
        )
        return TaskOpResult(
            task_id=task_id,
            success=True,
            run_id=run_id,
            extend_props={"orchestration_mode": "relay", "root_node_id": task_id},
        )

    def _relay_node(self, task_id: str, node_id: str) -> tuple[Any, TaskNode]:
        graph = self._graph.query_task_dashboard(task_id)
        config = graph.extend_props.get("execution_config", {}) or {}
        if config.get("orchestration_mode") != "relay":
            raise TaskStateError(f"task={task_id} is not in relay mode")
        node = next((item for item in graph.tasks if item.node_id == node_id), None)
        if node is None:
            raise TaskStateError(f"relay node not found task={task_id} node={node_id}")
        return graph, node

    @staticmethod
    def _holder_matches(node: TaskNode, graph, holder_id: str) -> bool:
        expected = str(
            node.run_info.extend_props.get("relay_holder_id")
            or node.run_info.assignee
            or graph.extend_props.get("owner_bot_id")
            or ""
        )
        return (
            holder_id == expected
            or holder_id.partition(":")[0] == expected.partition(":")[0]
        )

    async def report_task_event(
        self,
        *,
        task_id: str,
        node_id: str,
        event_type: str,
        event_id: str,
        holder_id: str,
        payload: dict[str, Any],
        relay_turn: str | None = None,
        progress_reason: str | None = None,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        if event_type == "SEARCH_RESULT":
            event_type = "DISPATCH_RESULT"
        if event_type not in {"EXECUTION_RESULT", "PLAN_RESULT", "DISPATCH_RESULT"}:
            raise TaskStateError(f"unsupported relay event_type={event_type}")
        progress_reason = self._required_reason(
            progress_reason, "progress_reason is required for relay task events"
        )
        if (
            event_type == "DISPATCH_RESULT"
            and str(payload.get("outcome") or "").upper() == "MISS"
        ):
            failure_reason = self._required_reason(
                failure_reason, "failure_reason is required for relay dispatch MISS"
            )

        relay = self._relay()
        graph, node = self._relay_node(task_id, node_id)
        event_key = f"{event_type}:{event_id}"
        reservation = relay.begin_event(task_id, event_key)
        reservation_state = str(reservation.get("state") or "")
        if reservation_state == "COMPLETED":
            stored = dict(reservation.get("result") or {})
            stored["ok"] = True
            stored["idempotent"] = True
            if event_type == "EXECUTION_RESULT":
                latest_graph, latest_node = self._relay_node(task_id, node_id)
                graph_terminal = getattr(
                    latest_graph.status, "value", latest_graph.status
                ) in {"DONE", "SUCCESS", "HUNG", "FAILED", "CANCELLED"}
                node_terminal = getattr(
                    latest_node.status, "value", latest_node.status
                ) in {"DONE", "SUCCESS", "HUNG", "FAILED", "CANCELLED"}
                if graph_terminal or node_terminal:
                    return {"ok": True, "idempotent": True, "turn_consumed": True}
                turn = self._report_relay_turn(
                    "RELAY_TURN_GRANT",
                    task_id=task_id,
                    node_id=node_id,
                    holder_id=holder_id,
                    retry_event=True,
                )
                if turn is None:
                    stored["turn_consumed"] = True
                else:
                    stored.update(self._turn_response(turn, idempotent=True))
            return stored
        if reservation_state == "PROCESSING":
            return {"ok": True, "idempotent": True, "event_in_progress": True}

        try:
            if event_type == "EXECUTION_RESULT":
                result = self._apply_execution_result(
                    graph, node, holder_id, payload, progress_reason, failure_reason
                )
                turn = self._report_relay_turn(
                    "RELAY_TURN_GRANT",
                    task_id=task_id,
                    node_id=node_id,
                    holder_id=holder_id,
                )
                if turn is None:
                    raise AssertionError("new relay execution did not receive a turn")
                result.update(self._turn_response(turn))
            else:
                if not relay_turn:
                    raise TaskStateError(
                        "relay_turn is required after execution report"
                    )
                try:
                    turn_node_id = relay.require(
                        task_id, node_id, holder_id, relay_turn
                    )
                except TaskStateError as exc:
                    self._emit_relay(
                        task_id=task_id,
                        node_id=node_id,
                        action_result="turn_invalid",
                        error_type=ReasonCatalog.RELAY,
                        error_msg=str(exc),
                        attempt=self._relay_attempt(task_id),
                        ext_info={
                            "holder_id": holder_id,
                            "relay_turn_prefix": relay_turn[:8],
                        },
                    )
                    raise
                if event_type == "PLAN_RESULT":
                    if node_id != turn_node_id:
                        raise TaskStateError(
                            "relay plan must target the turn origin node"
                        )
                    result = self._apply_plan_result(
                        graph, node, holder_id, payload, progress_reason, failure_reason
                    )
                    if result.get("completed") or result.get("hung"):
                        self._report_relay_turn(
                            "RELAY_TURN_CONSUME",
                            task_id=task_id,
                            node_id=node_id,
                            holder_id=holder_id,
                            token=relay_turn,
                        )
                    else:
                        result["dispatch_turn"] = relay_turn
                else:
                    if (
                        node.run_info.extend_props.get("relay_planned_by")
                        != turn_node_id
                    ):
                        raise TaskStateError(
                            "relay dispatch decision is outside the current turn plan"
                        )
                    result = await self._apply_search_result(
                        graph,
                        node,
                        holder_id,
                        payload,
                        relay_turn,
                        progress_reason,
                        failure_reason,
                    )

            if event_type == "EXECUTION_RESULT":
                decision = str(payload.get("execution_decision") or "").upper()
                succeeded = decision != "DECLINED" and bool(
                    payload.get("success", True)
                )
                self._emit_relay(
                    task_id=task_id,
                    node_id=node_id,
                    action_result="execution_result"
                    if succeeded
                    else "execution_failed",
                    error_type=None if succeeded else ReasonCatalog.RELAY,
                    error_msg=None
                    if succeeded
                    else str(failure_reason or "execution declined"),
                    status_to=Status.RUNNING,
                    attempt=self._relay_attempt(task_id),
                    boost_reason=progress_reason,
                    ext_info={"holder_id": holder_id, "relay_turn_granted": True},
                )
            elif event_type == "PLAN_RESULT":
                failed = bool(result.get("hung"))
                self._emit_relay(
                    task_id=task_id,
                    node_id=node_id,
                    action_result="gap_hung" if failed else "plan_result",
                    error_type=ReasonCatalog.RELAY if failed else None,
                    error_msg=str(result.get("failure_reason") or "")
                    if failed
                    else None,
                    status_to=Status.HUNG
                    if failed
                    else (Status.SUCCESS if result.get("completed") else Status.DONE),
                    attempt=self._relay_attempt(task_id),
                    boost_reason=progress_reason,
                    ext_info={
                        "completed": bool(result.get("completed")),
                        "target_node_id": result.get("target_node_id"),
                        "holder_id": holder_id,
                    },
                )

            relay.complete_event(task_id, event_key, result)
            self._report_relay_turn(
                "RELAY_TURN_MARK_EVENT",
                task_id=task_id,
                node_id=node_id,
                holder_id=holder_id,
                event_key=event_key,
            )
            return result
        except Exception:
            relay.release_event(task_id, event_key)
            raise

    def _apply_execution_result(
        self, graph, node, holder_id, payload, progress_reason, failure_reason
    ) -> dict[str, Any]:
        if not self._holder_matches(node, graph, holder_id):
            raise TaskStateError(
                f"relay execution reporter is not assignee node={node.node_id}"
            )
        decision = str(payload.get("execution_decision") or "").upper()
        if not decision:
            decision = "ACCEPTED" if bool(payload.get("success", True)) else "DECLINED"
        if decision not in {"ACCEPTED", "DECLINED"}:
            raise TaskStateError("execution_decision must be ACCEPTED or DECLINED")
        actual_goal = self._goal_from_payload(payload.get("actual_goal"))
        acceptance = self._acceptance_from_payload(payload.get("acceptance_result"))
        output = payload.get("output")
        output_patch = (
            output
            if isinstance(output, dict)
            else ({"result": output} if output is not None else {})
        )
        if decision == "ACCEPTED":
            actual_goal = actual_goal or node.task_spec.goal
            if acceptance is None:
                raise TaskStateError("ACCEPTED execution requires acceptance_result")
        else:
            if actual_goal is not None or output_patch or acceptance is not None:
                raise TaskStateError("DECLINED execution cannot contain business facts")
            failure_reason = self._required_reason(
                failure_reason or "capability_mismatch",
                "DECLINED execution requires failure_reason",
            )
        self._report_node_patch(
            TaskNodePatch(
                task_id=graph.task_id,
                node_id=node.node_id,
                actual_goal=actual_goal,
                local_acceptance_result=acceptance,
                execution_decision=decision,
                output_patch=output_patch,
                progress_reason=progress_reason,
                failure_reason=failure_reason,
                extend_props_patch={
                    "relay_execution_reported_at": int(time.time() * 1000),
                },
            )
        )
        if node.run_info.run_mode == "bbs":
            self._report_node_patch(
                TaskNodePatch(
                    task_id=graph.task_id,
                    node_id=node.node_id,
                    extend_props_patch={"bbs_owner": None},
                )
            )
        return {"ok": True, "execution_recorded": True, "node_id": node.node_id}

    @staticmethod
    def _goal_from_payload(value: Any) -> Goal | None:
        if not isinstance(value, dict):
            return None
        objective = str(value.get("objective") or "").strip()
        if not objective:
            return None
        return Goal(
            objective=objective,
            acceptances=[
                AcceptanceCriteria(
                    id=str(item.get("id") or ""),
                    description=str(
                        item.get("description") or item.get("acceptance") or ""
                    ),
                )
                for item in value.get("acceptances") or []
                if isinstance(item, dict)
            ],
        )

    @staticmethod
    def _acceptance_from_payload(value: Any) -> AcceptanceResult | None:
        if not isinstance(value, dict):
            return None
        try:
            verdict = AcceptanceVerdict(value.get("verdict"))
        except (TypeError, ValueError) as exc:
            raise TaskStateError("invalid acceptance_result.verdict") from exc
        return AcceptanceResult(
            verdict=verdict,
            acceptances_metric=list(value.get("acceptances_metric") or []),
            gaps=list(value.get("gaps") or []),
        )

    async def resume_expired_relay_turn(self, task_id: str) -> bool:
        """Renew and resume an expired Relay planning lease for its current holder.

        The recovery targets only the baton node and graph-level lease metadata.
        It never invokes centralized parent/root reconciliation or re-executes
        the already reported business work.
        """
        graph = self._graph.query_task_dashboard(task_id)
        config = graph.extend_props.get("execution_config", {}) or {}
        if config.get("orchestration_mode") != "relay":
            return False
        current = graph.extend_props.get("relay_turn") or {}
        node_id = str(current.get("node_id") or "")
        holder_id = str(current.get("holder_id") or "")
        if not node_id or not holder_id:
            return False
        node = next((item for item in graph.tasks if item.node_id == node_id), None)
        if node is None:
            logger.warning(
                "[task][relay] resume rejected: baton node missing task=%s node=%s",
                task_id,
                node_id,
            )
            return False
        max_resumes = int(config.get("RELAY_RESUME_MAX", 2))
        resumes = int(node.run_info.extend_props.get("relay_resume_count", 0))
        if resumes >= max_resumes:
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=node_id,
                    status=Status.HUNG,
                    failure_reason="relay planning timeout exceeded resume limit",
                    extend_props_patch={"relay_resume_exhausted": True},
                )
            )
            self._report_graph_patch(
                task_id,
                TaskGraphPatch(
                    status=Status.HUNG,
                    extend_props_patch={
                        "hung_reason": "relay planning timeout exceeded resume limit",
                        "relay_recovery_exhausted": True,
                    },
                ),
            )
            self._report_relay_turn(
                "RELAY_TURN_EXPIRE",
                task_id=task_id,
                node_id=node_id,
                holder_id=holder_id,
            )
            logger.warning(
                "[task][relay] resume exhausted task=%s node=%s holder=%s resumes=%s max=%s",
                task_id,
                node_id,
                holder_id,
                resumes,
                max_resumes,
            )
            self._emit_relay(
                task_id=task_id,
                node_id=node_id,
                action_result="resume_exhausted_hung",
                error_type=ReasonCatalog.RELAY,
                error_msg="relay planning timeout exceeded resume limit",
                status_to=Status.HUNG,
                attempt=resumes,
                ext_info={"holder_id": holder_id, "max_resumes": max_resumes},
            )
            return False
        turn = self._report_relay_turn(
            "RELAY_TURN_RENEW_EXPIRED",
            task_id=task_id,
            node_id=node_id,
            holder_id=holder_id,
        )
        if turn is None:
            return False
        refreshed_node = self._relay_node(task_id, node_id)[1]
        delivered = await self._relay_adapter.runner.resume_relay_turn(
            refreshed_node, turn.token
        )
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                extend_props_patch={
                    "relay_resume_count": resumes + 1,
                    "relay_resume_delivered": delivered,
                    "relay_resume_at_ms": int(time.time() * 1000),
                },
            )
        )
        logger.info(
            "[task][relay] expired planning turn resumed task=%s node=%s holder=%s delivered=%s",
            task_id,
            node_id,
            holder_id,
            delivered,
        )
        self._emit_relay(
            task_id=task_id,
            node_id=node_id,
            action_result="turn_resume",
            error_type=None if delivered else ReasonCatalog.RELAY,
            error_msg=None if delivered else "relay resume delivery failed",
            attempt=resumes + 1,
            ext_info={"holder_id": holder_id, "delivered": delivered},
        )
        return delivered

    @staticmethod
    def _required_reason(value: str | None, message: str) -> str:
        reason = str(value or "").strip()
        if not reason:
            raise TaskStateError(message)
        return reason

    @staticmethod
    def _turn_response(turn, *, idempotent: bool = False) -> dict[str, Any]:
        return {
            "ok": True,
            "idempotent": idempotent,
            "relay_turn": turn.token,
            "relay_turn_node_id": turn.node_id,
            "expires_at_ms": turn.expires_at_ms,
        }

    def _apply_plan_result(
        self, graph, node, holder_id, payload, progress_reason, failure_reason
    ) -> dict[str, Any]:
        gaps = [
            str(item).strip() for item in payload.get("gaps") or [] if str(item).strip()
        ]
        next_raw = payload.get("next_task_spec")
        # Migration compatibility for legacy has_gap/children payloads.
        if next_raw is None and payload.get("has_gap"):
            children = payload.get("children") or []
            if len(children) == 1 and isinstance(children[0], dict):
                next_raw = children[0].get("task_spec") or children[0]
            if not gaps:
                detail = str(
                    payload.get("gap_detail") or failure_reason or "任务仍有未覆盖范围"
                )
                gaps = [detail]
        next_spec = (
            task_spec_from_dict(dict(next_raw)) if isinstance(next_raw, dict) else None
        )
        if next_spec is not None and not next_spec.goal.objective.strip():
            raise TaskStateError("relay next_task_spec requires goal.objective")
        max_rounds = int(
            self._graph._execution_config(graph.task_id).get("MAX_LOOP", 3)
        )
        return self._report_fact(
            "RELAY_PLAN_RESULT",
            {
                "task_id": graph.task_id,
                "origin_node_id": node.node_id,
                "gaps": gaps,
                "next_task_spec": next_spec,
                "holder_id": holder_id,
                "max_rounds": max_rounds,
                "progress_reason": progress_reason,
                "failure_reason": failure_reason,
            },
        )

    def _complete_relay_task(
        self, task_id: str, node_id: str, reason: str | None
    ) -> None:
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                status=Status.SUCCESS,
                progress_reason=reason or "gap 已闭合，任务完成",
            )
        )
        # Relay is a serial baton, not a parent/child aggregation workflow.
        # Nodes that hand off a next step are already marked DONE in
        # _apply_plan_result; terminal completion only closes the graph.
        self._report_graph_patch(task_id, TaskGraphPatch(status=Status.DONE))

    async def search_task_candidates(self, *, query: str) -> dict[str, Any]:
        return await self._relay_adapter.search.search_catalog(query)

    def _schedule_relay_bbs_selection(self, task_id: str, node_id: str) -> None:
        """Run the centralized BBS roster/bid selector on an existing Relay node."""
        node = self._relay_node(task_id, node_id)[1]
        task = asyncio.create_task(self._relay_adapter.runner.start_run([node]))
        tasks = getattr(self, "_bg_tasks", None)
        if isinstance(tasks, set):
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        logger.info(
            "[task][relay-bbs] scheduled centralized dynamic selection task=%s node=%s",
            task_id,
            node_id,
        )

    async def _apply_search_result(
        self,
        graph,
        node,
        holder_id,
        payload,
        relay_turn,
        progress_reason,
        failure_reason,
    ) -> dict[str, Any]:
        if node.status != Status.PENDING:
            raise TaskStateError(
                f"relay dispatch decision target must be PENDING node={node.node_id}"
            )
        outcome = str(payload.get("outcome") or "").upper()
        next_bots = [
            str(item).strip()
            for item in (payload.get("next_relay_bots") or payload.get("bot_ids") or [])
            if str(item).strip()
        ]
        driver = str(
            payload.get("driver_bot_id")
            or payload.get("assignee")
            or (next_bots[0] if next_bots else "")
        ).strip()
        if outcome == "HIT_SINGLE":
            if not next_bots and driver:
                next_bots = [driver]
            if len(next_bots) != 1 or driver != next_bots[0]:
                raise TaskStateError(
                    "HIT_SINGLE requires one next_relay_bot equal to driver_bot_id"
                )
            patch = TaskNodePatch(
                task_id=graph.task_id,
                node_id=node.node_id,
                run_mode="single_bot",
                assignee=driver,
                progress_reason=progress_reason,
                failure_reason=failure_reason,
                extend_props_patch={
                    "relay_holder_id": driver,
                    "driver_bot_id": driver,
                    "next_relay_bots": next_bots,
                },
            )
        elif outcome == "HIT_MULTI_BOTS":
            if len(next_bots) < 2 or not driver or driver not in next_bots:
                raise TaskStateError(
                    "HIT_MULTI_BOTS requires driver_bot_id in next_relay_bots"
                )
            members_info = payload.get("members_info") or [
                {
                    "bot_id": bot_id,
                    "role": "manager" if bot_id == driver else "worker",
                }
                for bot_id in next_bots
            ]
            formation = GroupFormation(
                bot_ids=next_bots,
                collab_mode=str(payload.get("collab_mode") or "manager_worker"),
                group_name=payload.get("group_name"),
                members_info=members_info,
                extend_props={
                    **dict(payload.get("group_extend_props") or {}),
                    "relay_execution": True,
                    "dynamic_task_node_protocol": True,
                    "task_id": graph.task_id,
                    "loop_task_id": f"{graph.task_id}::{node.node_id}",
                    "task_objective": node.task_spec.goal.objective,
                    "task_instruction": task_spec_instruction(node.task_spec),
                    "task_context": node.task_spec.context.background,
                    "acceptances": [
                        item.to_dict() for item in node.task_spec.goal.acceptances
                    ],
                    "manager_bot_id": driver,
                    "originator_bot_id": driver,
                    "owner_user_id": graph.extend_props.get("owner_user_id"),
                },
            )
            patch = TaskNodePatch(
                task_id=graph.task_id,
                node_id=node.node_id,
                run_mode="coop_group",
                assignee=driver,
                progress_reason=progress_reason,
                failure_reason=failure_reason,
                extend_props_patch={
                    "pending_group_formation": formation.to_dict(),
                    "relay_holder_id": driver,
                    "driver_bot_id": driver,
                    "next_relay_bots": next_bots,
                },
            )
        elif outcome == "MISS":
            reason = failure_reason or str(
                payload.get("miss_reason") or "搜推没有匹配结果"
            )
            self._report_node_patch(
                TaskNodePatch(
                    task_id=graph.task_id,
                    node_id=node.node_id,
                    run_mode="bbs",
                    progress_reason=progress_reason,
                    failure_reason=reason,
                    extend_props_patch={
                        "driver_bot_id": None,
                        "next_relay_bots": [],
                    },
                )
            )
            self._report_graph_patch(
                graph.task_id,
                TaskGraphPatch(
                    extend_props_patch={"bbs_mode": True, "bbs_node_id": node.node_id}
                ),
            )
            self._report_relay_turn(
                "RELAY_TURN_CONSUME",
                task_id=graph.task_id,
                node_id=node.node_id,
                holder_id=holder_id,
                token=relay_turn,
            )
            self._schedule_relay_bbs_selection(graph.task_id, node.node_id)
            self._emit_relay(
                task_id=graph.task_id,
                node_id=node.node_id,
                action_result="miss",
                attempt=self._relay_attempt(graph.task_id),
                boost_reason=progress_reason,
                ext_info={
                    "published_bbs": True,
                    "miss_reason": reason,
                    "holder_id": holder_id,
                },
            )
            return {"ok": True, "published_bbs": True, "node_id": node.node_id}
        else:
            raise TaskStateError(f"unsupported dispatch outcome={outcome}")
        self._report_node_patch(patch)
        self._emit_relay(
            task_id=graph.task_id,
            node_id=node.node_id,
            action_result="hit_single" if outcome == "HIT_SINGLE" else "hit_multi",
            attempt=self._relay_attempt(graph.task_id),
            boost_reason=progress_reason,
            ext_info={
                "driver_bot_id": driver,
                "next_relay_bots": next_bots,
                "holder_id": holder_id,
                "planned_by": node.run_info.extend_props.get("relay_planned_by"),
            },
        )
        return {
            "ok": True,
            "published_bbs": False,
            "node_id": node.node_id,
            "driver_bot_id": driver,
            "next_relay_bots": next_bots,
        }

    async def dispatch_task(
        self,
        *,
        task_id: str,
        origin_node_id: str,
        target_node_id: str,
        holder_id: str,
        relay_turn: str,
        dispatch_id: str,
    ) -> dict[str, Any]:
        relay = self._relay()
        _, node = self._relay_node(task_id, target_node_id)
        if node.run_info.extend_props.get("relay_dispatch_id") == dispatch_id:
            return {"ok": True, "idempotent": True, "node_id": target_node_id}
        try:
            turn_node_id = relay.require(task_id, origin_node_id, holder_id, relay_turn)
        except TaskStateError as exc:
            self._emit_relay(
                task_id=task_id,
                node_id=target_node_id,
                action_result="turn_invalid",
                error_type=ReasonCatalog.RELAY,
                error_msg=str(exc),
                attempt=self._relay_attempt(task_id),
                ext_info={"holder_id": holder_id, "relay_turn_prefix": relay_turn[:8]},
            )
            raise
        if turn_node_id != origin_node_id:
            raise TaskStateError("relay dispatch origin does not own current turn")
        if node.run_info.extend_props.get("relay_planned_by") != origin_node_id:
            raise TaskStateError("relay dispatch target was not planned by origin node")
        if node.status != Status.PENDING:
            raise TaskStateError(
                f"relay dispatch target must be PENDING node={target_node_id}"
            )
        if node.run_info.run_mode not in {"single_bot", "coop_group"}:
            raise TaskStateError(
                "relay dispatch requires a persisted dispatch decision"
            )
        formation = None
        if node.run_info.run_mode == "coop_group":
            raw = node.run_info.extend_props.get("pending_group_formation") or {}
            formation = GroupFormation.from_dict(raw)
        self._report_relay_turn(
            "RELAY_TURN_CONSUME",
            task_id=task_id,
            node_id=origin_node_id,
            holder_id=holder_id,
            token=relay_turn,
        )
        try:
            extend_patch: dict[str, Any] = {"relay_dispatch_id": dispatch_id}
            if formation is not None:
                group_id = await self._relay_adapter.runner.form_coop_group(formation)
                extend_patch["group_id"] = group_id
            # Persist infrastructure delivery identity first, but expose RUNNING
            # only after the actual Runner delivery succeeds.
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=target_node_id,
                    extend_props_patch=extend_patch,
                )
            )
            refreshed = self._relay_node(task_id, target_node_id)[1]
            delivered = bool(
                (await self._relay_adapter.runner.start_run([refreshed]))[0]
            )
        except Exception:
            logger.exception(
                "[task][relay] dispatch failed task=%s origin=%s target=%s",
                task_id,
                origin_node_id,
                target_node_id,
            )
            delivered = False
        if not delivered:
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=target_node_id,
                    failure_reason="下一棒 Runner 派发失败",
                    extend_props_patch={
                        "relay_dispatch_id": None,
                        "group_id": None,
                    },
                )
            )
            self._report_relay_turn(
                "RELAY_TURN_REOPEN",
                task_id=task_id,
                node_id=origin_node_id,
                holder_id=holder_id,
                token=relay_turn,
            )
            self._emit_relay(
                task_id=task_id,
                node_id=target_node_id,
                action_result="dispatch_failed_reopen",
                error_type=ReasonCatalog.RELAY,
                error_msg=f"relay dispatch failed node={target_node_id}",
                status_from=Status.PENDING,
                status_to=Status.PENDING,
                attempt=self._relay_attempt(task_id),
                ext_info={"holder_id": holder_id, "dispatch_id": dispatch_id},
            )
            raise TaskStateError(f"relay dispatch failed node={target_node_id}")
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=target_node_id,
                status=Status.RUNNING,
                progress_reason="下一棒 Runner 已完成实际投递",
            )
        )
        refreshed = self._relay_node(task_id, target_node_id)[1]
        self._emit_relay(
            task_id=task_id,
            node_id=target_node_id,
            action_result="dispatch",
            status_from=Status.PENDING,
            status_to=Status.RUNNING,
            attempt=self._relay_attempt(task_id),
            ext_info={
                "holder_id": holder_id,
                "dispatch_id": dispatch_id,
                "run_mode": refreshed.run_info.run_mode,
                "assignee": refreshed.run_info.assignee,
            },
        )
        return {
            "ok": True,
            "origin_node_id": origin_node_id,
            "target_node_id": target_node_id,
            "run_mode": refreshed.run_info.run_mode,
            "assignee": refreshed.run_info.assignee,
            "group_id": refreshed.run_info.extend_props.get("group_id"),
        }

    def _claim_relay_bbs(
        self, task_id: str, node_id: str, bot_id: str, claim_id: str | None = None
    ):
        _, node = self._relay_node(task_id, node_id)
        if node.run_info.run_mode != "bbs" or node.status != Status.PENDING:
            raise TaskStateError(f"relay BBS node is not claimable node={node_id}")
        result = self._report_fact(
            "BBS_CLAIM",
            {
                "task_id": task_id,
                "node_id": node_id,
                "bot_id": bot_id,
                "claim_id": claim_id,
            },
        )
        self._emit_relay(
            task_id=task_id,
            node_id=node_id,
            action_result="bbs_claim",
            status_from=Status.PENDING,
            status_to=Status.RUNNING,
            attempt=self._relay_attempt(task_id),
            ext_info={"bbs_bot_id": bot_id},
        )
        return result

    async def _report_relay_bbs_result(
        self,
        *,
        task_id: str,
        node_id: str,
        bot_id: str,
        output_patch: dict | None = None,
        exec_error: str | None = None,
    ) -> NodeOpResult:
        """Fold a relay BBS result into the current baton node only.

        Relay is serial: the claimed BBS node becomes DONE and the next turn is
        granted, but no parent/root status reconciliation or planner callback is
        run. The root ``bbs_owner`` field is only cleared to release the claim.
        """
        graph, node = self._relay_node(task_id, node_id)
        if node.run_info.run_mode != "bbs" or node.status != Status.RUNNING:
            raise TaskStateError(f"relay BBS node is not running node={node_id}")
        if node.run_info.extend_props.get("bbs_owner") != bot_id:
            raise TaskStateError(
                f"relay BBS reporter is not claim owner node={node_id}"
            )

        result = self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                status=Status.DONE,
                assignee=bot_id,
                output_patch=output_patch,
                failure_reason=exec_error,
                progress_reason="BBS 接力节点执行完成，等待下一棒规划",
            )
        )
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                extend_props_patch={"bbs_owner": None},
            )
        )
        turn = self._report_relay_turn(
            "RELAY_TURN_GRANT",
            task_id=task_id,
            node_id=node_id,
            holder_id=bot_id,
        )
        if turn is None:
            raise AssertionError("relay BBS result did not receive a turn")
        self._emit_relay(
            task_id=task_id,
            node_id=node_id,
            action_result="bbs_result",
            status_from=Status.RUNNING,
            status_to=Status.DONE,
            error_type=ReasonCatalog.RELAY if exec_error else None,
            error_msg=str(exec_error) if exec_error else None,
            attempt=self._relay_attempt(task_id),
            ext_info={"bbs_bot_id": bot_id, "relay_turn_granted": True},
        )
        return result
