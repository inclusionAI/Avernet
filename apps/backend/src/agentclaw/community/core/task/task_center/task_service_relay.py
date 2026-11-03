"""TaskService's single distributed Relay report and candidate-search contract."""

from __future__ import annotations

import logging
import time
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    BotCandidate,
    NodeAction,
    Status,
    TaskCallbackData,
    TaskNodePatch,
    TaskReportCommand,
    TaskReportResult,
    TaskRuntimeProfile,
    task_spec_instruction,
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
            allowed_run_modes=list(TaskRuntimeProfile.run_modes_from_graph(graph)),
        )

    async def report(self, command: TaskReportCommand) -> TaskReportResult:
        """Atomically apply EXECUTION_RESULT or PLAN_RESULT to the task graph,
        then write the reported trace steps into the trajectory table.

        写时发射(2026-10-15 验收语义):relay 轨迹的**唯一事实源 = Bot 上报的
        ``trace.steps``**——上报即逐条转 ``task_trajectory_events`` 行(七字段,
        见 ``TaskReportTraceStep``),读取原样拼回;apply 记录的高层动作
        (execute/verify/plan/transition)**不落轨迹表**(留在 action_log/图内,
        是 dashboard 侧事实)。观测旁路,决策 #14 fire-and-forget:发射失败仅
        日志,绝不影响上报结果。

        幂等:发射前查图 ``_relay_report_event_ids`` ——该 event_id 已有回执 =
        重放上报,跳过发射(apply 自身对重放返回既有结果,不重记账)。预查失败
        best-effort 视为未见过(宁可多发,不可漏观测)。"""
        already_known = self._relay_report_event_known(command)
        result = self._graph.apply_relay_report(command)
        if not already_known and command.trace is not None and command.trace.steps:
            self._emit_trace_step_rows(command)
        return result

    def _relay_report_event_known(self, command: TaskReportCommand) -> bool:
        """该 ``event_id`` 是否已有 relay 回执(= 重放上报)。读图失败 → False
        (best-effort:预查不可用时宁可重发,不丢观测)。"""
        try:
            graph = self._graph.query_task_dashboard(command.task_id)
            known = set(
                (graph.extend_props.get("_relay_report_event_ids") or {}).keys()
            )
            return command.event_id in known
        except Exception:  # noqa: BLE001  观测旁路门闸,吞而不抛
            logger.warning(
                "[task][relay][trajectory] 上报预查失败,按未见过处理 "
                "task=%s event=%s",
                command.task_id,
                command.event_id,
                exc_info=True,
            )
            return False

    def _emit_trace_step_rows(self, command: TaskReportCommand) -> None:
        """trace.steps 逐条写时落表:一 step 一行,七字段与轨迹表对齐。

        ``now_ms`` 以当前时刻为基准按 step 序 +1 偏移,保证同一上报的 steps 在
        timeline 内先后有序;``action_type`` 即 Bot 侧 step 码(如 S1_CONTEXT,
        2026-10-15 起为轨迹**主事件**的 action_type,直接透传不再限定枚举)。
        ``ext_info`` 带 ``trace_step`` + ``relay_event_id`` 可追溯。"""
        if self._task_context_service is None:
            return
        base_ts = int(time.time() * 1000)
        for offset, step in enumerate(command.trace.steps, start=1):
            self._task_context_service.emit_trajectory_event(
                command.task_id,
                command.node_id,
                step.action_type,
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
