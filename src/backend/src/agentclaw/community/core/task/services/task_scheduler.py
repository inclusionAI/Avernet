"""TaskScheduler — orchestration authority (Phase 3, plan §2.1/§3/§7.2).

单一链路:全生命周期动作节点图(NodeType-aware)。Drives the EXECUTING → REVIEWING
loop. Holds NO state of its own: every write flows through :class:`TaskService`
(which guards + folds + appends the event log). The Scheduler only decides
*what to do next*:

- :meth:`start` (approve 委派) — DEFINED → EXECUTING + ``spawn_build_dag`` +
  ``mark_graph(ON_PLAZA)`` + 首个 tick。
- :meth:`tick` — 委派 :meth:`_tick`(NodeType-aware 推进;规划链/搜推先行/
  分解/派发/exec-aggregate 触发/终验,见 ``scheduler_ops``)。
- :meth:`on_event` — ``NODE_FAILED`` → 同执行方有限次重派(T-13),超限 reroute C5。
  判定节点(EXEC_AGGREGATED/GOAL_VERIFY/NODE_HANG/BBS_CONFIRMED/HANG_CANCELLED)
  的判验结果由 skill 经 :class:`TaskService` ``on_event`` fold,不经 Scheduler。

Avernet rules: ``from __future__ import annotations``; ``Optional[T]``;
``@inject`` constructor injection; no ``T | None``。
"""
from __future__ import annotations

from typing import Any, Optional

from injector import inject

from agentclaw.community.core.task.protocols import (
    BotDiscoverPort,
    DecomposerPort,
    ExecutionPort,
    TaskDriverPort,
    TaskService,
)
from agentclaw.community.core.task.domain.events import EventKind
from agentclaw.community.core.task.services.scheduler_ops import (
    SchedulerOpsMixin,
)
from agentclaw.community.core.task.domain.models import (
    NodeStatus,
    RouteClass,
    Task,
    TaskStatus,
)
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
)
from agentclaw.community.log import get_logger

logger = get_logger()

# 同执行方重派上限(NODE_FAILED → 有限次 inline 重派,超限 reroute C5;T-13/§18.1-12)。
DEFAULT_MAX_ATTEMPTS = 2


class TaskScheduler(SchedulerOpsMixin):
    """Orchestration authority. All writes via TaskService.

    唯一 tick 链路为 ``_tick``(动作节点模型,plan §7.2);历史扁平 tick / inline
    搜推 / compute_gap 短路 / acceptance-fail split 已退场(§18 校准)。
    """

    @inject
    def __init__(
        self,
        task_service: TaskService,
        discover: BotDiscoverPort,
        driver: TaskDriverPort,
        decomposer: DecomposerPort,
        execution: ExecutionPort,
    ) -> None:
        self._svc = task_service
        self._discover = discover
        self._driver = driver
        self._decomposer = decomposer
        self._execution = execution

    # --- start (approve 委派) ----------------------------------------------

    def start(self, task_id: str) -> Optional[Task]:
        task = self._svc.get(task_id)
        if task is None:
            return None
        if task.status is not TaskStatus.DEFINED:
            raise IllegalTransitionError(
                f"start requires DEFINED, task {task_id} is {task.status.value}"
            )
        logger.info("[Scheduler] task=%s start defined→executing", task_id)
        # DEFINED → EXECUTING (legal edge)
        task.status = TaskStatus.EXECUTING
        if task.execution_graph is not None:
            task.execution_graph.root_phase = TaskStatus.EXECUTING
        self._svc.spawn_build_dag(task)
        task = self._svc.get(task_id)  # spawn 自持久化;重读续用
        from agentclaw.community.core.task.domain.models import GraphStatus

        self._svc.mark_graph_status(task, GraphStatus.ON_PLAZA)
        # emit a dispatch tick
        self.tick(task_id)
        return self._svc.get(task_id)

    # --- tick --------------------------------------------------------------

    def tick(self, task_id: str) -> dict:
        task = self._svc.get(task_id)
        if task is None:
            return {"task_id": task_id, "action": "noop", "reason": "not_found"}
        if task.status not in {TaskStatus.EXECUTING}:
            return {"task_id": task_id, "action": "noop", "reason": f"status={task.status.value}"}
        logger.info("[Scheduler] task=%s tick status=executing", task_id)
        return self._tick(task)

    def _advance(self, task: Task, target: TaskStatus) -> None:
        from agentclaw.community.core.task.domain.state_machine import (
            require_task_transition,
        )

        require_task_transition(task.status, target)
        task.status = target
        if task.execution_graph is not None:
            task.execution_graph.root_phase = target

    # --- on_event (编排 reactions, plan §3.4) ------------------------------

    def on_event(self, event: Any) -> Optional[Task]:
        task_id, kind, payload = self._unpack(event)
        task = self._svc.get(task_id)
        if task is None:
            return None
        if kind is EventKind.NODE_FAILED:
            return self._handle_node_failed(task, payload)
        return task

    def _handle_node_failed(self, task: Task, payload: dict) -> Task:
        """NODE_FAILED → 同执行方有限次重派(T-13);超限 → reroute C5。

        R7:不只 set RUNNING,须再 fire ``ExecutionPort`` 把同一执行方真正重派下去;
        完成仍由 skill on_event 异步回投。"""
        node_id = payload.get("node_id") or ""
        node = self._svc._find_node(task, node_id)  # noqa: SLF001
        if node is None:
            return task
        max_attempts = int(node.properties.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
        if len(node.attempted_executors) < max_attempts:
            last_executor = node.attempted_executors[-1].executor_id if node.attempted_executors else ""
            if last_executor:
                try:
                    self._svc.set_node_status(task, node_id, NodeStatus.RUNNING)
                except IllegalTransitionError:
                    pass
                self._execution.dispatch_single_bot(task.id, node_id, last_executor)
                self._svc._task_repo.save(task)  # noqa: SLF001
                return self._svc.get(task.id)
        # exceeded retries → reroute C5
        self._driver.redispatch(task.id, node_id, RouteClass.C5)
        return self._svc.get(task.id)

    # --- envelope ---------------------------------------------------------

    def _unpack(self, event: Any) -> tuple[str, EventKind, dict]:
        if isinstance(event, dict):
            task_id = str(event.get("task_id") or "")
            kind_raw = event.get("kind") or ""
            payload = dict(event.get("payload") or {})
        else:
            task_id = getattr(event, "task_id", "")
            kind_raw = getattr(event, "kind", "")
            payload = dict(getattr(event, "payload", {}) or {})
        try:
            kind = EventKind(str(kind_raw))
        except ValueError:
            kind = EventKind.NODE_RUNNING
        return task_id, kind, payload


__all__ = ["DEFAULT_MAX_ATTEMPTS", "TaskScheduler"]