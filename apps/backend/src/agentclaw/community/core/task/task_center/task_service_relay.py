"""TaskService's single distributed Relay report and candidate-search contract."""

from __future__ import annotations

import logging
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    BotCandidate,
    NodeAction,
    NodeActionEvent,
    Status,
    TaskCallbackData,
    TaskNodePatch,
    TaskReportCommand,
    TaskReportResult,
    task_spec_instruction,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    TrajectoryActionType,
)
from agentclaw.community.core.task.task_context.task_trajectory.relay_action_projection import (
    relay_action_event_fields,
)
from agentclaw.community.core.task.task_runner.client.relay_prompt import (
    format_relay_execution_instruction,
)

logger = logging.getLogger("task.relay")


class TaskServiceRelayMixin:
    def _build_relay_root_execution_instruction(self, task_id: str) -> str:
        """Build the first baton for the existing requester/Bot session."""
        backend_url = str(self._api_base_url or "").strip().rstrip("/")
        if not backend_url:
            raise TaskStateError(
                "relay task creation requires task_dispatch.backend_url"
            )
        graph = self._graph.query_task_dashboard(task_id)
        root = next(
            (node for node in graph.tasks if node.node_id == task_id), None
        )
        if root is None:
            raise TaskStateError("relay root node is unavailable")
        relay_token = str(root.run_info.extend_props.get("relay_token") or "")
        if not relay_token:
            raise TaskStateError("relay root node has no run generation")
        return format_relay_execution_instruction(
            task_id=task_id,
            node_id=root.node_id,
            relay_token=relay_token,
            backend_url=backend_url,
            objective=root.task_spec.goal.objective,
            instruction=task_spec_instruction(root.task_spec),
            acceptances=[
                {"id": item.id, "description": item.description}
                for item in root.task_spec.goal.acceptances
            ],
        )

    async def report(self, command: TaskReportCommand) -> TaskReportResult:
        """Atomically apply EXECUTION_RESULT or PLAN_RESULT to the task graph,
        then mirror the recorded action events AND the reported trace steps
        into the trajectory table.

        写时发射:``apply_relay_report`` 返回本次上报记入 action_log 的动作事件,
        逐条经 ``emit_trajectory_event`` 直插 ``task_trajectory_events``;``command.
        trace.steps`` 同样逐条独立成行(七字段与轨迹表对齐,见
        ``TaskReportTraceStep``——上报即转,**不做读时投影**)。观测旁路,决策 #14
        fire-and-forget 吞异常,失败不影响上报结果;读时投影
        (``merge_relay_action_log``)按签名去重后成为存量/旁路兜底,时间线不双份。
        幂等重放(action_events 为 None)不重复发射。"""
        result, action_events, _ = self._graph.apply_relay_report(command)
        if action_events:
            self._emit_relay_report_trajectory(command, action_events)
        return result

    def _emit_relay_report_trajectory(
        self, command: TaskReportCommand, action_events: list[NodeActionEvent]
    ) -> None:
        """把上报动作事件 + trace.steps 转为 TrajectoryEvent 行发射
        (task_context_service 未接线 no-op)。"""
        if self._task_context_service is None:
            return
        base_ts = 0
        for event in action_events:
            fields = relay_action_event_fields(
                event, relay_event_id=command.event_id
            )
            node_id = fields.pop("node_id")
            base_ts = max(base_ts, event.ts)
            self._task_context_service.emit_trajectory_event(
                command.task_id,
                node_id,
                event.action.value,
                **fields,
            )
        if command.trace is not None and command.trace.steps:
            self._emit_trace_step_rows(command, base_ts)

    def _emit_trace_step_rows(
        self, command: TaskReportCommand, base_ts: int
    ) -> None:
        """trace.steps 逐条写时落表:一 step 一行,七字段与轨迹表对齐。

        ``now_ms`` 以本次上报高层动作的 ts 为基准按序 +1 偏移——高层行的 ts 被
        读时投影的签名去重钉死(不能挪),step 行排在其后保住"上报→步骤"的先后
        与 step 之间的顺序。``action_type`` 写侧先验枚举(读侧枚举强校验会整行
        丢弃),非法值跳过该条 + WARNING。"""
        steps = command.trace.steps if command.trace is not None else []
        for offset, step in enumerate(steps, start=1):
            try:
                action_type = TrajectoryActionType(step.action_type)
            except ValueError:
                logger.warning(
                    "[task][relay][trajectory] trace step 非法 action_type 跳过 "
                    "task=%s node=%s event=%s action_type=%s",
                    command.task_id, command.node_id, command.event_id,
                    step.action_type,
                )
                continue
            self._task_context_service.emit_trajectory_event(
                command.task_id,
                command.node_id,
                action_type,
                action_result=step.action_result,
                action_input=step.action_input,
                status_from=step.status_from,
                status_to=step.status_to,
                attempt=0,
                boost_reason=step.boost_reason,
                error_msg=step.error_msg,
                ext_info={
                    "trace_step": True,
                    "relay_event_id": command.event_id,
                },
                now_ms=base_ts + offset,
            )

    def _report_fact(self, report_type: str, payload: dict[str, Any]) -> Any:
        """Preserve the GraphService fact seam used by centralized BBS too."""
        return self._graph.report(TaskCallbackData(data={
            "report_type": report_type,
            "payload": payload,
        }))

    def _report_node_patch(self, patch: TaskNodePatch) -> Any:
        return self._report_fact("NODE_PATCH", {"patch": patch})

    async def search_bots(
        self, *, query: str, task_id: str, node_id: str,
    ) -> list[BotCandidate]:
        """Project the catalog result to the stable Relay BotCandidate model."""
        if not query.strip() or not task_id or not node_id:
            raise TaskStateError("relay search requires query, task_id and node_id")
        try:
            raw = await self._relay_adapter.search.search_catalog(query)
        except Exception as exc:
            self._graph.append_action_event(
                task_id,
                node_id,
                NodeAction.DISPATCH,
                {
                    "phase": "search",
                    "action_result": "failed",
                    "query": query,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise
        candidates: list[BotCandidate] = []
        for item in raw.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            bot_id = str(item.get("bot_uuid") or item.get("bot_id") or "").strip()
            if not bot_id:
                continue
            score_raw = (item.get("recommend") or {}).get("score")
            try:
                score = float(score_raw) if score_raw is not None else None
            except (TypeError, ValueError):
                score = None
            candidates.append(BotCandidate(
                bot_id=bot_id,
                name=str(item.get("bot_name") or item.get("name") or bot_id),
                description=str(item.get("bot_desc") or item.get("description") or ""),
                score=score,
            ))
        self._graph.append_action_event(
            task_id,
            node_id,
            NodeAction.DISPATCH,
            {
                "phase": "search",
                "action_result": "search",
                "query": query,
                "candidate_count": len(candidates),
                "candidates": [
                    {
                        "bot_id": candidate.bot_id,
                        "name": candidate.name,
                        "score": candidate.score,
                    }
                    for candidate in candidates
                ],
                "raw_item_count": raw.get("raw_item_count"),
                "failed_keywords": list(raw.get("failed_keywords") or []),
            },
        )
        return candidates

    def _claim_relay_bbs(
        self, task_id: str, node_id: str, bot_id: str,
        claim_id: str | None = None,
    ):
        graph = self._graph.query_task_dashboard(task_id)
        if not graph.is_relay:
            raise TaskStateError("BBS node is not in relay mode")
        node = next((item for item in graph.tasks if item.node_id == node_id), None)
        if node is None or node.run_info.run_mode != "bbs" or node.status != Status.PENDING:
            raise TaskStateError("relay BBS node is not claimable")
        return self._graph.claim_bbs_owner(
            task_id, bot_id, node_id=node_id, claim_id=claim_id,
        )
