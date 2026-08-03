"""BbsExecutor — BBS 广场 executor (Phase 5, plan §5).

The shared blackboard IS the task's :class:`TaskExecutionGraph`.广场 bots:

- **read** the blackboard via :class:`TaskService` query face (:meth:`retrieve_state`
  / ``get_task_graph``) — no special "BBS read API"; the same副屏 query face serves
  everyone.``progress_snapshot`` does not exist(§18.1-10):read 经 ``retrieve_state``。
- **write** via :meth:`TaskService.on_event` (run_mode=BBS) through the state
  group. BBS does NOT drive a Scheduler tick — it is self-drive on the广场; the
  graph ``status`` stays ``BBS_ACTIVE``. A BBS goal-FAIL verdict routes the task
  to FAILED 终态(v2 三终止 O-P2/§13:``TaskService._apply_goal_verdict`` post-BBS branch)。

This executor holds **mechanics only** (广场认领 CAS, 续做 event fold): it holds
NO task state — the event log + graph snapshot remain the single source of truth,
so the广场 self-loop invariant (no per-bot tracking) stays intact.

Avernet rules: ``from __future__ import annotations``; ``Optional[T]``; ``@inject``.
"""
from __future__ import annotations

from typing import Any, Optional

from injector import inject

from agentclaw.community.core.task.protocols import (
    BbsExecutor,
    DispatchResult,
    TaskService,
)
from agentclaw.community.core.task.domain.events import TaskEvent
from agentclaw.community.core.task.domain.models import NodeStatus, RunMode, Task
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class BbsExecutorService(BbsExecutor):
    """BBS 广场 executor. All reads/writes via TaskService; no own state."""

    @inject
    def __init__(self, task_service: TaskService) -> None:
        self._svc = task_service

    def claim(self, task_id: str, bot_id: str) -> Optional[DispatchResult]:
        """广场 CAS 认领:认领第一个 PENDING 且已解锁的节点(atomic PENDING→RUNNING).
        Returns None when no node is claimable. CAS guarantees only one bot wins."""
        task = self._svc.get(task_id)
        if task is None or task.execution_graph is None:
            return None
        for n in task.execution_graph.nodes:
            if n.status is not NodeStatus.PENDING:
                continue
            if not _is_unlocked(task, n.node_id):
                continue
            try:
                # claim_node is the CAS (PENDING→RUNNING + assignee + attempt).
                result = self._svc.claim_node(task_id, n.node_id, bot_id)
            except IllegalTransitionError:
                # raced — another bot won; try the next node
                continue
            if result is not None:
                result.run_mode = RunMode.BBS
                logger.info(
                    "[BbsExecutor] bot %s claimed node %s on广场 task %s",
                    bot_id,
                    n.node_id,
                    task_id,
                )
                return result
        return None

    def post_progress(self, event: Any) -> Optional[Task]:
        """广场续做:fold a bot-reported event via TaskService.on_event (state
        group, no Scheduler tick). BBS goal-FAIL → FAILED 终态(v2 §13,fold 内理)。"""
        # Ensure the event reads as BBS-sourced so the goal-verdict fold takes
        # the BBS branch on rejection.
        if isinstance(event, TaskEvent) and event.payload.get("run_mode") is None:
            event.payload["run_mode"] = RunMode.BBS.value
        elif isinstance(event, dict) and not event.get("run_mode"):
            event.setdefault("payload", {})
            event["payload"].setdefault("run_mode", RunMode.BBS.value)
        return self._svc.on_event(event)

    def retrieve_state(self, task_id: str, scope: Optional[str] = None) -> dict:
        """广场读黑板:delegate TaskService.retrieve_state(public + subtasks[scope])。

        ``progress_snapshot`` 不存在(§18.1-10):BBS bot 读执行上下文/中间结果/gap
        经此口(scope=node_id 读该 subtask 分区;scope=None 读 public)。"""
        return self._svc.retrieve_state(task_id, scope)


def _is_unlocked(task: Any, node_id: str) -> bool:
    """Same topo-unlock rule as the Scheduler (predecessors DONE/SKIPPED)."""
    g = task.execution_graph
    if g is None:
        return False
    preds = [e.from_node for e in g.edges if e.to_node == node_id]
    if not preds:
        return True
    status_of = {n.node_id: n.status for n in g.nodes}
    return all(
        status_of.get(p) in {NodeStatus.DONE, NodeStatus.SKIPPED} for p in preds
    )


__all__ = ["BbsExecutorService"]