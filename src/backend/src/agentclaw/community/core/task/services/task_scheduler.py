"""TaskScheduler — orchestration authority (Phase 3, plan §2.1/§3/§7.2).

单一链路:全生命周期动作节点图(NodeType-aware)。Drives the RUNNING → REVIEWING
loop. Holds NO state of its own: every write flows through :class:`TaskService`
(which guards + folds + appends the event log). The Scheduler only decides
*what to do next*:

- :meth:`start` (approve 委派) — DEFINED → RUNNING + ``init_execution_graph`` +
  ``mark_graph(RUNNING)`` + 首个 tick。
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
    DEFAULT_MAX_ATTEMPTS,
    SchedulerOpsMixin,
)
from agentclaw.community.core.task.domain.models import (
    Task,
    GraphStatus,
)
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
)
from agentclaw.community.log import get_logger

logger = get_logger()


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
        if task.status is not GraphStatus.DEFINED:
            raise IllegalTransitionError(
                f"start requires DEFINED, task {task_id} is {task.status.value}"
            )
        logger.info("[Scheduler] task=%s start defined→running", task_id)
        # DEFINED → RUNNING (legal edge)
        task.status = GraphStatus.RUNNING
        self._svc.init_execution_graph(task)
        task = self._svc.get(task_id)  # init 自持久化;重读续用
        self._svc.mark_graph_status(task, GraphStatus.RUNNING)
        # emit a dispatch tick
        self.tick(task_id)
        return self._svc.get(task_id)

    # --- tick --------------------------------------------------------------

    def tick(self, task_id: str) -> dict:
        task = self._svc.get(task_id)
        if task is None:
            return {"task_id": task_id, "action": "noop", "reason": "not_found"}
        if task.status not in {GraphStatus.RUNNING}:
            return {"task_id": task_id, "action": "noop", "reason": f"status={task.status.value}"}
        logger.info("[Scheduler] task=%s tick status=running", task_id)
        return self._tick(task)

    def _advance(self, task: Task, target: GraphStatus) -> None:
        from agentclaw.community.core.task.domain.state_machine import (
            require_graph_transition,
        )

        require_graph_transition(task.status, target)
        task.status = target
        if task.execution_graph is not None:
            task.execution_graph.status = target

    # --- on_event (编排 reactions, plan §3.4) ------------------------------

    def on_event(self, event: Any) -> Optional[Task]:
        """编排反应入口(design 双 on_event 的编排半)。

        retry/reroute 统一由 ``tick`` 驱动(T-13,修订 §18.1-12):NODE_FAILED 经
        ``TaskService.on_event`` 落态 fold(置 FAILED)后,这里**泵一次 tick**,让
        ``_retry_failed`` 按计数同执行方重派 / 到上限派 reroute 判定给失败方 skill。
        其他事件不在此反应 —— 其编排后果(聚合触发 / 终验 / 新节点派发等)由后续
        tick 扫图检测。tick 是唯一驱动权威,scheduler 不裸 dispatch/reroute。"""
        task_id, kind, _payload = self._unpack(event)
        if kind is EventKind.NODE_FAILED:
            self.tick(task_id)
        return self._svc.get(task_id)

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