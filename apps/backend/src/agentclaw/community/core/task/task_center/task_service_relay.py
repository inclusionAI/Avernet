"""TaskService's single distributed Relay report and candidate-search contract."""

from __future__ import annotations

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
)


class TaskServiceRelayMixin:
    async def report(self, command: TaskReportCommand) -> TaskReportResult:
        """Atomically apply EXECUTION_RESULT or PLAN_RESULT to the task graph."""
        return self._graph.apply_relay_report(command)

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
