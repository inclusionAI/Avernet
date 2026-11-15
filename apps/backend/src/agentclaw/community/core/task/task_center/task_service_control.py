"""Internal task control operations: manual stop and node updates.

Composed into TaskService; public signatures and execution-adapter routing remain
unchanged. Control actions do not own intake, execution, or delivery recovery.
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    NodeAction,
    NodeOpResult,
    Status,
    TaskNodePatch,
    TaskStopResult,
)


class TaskServiceControlMixin:
    """TaskService's internal implementation of explicit control-plane writes."""

    async def stop_task(
        self,
        task_id: str,
        *,
        reason: str = "manual_stop",
        operator: str = "",
    ) -> TaskStopResult:
        """Manually cancel every non-terminal node in one task graph.

        This is a small and bounded control-plane action, not a retry variant:
        it never re-runs a selected candidate and never resets a node into
        PENDING. It closes non-terminal nodes into the terminal CANCELLED
        state and settles Relay delivery intents so the redriver cannot
        revive a stopped baton.
        """
        graph = self._graph.query_task_dashboard(task_id)
        if graph.status is Status.CANCELLED:
            return TaskStopResult(
                task_id=task_id,
                status=Status.CANCELLED,
                stopped_nodes=[],
                reason=reason,
                operator=operator,
            )
        if graph.status in {Status.SUCCESS, Status.DONE, Status.FAILED, Status.HUNG}:
            raise TaskStateError(
                f"task is already terminal: task_id={task_id} status={graph.status.value}"
            )

        stop_nodes = [
            node for node in graph.tasks
            if node.status in {Status.PENDING, Status.PLANNING, Status.RUNNING}
        ]
        if not stop_nodes:
            raise TaskStateError(
                f"task has no cancellable nodes: task_id={task_id} status={graph.status.value}"
            )

        stopped_nodes: list[str] = []
        patch_props = {
            "manual_stop": True,
            "stop_reason": reason,
            "stop_operator": operator or "unauthenticated",
        }
        for node in stop_nodes:
            previous_status = node.status
            result = await self.update_task_node_info(
                task_id,
                node.node_id,
                status=Status.CANCELLED.value,
                failure_reason=reason,
                extend_props_patch=patch_props,
            )
            if not result.success:
                raise TaskStateError(
                    f"manual task stop failed: task_id={task_id} node={node.node_id}"
                )

            stop_token = str(
                node.run_info.extend_props.get("relay_token") or ""
            ).strip()
            if stop_token and self._delivery_intents is not None:
                self._delivery_intents.settle_delivery_intent(
                    task_id, node.node_id, stop_token
                )
            self._graph.append_action_event(
                task_id,
                node.node_id,
                NodeAction.TRANSITION,
                {
                    "reason": reason,
                    "operator": operator,
                    "action_result": "cancelled",
                    "status_from": previous_status.value,
                    "status_to": Status.CANCELLED.value,
                },
                status_from=previous_status,
                status_to=Status.CANCELLED,
            )
            stopped_nodes.append(node.node_id)

        return TaskStopResult(
            task_id=task_id,
            status=Status.CANCELLED,
            stopped_nodes=stopped_nodes,
            reason=reason,
            operator=operator,
        )

    async def update_task_node_info(
        self,
        task_id: str,
        node_id: str,
        *,
        status: str | None = None,
        run_mode: str | None = None,
        assignee: str | None = None,
        output_patch: dict | None = None,
        acceptance_result: AcceptanceResult | None = None,
        exec_error: str | None = None,
        progress_reason: str | None = None,
        failure_reason: str | None = None,
        extend_props_patch: dict | None = None,
    ) -> NodeOpResult:
        """内部节点写口:透传 ``TaskNodePatch`` 经 ``CentralizedExecutionAdapter.on_report`` 落库(+触发翻态/验收/收敛旁路)。

        与回投同一入口(``on_report``):
        ① ``acceptance_result`` 非空 → 验收驱动(PASS→SUCCESS / FAIL+gaps→DONE);仅 PASS 进入收敛传播;
        ② ``exec_error`` 非空 → 执行报错(→ on_harness 复位重投,计数达上限 HUNG);
        ③ ``status`` 非空(无前两者)→ 框架直驱(``_DIRECT_TRANSITIONS`` 约束 + 收敛旁路,如根子节点全终态收敛);
        三者全空 → 仅 fold 非状态字段(``output_patch``/``run_mode``/``assignee``/``extend_props_patch``)。
        ``status`` 为字符串(DTO 入参),此处转 ``Status`` 枚举;非法值由枚举构造抛 ``ValueError``。
        供内部调用方/功能测试直驱节点状态,不经 BBS claim 校验(区别于 ``report_bbs_result``)。
        """
        status_enum = Status(status) if status else None
        patch = TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            status=status_enum,
            run_mode=run_mode,
            assignee=assignee,
            output_patch=output_patch,
            acceptance_result=acceptance_result,
            exec_error=exec_error,
            progress_reason=progress_reason,
            failure_reason=failure_reason,
            extend_props_patch=extend_props_patch,
        )
        return await self._centralized_adapter.on_report(patch)

