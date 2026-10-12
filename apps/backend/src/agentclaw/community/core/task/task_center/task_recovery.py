"""Profile-selected recovery strategies for centralized and Relay execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import NodeAction, Status, TaskNodePatch


class TaskRecoveryStrategy(Protocol):
    async def recover(self, patch: TaskNodePatch) -> None: ...


class CentralizedRecoveryStrategy:
    def __init__(self, handler: Callable[[TaskNodePatch], Awaitable[None]]) -> None:
        self._handler = handler

    async def recover(self, patch: TaskNodePatch) -> None:
        await self._handler(patch)


class RelayRecoveryStrategy:
    """Resume one Relay baton from the last graph-accepted phase."""

    def __init__(self, graph, delivery_intents, dispatch_node) -> None:
        self._graph = graph
        self._delivery_intents = delivery_intents
        self._dispatch_node = dispatch_node

    def _max_retries(self, task_id: str) -> int:
        return int(self._graph._execution_config(task_id).get("MAX_HARNESS", 2))

    def _hung(self, task_id: str, node_id: str, reason: str) -> None:
        before = next(
            item for item in self._graph.query_task_dashboard(task_id).tasks
            if item.node_id == node_id
        )
        previous_status = before.status
        attempt = int(before.run_info.extend_props.get("harness_retries", 0) or 0)
        self._graph.update_task_node_info(TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            status=Status.HUNG,
            failure_reason=reason,
            extend_props_patch={"hung_reason": reason},
        ))
        self._graph.append_action_event(
            task_id,
            node_id,
            NodeAction.RESET,
            {
                "reason": reason,
                "action_result": "failed",
                "recovery_phase": "EXHAUSTED",
                "attempt_before": attempt,
                "attempt_after": attempt,
                "token_rotated": False,
            },
            attempt=attempt,
            status_from=previous_status,
            status_to=Status.HUNG,
        )

    async def recover(self, patch: TaskNodePatch) -> None:
        task_id, node_id = patch.task_id, patch.node_id
        graph = self._graph.query_task_dashboard(task_id)
        if not graph.is_relay:
            raise TaskStateError("relay recovery requires a relay graph")
        node = next((item for item in graph.tasks if item.node_id == node_id), None)
        if node is None:
            return

        context = self._graph.get_node_context(task_id, node_id)
        plan = context.accepted_plan_report
        if plan is not None:
            if plan.target_node_id:
                self._graph.append_action_event(
                    task_id,
                    node_id,
                    NodeAction.RESET,
                    {
                        "reason": patch.exec_error or "relay_delivery_resume",
                        "action_result": "resume",
                        "recovery_phase": "AWAITING_DISPATCH",
                        "resume_from": "accepted_plan",
                        "target_node_id": plan.target_node_id,
                        "token_rotated": False,
                    },
                    attempt=int(
                        node.run_info.extend_props.get("harness_retries", 0) or 0
                    ),
                    status_from=node.status,
                    status_to=node.status,
                )
                await self._dispatch_node(task_id, plan.target_node_id)
            return
        if node.status in {
            Status.SUCCESS, Status.DONE, Status.HUNG, Status.FAILED, Status.CANCELLED,
        }:
            return

        reason = patch.exec_error or "relay_execution_timeout"
        if context.accepted_execution_patch is not None:
            previous_status = node.status
            attempt_before = int(
                node.run_info.extend_props.get("harness_retries", 0) or 0
            )
            resume_retries = int(
                node.run_info.extend_props.get("relay_resume_retries", 0) or 0
            )
            if resume_retries >= self._max_retries(task_id):
                self._hung(task_id, node_id, "relay_resume_retries_exhausted")
                return
            resume_retries += 1
            is_bbs = node.run_info.run_mode == "bbs"
            self._graph.update_task_node_info(TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                status=Status.PENDING if is_bbs else None,
                assignee="" if is_bbs else None,
                progress_reason="恢复已接纳执行结果后的规划阶段",
                failure_reason=reason,
                extend_props_patch={
                    "relay_resume_retries": resume_retries,
                    "bbs_owner": None,
                    "bbs_claim_id": None,
                    "recovery_phase": "AWAITING_PLAN",
                },
            ))
            current = next(
                item for item in self._graph.query_task_dashboard(task_id).tasks
                if item.node_id == node_id
            )
            token = str(current.run_info.extend_props.get("relay_token") or "")
            if not token:
                raise TaskStateError("relay recovery target has no run generation")
            self._graph.append_action_event(
                task_id,
                node_id,
                NodeAction.RESET,
                {
                    "reason": reason,
                    "action_result": "resume",
                    "recovery_phase": "AWAITING_PLAN",
                    "attempt_before": attempt_before,
                    "attempt_after": int(
                        current.run_info.extend_props.get("harness_retries", 0) or 0
                    ),
                    "token_rotated": False,
                    "resume_from": "accepted_execution",
                },
                attempt=int(
                    current.run_info.extend_props.get("harness_retries", 0) or 0
                ),
                status_from=previous_status,
                status_to=current.status,
            )
            self._delivery_intents.requeue_delivery_intent(
                task_id, node_id, token, reason=reason,
            )
            await self._dispatch_node(task_id, node_id)
            return

        retries = int(node.run_info.extend_props.get("harness_retries", 0) or 0)
        if retries >= self._max_retries(task_id):
            self._hung(task_id, node_id, "relay_execution_retries_exhausted")
            return
        is_bbs = node.run_info.run_mode == "bbs"
        previous_status = node.status
        previous_token = node.run_info.extend_props.get("relay_token")
        self._graph.update_task_node_info(TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            status=Status.PENDING,
            assignee="" if is_bbs else None,
            progress_reason="恢复未接纳执行结果的当前节点",
            failure_reason=reason,
            extend_props_patch={
                "harness_retries": retries + 1,
                "last_exec_error": reason,
                "bbs_owner": None,
                "bbs_claim_id": None,
                "recovery_phase": "AWAITING_EXECUTION",
            },
        ))
        current = next(
            item for item in self._graph.query_task_dashboard(task_id).tasks
            if item.node_id == node_id
        )
        self._graph.append_action_event(
            task_id,
            node_id,
            NodeAction.RESET,
            {
                "reason": reason,
                "action_result": "retry",
                "recovery_phase": "AWAITING_EXECUTION",
                "attempt_before": retries,
                "attempt_after": retries + 1,
                "token_rotated": (
                    current.run_info.extend_props.get("relay_token")
                    != previous_token
                ),
                "resume_from": "node_start",
            },
            attempt=retries + 1,
            status_from=previous_status,
            status_to=current.status,
        )
        await self._dispatch_node(task_id, node_id)


class TaskRecoveryCoordinator:
    """Select a recovery strategy from the task's frozen runtime profile."""

    def __init__(self, graph, strategies: dict[str, TaskRecoveryStrategy]) -> None:
        self._graph = graph
        self._strategies = dict(strategies)

    async def recover(self, patch: TaskNodePatch) -> None:
        graph = self._graph.query_task_dashboard(patch.task_id)
        profile = graph.extend_props.get("runtime_profile") or {}
        name = str(profile.get("recovery_strategy") or "centralized")
        strategy = self._strategies.get(name)
        if strategy is None:
            raise TaskStateError(f"unknown recovery_strategy={name}")
        await strategy.recover(patch)
