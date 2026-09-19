"""Skill-driven distributed relay operations for :class:`TaskService`."""
from __future__ import annotations

import time
import uuid
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    RuntimeInfo,
    Status,
    TaskGraphPatch,
    TaskNode,
    TaskNodePatch,
    TaskOpResult,
)
from agentclaw.community.core.task.repository.serializers import task_spec_from_dict
from agentclaw.community.core.task.task_center.relay import RelayCoordinator
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation


class TaskServiceRelayMixin:
    """Relay flow: report execution/plan/search decisions, then dispatch once."""

    def _relay(self) -> RelayCoordinator:
        return RelayCoordinator(self._graph)

    def _bootstrap_relay(self, task_id: str, owner_bot_id: str, run_id: int) -> TaskOpResult:
        self._graph.update_task_node_info(
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
        return TaskOpResult(
            task_id=task_id, success=True, run_id=run_id,
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
        return holder_id == expected or holder_id.partition(":")[0] == expected.partition(":")[0]

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
        if event_type not in {"EXECUTION_RESULT", "PLAN_RESULT", "SEARCH_RESULT"}:
            raise TaskStateError(f"unsupported relay event_type={event_type}")
        progress_reason = self._required_reason(
            progress_reason, "progress_reason is required for relay task events"
        )
        if event_type == "EXECUTION_RESULT" and not bool(payload.get("success", True)):
            failure_reason = self._required_reason(
                failure_reason, "failure_reason is required for failed relay execution"
            )
        if event_type == "PLAN_RESULT" and payload.get("has_gap") and not payload.get("children"):
            failure_reason = self._required_reason(
                failure_reason, "failure_reason is required when a relay gap cannot be planned"
            )
        if event_type == "SEARCH_RESULT" and str(payload.get("outcome") or "").upper() == "MISS":
            failure_reason = self._required_reason(
                failure_reason, "failure_reason is required for relay search MISS"
            )
        relay = self._relay()
        graph, node = self._relay_node(task_id, node_id)
        event_key = f"{event_type}:{event_id}"
        if relay.seen_event(task_id, event_key):
            if event_type == "EXECUTION_RESULT":
                turn = relay.grant(
                    task_id, node_id, holder_id, retry_event=True
                )
                if turn is None:
                    return {"ok": True, "idempotent": True, "turn_consumed": True}
                return self._turn_response(turn, idempotent=True)
            return {"ok": True, "idempotent": True}

        if event_type == "EXECUTION_RESULT":
            if not self._holder_matches(node, graph, holder_id):
                raise TaskStateError(f"relay execution reporter is not assignee node={node_id}")
            output = payload.get("output")
            output_patch = output if isinstance(output, dict) else {"result": output}
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=node_id,
                    output_patch=output_patch,
                    progress_reason=progress_reason or "当前执行节点已完成，进入 gap 规划",
                    failure_reason=failure_reason or payload.get("exec_error"),
                    extend_props_patch={
                        "relay_execution_success": bool(payload.get("success", True)),
                        "relay_execution_reported_at": int(time.time() * 1000),
                    },
                )
            )
            if node.run_info.run_mode == "bbs":
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=task_id,
                        extend_props_patch={"bbs_owner": None},
                    )
                )
            turn = relay.grant(task_id, node_id, holder_id)
            if turn is None:
                raise AssertionError("new relay execution did not receive a turn")
            relay.mark_event(task_id, event_key)
            return self._turn_response(turn)

        if not relay_turn:
            raise TaskStateError("relay_turn is required after execution report")
        turn_node_id = relay.require(task_id, node_id, holder_id, relay_turn)
        if event_type == "PLAN_RESULT":
            if node_id != turn_node_id:
                raise TaskStateError("relay plan must target the turn origin node")
            result = self._apply_plan_result(
                graph, node, holder_id, payload, progress_reason, failure_reason
            )
            if result.get("completed") or result.get("hung"):
                relay.consume(task_id, node_id, holder_id, relay_turn)
        elif event_type == "SEARCH_RESULT":
            if node.run_info.extend_props.get("relay_planned_by") != turn_node_id:
                raise TaskStateError("relay search decision is outside the current turn plan")
            result = await self._apply_search_result(
                graph, node, holder_id, payload, relay_turn,
                progress_reason, failure_reason,
            )
        else:
            raise AssertionError("validated relay event type was not handled")
        relay.mark_event(task_id, event_key)
        return result

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
        has_gap = bool(payload.get("has_gap"))
        children_data = payload.get("children") or []
        if not has_gap:
            self._complete_relay_task(graph.task_id, node.node_id, progress_reason)
            return {"ok": True, "completed": True, "children": []}
        max_rounds = int(self._graph._execution_config(graph.task_id).get("MAX_LOOP", 3))
        if graph.loop_round >= max_rounds or not children_data:
            reason = failure_reason or (
                "达到分布式接力迭代轮次上限" if graph.loop_round >= max_rounds else "规划存在 gap 但未产出下一步任务"
            )
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=graph.task_id,
                    node_id=node.node_id,
                    status=Status.HUNG,
                    failure_reason=reason,
                )
            )
            self._graph.update_task_graph_info(
                graph.task_id,
                TaskGraphPatch(status=Status.HUNG, extend_props_patch={"hung_reason": reason}),
            )
            return {"ok": True, "completed": False, "hung": True, "failure_reason": reason}
        if len(children_data) != 1:
            raise TaskStateError(
                "relay plan must produce exactly one next node for serial handoff"
            )

        children: list[TaskNode] = []
        for raw in children_data:
            if not isinstance(raw, dict):
                raise TaskStateError("relay plan child must be an object")
            spec = task_spec_from_dict(dict(raw.get("task_spec") or raw))
            if not spec.metadata.instruction.strip() or not spec.goal.objective.strip():
                raise TaskStateError("relay plan child requires instruction and objective")
            child_id = str(raw.get("node_id") or spec.metadata.task_id or f"relay-{uuid.uuid4().hex[:8]}")
            spec.metadata.task_id = child_id
            children.append(
                TaskNode(
                    node_id=child_id,
                    task_id=graph.task_id,
                    status=Status.PENDING,
                    task_spec=spec,
                    run_info=RuntimeInfo(
                        progress_reason=str(raw.get("progress_reason") or "规划生成的下一棒任务"),
                        extend_props={
                            "relay_planned_by": node.node_id,
                            "relay_planner": holder_id,
                        },
                    ),
                    node_run_graph=graph,
                )
            )
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=graph.task_id,
                node_id=node.node_id,
                status=Status.DONE,
                progress_reason=progress_reason or "当前接力节点已完成并交接下一棒任务",
                failure_reason=failure_reason,
            )
        )
        self._graph.add_task_nodes(
            children, parent_node_id=node.node_id, mark_parent_planning=False
        )
        self._graph.update_task_graph_info(
            graph.task_id, TaskGraphPatch(loop_round_increment=1)
        )
        return {"ok": True, "completed": False, "children": [item.node_id for item in children]}

    def _complete_relay_task(self, task_id: str, node_id: str, reason: str | None) -> None:
        self._graph.update_task_node_info(
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
        self._graph.update_task_graph_info(task_id, TaskGraphPatch(status=Status.DONE))

    @staticmethod
    def _project_search_candidate(item: dict[str, Any]) -> dict[str, Any]:
        """Expose only fields backed by the current Bot catalog/search result."""
        candidate: dict[str, Any] = {
            "bot_uuid": item.get("bot_uuid") or item.get("bot_id"),
        }
        for field_name in ("bot_name", "bot_desc", "bot_type", "status"):
            if field_name in item:
                candidate[field_name] = item[field_name]
        recommend = item.get("recommend")
        if isinstance(recommend, dict):
            candidate["recommend"] = recommend
        return candidate

    async def search_task_candidates(self, *, query: str) -> dict[str, Any]:
        """Search candidates only; task graph decisions remain Skill-owned."""
        import asyncio

        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise TaskStateError("search query is required")
        discover = self._engine._discover
        if discover is None:
            return {"candidates": [], "total": 0}
        try:
            result = await asyncio.to_thread(
                discover.search_by_keyword,
                keyword=normalized_query,
                user_id="",
                top_k=20,
                min_score=0.01,
                filters={"runtime_state": ["online"]},
            )
        except Exception:  # noqa: BLE001 - search failure is an empty candidate result
            return {"candidates": [], "total": 0}
        items = (result or {}).get("items") or []
        candidates = [
            self._project_search_candidate(item)
            for item in items
            if isinstance(item, dict) and (item.get("bot_uuid") or item.get("bot_id"))
        ]
        return {"candidates": candidates[:20], "total": len(candidates[:20])}

    async def _apply_search_result(
        self, graph, node, holder_id, payload, relay_turn, progress_reason, failure_reason
    ) -> dict[str, Any]:
        if node.status != Status.PENDING:
            raise TaskStateError(f"relay search decision target must be PENDING node={node.node_id}")
        outcome = str(payload.get("outcome") or "").upper()
        if outcome == "HIT_SINGLE":
            assignee = str(payload.get("assignee") or "").strip()
            if not assignee:
                raise TaskStateError("HIT_SINGLE requires assignee")
            patch = TaskNodePatch(
                task_id=graph.task_id,
                node_id=node.node_id,
                run_mode="single_bot",
                assignee=assignee,
                progress_reason=progress_reason or f"候选能力匹配，选择 Bot {assignee}",
                failure_reason=failure_reason,
                extend_props_patch={"relay_holder_id": assignee},
            )
        elif outcome == "HIT_MULTI_BOTS":
            bot_ids = [str(item).strip() for item in payload.get("bot_ids") or []]
            if not bot_ids or any(not item for item in bot_ids):
                raise TaskStateError("HIT_MULTI_BOTS requires bot_ids")
            formation = GroupFormation(
                bot_ids=bot_ids,
                collab_mode=str(payload.get("collab_mode") or "manager_worker"),
                group_name=payload.get("group_name"),
                members_info=payload.get("members_info"),
                extend_props={
                    **dict(payload.get("group_extend_props") or {}),
                    "relay_execution": True,
                    "dynamic_task_node_protocol": True,
                    "task_id": graph.task_id,
                    "loop_task_id": f"{graph.task_id}::{node.node_id}",
                    "task_objective": node.task_spec.goal.objective,
                    "task_instruction": node.task_spec.metadata.instruction,
                    "task_context": node.task_spec.context.background,
                    "acceptances": [
                        item.to_dict() for item in node.task_spec.goal.acceptances
                    ],
                    "manager_bot_id": bot_ids[0],
                    "relay_blackboard": {
                        "root_goal": next(
                            item for item in graph.tasks if item.node_id == graph.task_id
                        ).task_spec.goal.to_dict(),
                        "loop_round": graph.loop_round,
                        "nodes": [
                            {
                                "node_id": item.node_id,
                                "status": item.status.value,
                                "goal": item.task_spec.goal.to_dict(),
                                "output": item.run_info.output,
                            }
                            for item in graph.tasks
                        ],
                    },
                },
            )
            patch = TaskNodePatch(
                task_id=graph.task_id,
                node_id=node.node_id,
                run_mode="coop_group",
                progress_reason=progress_reason or "多个候选能力互补，选择协作群执行",
                failure_reason=failure_reason,
                extend_props_patch={
                    "pending_group_formation": formation.to_dict(),
                    "relay_holder_id": bot_ids[0],
                },
            )
        elif outcome == "MISS":
            reason = failure_reason or str(payload.get("miss_reason") or "搜推没有匹配结果")
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=graph.task_id,
                    node_id=node.node_id,
                    run_mode="bbs",
                    progress_reason=progress_reason or "无直接匹配执行者，发布到 BBS 广场",
                    failure_reason=reason,
                )
            )
            self._graph.update_task_graph_info(
                graph.task_id,
                TaskGraphPatch(extend_props_patch={"bbs_mode": True, "bbs_node_id": node.node_id}),
            )
            self._relay().consume(graph.task_id, node.node_id, holder_id, relay_turn)
            return {"ok": True, "published_bbs": True, "node_id": node.node_id}
        else:
            raise TaskStateError(f"unsupported search outcome={outcome}")
        self._graph.update_task_node_info(patch)
        return {"ok": True, "published_bbs": False, "node_id": node.node_id}

    async def dispatch_task(
        self,
        *,
        task_id: str,
        node_id: str,
        holder_id: str,
        relay_turn: str,
        dispatch_id: str,
    ) -> dict[str, Any]:
        relay = self._relay()
        _, node = self._relay_node(task_id, node_id)
        if node.run_info.extend_props.get("relay_dispatch_id") == dispatch_id:
            return {"ok": True, "idempotent": True, "node_id": node_id}
        turn_node_id = relay.require(task_id, node_id, holder_id, relay_turn)
        if node.run_info.extend_props.get("relay_planned_by") != turn_node_id:
            raise TaskStateError("relay dispatch target was not planned by the current turn")
        if node.status != Status.PENDING:
            raise TaskStateError(f"relay dispatch target must be PENDING node={node_id}")
        if node.run_info.run_mode not in {"single_bot", "coop_group"}:
            raise TaskStateError("relay dispatch requires a persisted search decision")
        formation = None
        if node.run_info.run_mode == "coop_group":
            raw = node.run_info.extend_props.get("pending_group_formation") or {}
            formation = GroupFormation.from_dict(raw)
        relay.consume(task_id, node_id, holder_id, relay_turn)
        try:
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=node_id,
                    status=Status.RUNNING,
                    extend_props_patch={"relay_dispatch_id": dispatch_id},
                )
            )
            if formation is not None:
                group_id = await self._engine._runner.form_coop_group(formation)
                self._graph.update_task_node_info(
                    TaskNodePatch(task_id=task_id, node_id=node_id, assignee=group_id)
                )
            refreshed = self._relay_node(task_id, node_id)[1]
            delivered = bool((await self._engine._runner.start_run([refreshed]))[0])
        except Exception:
            delivered = False
        if not delivered:
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=node_id,
                    status=Status.PENDING,
                    failure_reason="下一棒 Runner 派发失败",
                    extend_props_patch={"relay_dispatch_id": None},
                )
            )
            relay.reopen(task_id, holder_id, relay_turn)
            raise TaskStateError(f"relay dispatch failed node={node_id}")
        refreshed = self._relay_node(task_id, node_id)[1]
        return {
            "ok": True,
            "node_id": node_id,
            "run_mode": refreshed.run_info.run_mode,
            "assignee": refreshed.run_info.assignee,
        }

    def _claim_relay_bbs(self, task_id: str, node_id: str, bot_id: str):
        _, node = self._relay_node(task_id, node_id)
        if node.run_info.run_mode != "bbs" or node.status != Status.PENDING:
            raise TaskStateError(f"relay BBS node is not claimable node={node_id}")
        result = self._graph.claim_bbs_owner(task_id, bot_id)
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                status=Status.RUNNING,
                assignee=bot_id,
                progress_reason=f"BBS Bot {bot_id} 主动认领任务",
            )
        )
        return result
