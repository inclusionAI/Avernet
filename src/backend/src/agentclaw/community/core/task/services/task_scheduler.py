"""TaskScheduler — orchestration authority (Phase 3, plan §2.1/§3).

Drives the EXECUTING → VALIDATING loop. Holds NO state of its own: every write
flows through :class:`TaskService` (which guards + folds + appends the event
log). The Scheduler only decides *what to do next*:

- :meth:`start` (approve 委派) — PLANNED → EXECUTING + ``spawn_build_dag`` +
  ``mark_graph(ON_PLAZA)``.
- :meth:`tick` — topo-unlock PENDING nodes whose predecessors are DONE/SKIPPED,
  recommend (Discover) + dispatch (Driver) + ``set_node_status(RUNNING)``;
  when all nodes settle → advance VALIDATING + emit the goal-check trigger
  (owner-bot SKILL, stub). Termination guards: ``loop_round`` ceiling, MAX
  consecutive no-progress ticks → force VALIDATING.
- :meth:`on_event` — ACCEPTANCE_FAIL (NODE_REJECTED) → ``_compute_gap`` →
  reroute (enqueue tick) or split (``add_sibling_node``); NODE_FAILED →
  same-executor retry up to ``max_attempts`` (default 2) → reroute (C5).

The three decisions (``_route`` / ``_select_collab`` / ``_compute_gap``) are
pure rule functions (plan §3.1) — attempted降权, confidence降级, gap split with
an atomic/recompose_count termination ceiling.

Avernet rules: ``from __future__ import annotations``; ``Optional[T]``;
``@inject`` constructor injection; no ``T | None``.
"""
from __future__ import annotations

from typing import Any, Optional

from injector import inject

from agentclaw.community.core.task.protocols import (
    BotDiscoverPort,
    DecomposerPort,
    RouteRecommendation,
    TaskDriverPort,
    TaskService,
)
from agentclaw.community.core.task.domain.events import EventKind, TaskEvent
from agentclaw.community.core.task.domain.models import (
    AttemptOutcome,
    CollabMode,
    Node,
    NodeStatus,
    RouteClass,
    RunMode,
    Task,
    TaskStatus,
)
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
)
from agentclaw.community.log import get_logger

logger = get_logger()


# --- termination ceilings ---------------------------------------------------

MAX_LOOP_ROUNDS = 5
MAX_NO_PROGRESS_TICKS = 3
MAX_RECOMPOSE = 3
DEFAULT_MAX_ATTEMPTS = 2


# --- pure decisions (plan §3.1) --------------------------------------------


def _failed_attempt_count(node: Node) -> int:
    return sum(
        1
        for a in node.attempted_executors
        if a.outcome in {AttemptOutcome.FAIL, None}
    )


def route(
    recommendation: RouteRecommendation,
    node: Node,
) -> RouteClass:
    """_route (C1~C5) pure rule. Attempted降权: ≥2 failed attempts → C5 (BBS);
    ≥1 failed + rec C1 → C2 (needs clarification). C4 when the node has no
    materialized sub-tasks and the spec looks compound (heuristic)."""
    base = recommendation.route_class
    failed = _failed_attempt_count(node)
    if failed >= 2:
        return RouteClass.C5
    if failed >= 1 and base is RouteClass.C1:
        return RouteClass.C2
    # C4: confidence low AND spec is multi-clause (compound) — runtime decompose.
    spec = node.spec or ""
    if recommendation.confidence < 0.5 and len(spec) > 40 and "," in spec:
        return RouteClass.C4
    return base


def select_collab(recommendation: RouteRecommendation) -> CollabMode:
    """_select_collab pure rule. confidence < 0.7 → manager_worker (needs
    coordination); else chat. (state_machine is opt-in via a collab_mode hint
    on the recommendation, not inferred here.)"""
    if recommendation.confidence < 0.7:
        return CollabMode.MANAGER_WORKER
    return CollabMode.CHAT


def compute_gap(
    task: Task,
    recompose_count: int = 0,
) -> dict:
    """_compute_gap pure rule. Scans nodes for PARTIAL_FAILED (reroute candidate)
    and FAILED (split candidate). ``atomic`` trips when recompose_count hits the
    ceiling — no more splits, the loop must force a terminal validate.

    Returns ``{need_reroute, need_split, reroute_nodes, split_nodes, atomic}``."""
    graph = task.execution_graph
    reroute_nodes: list[str] = []
    split_nodes: list[str] = []
    if graph is not None:
        for n in graph.nodes:
            if n.status is NodeStatus.PARTIAL_FAILED:
                reroute_nodes.append(n.node_id)
            elif n.status is NodeStatus.FAILED:
                max_attempts = int(n.properties.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
                if len(n.attempted_executors) < max_attempts:
                    reroute_nodes.append(n.node_id)
                else:
                    split_nodes.append(n.node_id)
    atomic = recompose_count >= MAX_RECOMPOSE
    # atomic suppresses further splits — reroute only.
    need_split = bool(split_nodes) and not atomic
    need_reroute = bool(reroute_nodes) or (bool(split_nodes) and atomic)
    return {
        "need_reroute": need_reroute,
        "need_split": need_split,
        "reroute_nodes": reroute_nodes,
        "split_nodes": split_nodes,
        "atomic": atomic,
    }


# --- predecessors ----------------------------------------------------------


def _predecessors(task: Task, node_id: str) -> list[str]:
    if task.execution_graph is None:
        return []
    return [e.from_node for e in task.execution_graph.edges if e.to_node == node_id]


def _node_status(task: Task, node_id: str) -> Optional[NodeStatus]:
    if task.execution_graph is None:
        return None
    for n in task.execution_graph.nodes:
        if n.node_id == node_id:
            return n.status
    return None


def _is_unlocked(task: Task, node_id: str) -> bool:
    preds = _predecessors(task, node_id)
    if not preds:
        return True
    return all(
        _node_status(task, p) in {NodeStatus.DONE, NodeStatus.SKIPPED}
        for p in preds
    )


def _all_settled(task: Task) -> bool:
    if task.execution_graph is None or not task.execution_graph.nodes:
        return False
    return all(
        n.status in {NodeStatus.DONE, NodeStatus.SKIPPED}
        for n in task.execution_graph.nodes
    )


# --- scheduler -------------------------------------------------------------


class TaskScheduler:
    """Orchestration authority. All writes via TaskService."""

    @inject
    def __init__(
        self,
        task_service: TaskService,
        discover: BotDiscoverPort,
        driver: TaskDriverPort,
        decomposer: DecomposerPort,
    ) -> None:
        self._svc = task_service
        self._discover = discover
        self._driver = driver
        self._decomposer = decomposer
        self._no_progress = 0
        self._recompose_count = 0

    # --- start (approve 委派) ----------------------------------------------

    def start(self, task_id: str) -> Optional[Task]:
        task = self._svc.get(task_id)
        if task is None:
            return None
        if task.status is not TaskStatus.PLANNED:
            raise IllegalTransitionError(
                f"start requires PLANNED, task {task_id} is {task.status.value}"
            )
        # PLANNED → EXECUTING (legal edge)
        task.status = TaskStatus.EXECUTING
        if task.execution_graph is not None:
            task.execution_graph.root_phase = TaskStatus.EXECUTING
            task.execution_graph.graph_status = task.execution_graph.graph_status  # noop
        self._svc.spawn_build_dag(task)
        from agentclaw.community.core.task.domain.models import GraphStatus

        self._svc.mark_graph_status(task, GraphStatus.ON_PLAZA)
        self._svc._task_repo.save(task)  # noqa: SLF001
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

        progressed = False
        if task.execution_graph is not None:
            for n in list(task.execution_graph.nodes):
                if n.status is not NodeStatus.PENDING:
                    continue
                if not _is_unlocked(task, n.node_id):
                    continue
                recommendation = self._discover.recommend(task_id, n.node_id)
                rc = route(recommendation, n)
                dispatched = self._dispatch(task, n, recommendation, rc)
                if dispatched:
                    progressed = True

        settled = _all_settled(task)
        if settled:
            # all nodes DONE/SKIPPED → advance VALIDATING + emit goal-check trigger (stub)
            self._advance(task, TaskStatus.VALIDATING)
            self._svc._task_repo.save(task)  # noqa: SLF001
            logger.info("[Scheduler] task %s all settled → VALIDATING", task_id)
            return {"task_id": task_id, "action": "advance_validating"}

        # termination guards
        if task.loop_round >= MAX_LOOP_ROUNDS or self._no_progress >= MAX_NO_PROGRESS_TICKS:
            self._advance(task, TaskStatus.VALIDATING)
            self._svc._task_repo.save(task)  # noqa: SLF001
            logger.info(
                "[Scheduler] task %s termination guard → VALIDATING (loop=%d noprog=%d)",
                task_id,
                task.loop_round,
                self._no_progress,
            )
            return {"task_id": task_id, "action": "force_validating"}

        self._no_progress = self._no_progress + 1 if not progressed else 0
        return {"task_id": task_id, "action": "ticked", "progressed": progressed}

    def _dispatch(
        self,
        task: Task,
        node: Node,
        recommendation: RouteRecommendation,
        rc: RouteClass,
    ) -> bool:
        """Recommend → dispatch → set RUNNING. Returns True if the node moved."""
        task_id = task.id
        if rc is RouteClass.C5:
            self._driver.escalate_to_bbs(task_id, reason=f"node {node.node_id} C5")
            return False  # BBS escalation does not set RUNNING here
        # Prefer claim via a recommended candidate; else Driver dispatch (Noop in Phase 3).
        if recommendation.candidates:
            bot_id = recommendation.candidates[0].bot_id
            try:
                self._svc.claim_node(task_id, node.node_id, bot_id)
                return True
            except IllegalTransitionError:
                return False
        result = self._driver.dispatch_node(task_id, node.node_id)
        if result is None:
            return False
        try:
            self._svc.set_node_status(task, node.node_id, NodeStatus.RUNNING)
            self._svc._task_repo.save(task)  # noqa: SLF001
            return True
        except IllegalTransitionError:
            return False

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

        if kind is EventKind.NODE_REJECTED:
            return self._handle_acceptance_fail(task, payload)
        if kind is EventKind.NODE_FAILED:
            return self._handle_node_failed(task, payload)
        return task

    def _handle_acceptance_fail(self, task: Task, payload: dict) -> Task:
        gap = compute_gap(task, self._recompose_count)
        node_id = payload.get("node_id") or ""
        if gap["need_split"]:
            self._recompose_count += 1
            self._split_node(task, node_id)
            self._svc._task_repo.save(task)  # noqa: SLF001
        if gap["need_reroute"]:
            for nid in gap["reroute_nodes"] or [node_id]:
                self._driver.redispatch(task.id, nid, RouteClass.C5)
        # enqueue a tick to re-run the loop
        self.tick(task.id)
        return self._svc.get(task.id)

    def _handle_node_failed(self, task: Task, payload: dict) -> Task:
        node_id = payload.get("node_id") or ""
        node = self._svc._find_node(task, node_id)  # noqa: SLF001
        if node is None:
            return task
        max_attempts = int(node.properties.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
        if len(node.attempted_executors) < max_attempts:
            # R7: same-executor retry
            last_executor = node.attempted_executors[-1].executor_id if node.attempted_executors else ""
            if last_executor:
                try:
                    self._svc.set_node_status(task, node_id, NodeStatus.RUNNING)
                except IllegalTransitionError:
                    pass
                self._svc._task_repo.save(task)  # noqa: SLF001
                return self._svc.get(task.id)
        # exceeded retries → reroute C5
        self._driver.redispatch(task.id, node_id, RouteClass.C5)
        return self._svc.get(task.id)

    def _split_node(self, task: Task, node_id: str) -> None:
        """Runtime decompose a FAILED node into sibling sub-nodes (C4 path)."""
        from agentclaw.community.core.task.domain.models import Node as NodeModel

        plan = self._decomposer.decompose(task.id)
        for sub in plan.sub_tasks:
            self._svc.add_sibling_node(
                task,
                node_id,
                NodeModel(node_id=sub.node_id, spec=sub.spec, run_mode=sub.run_mode),
            )

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


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "MAX_LOOP_ROUNDS",
    "MAX_NO_PROGRESS_TICKS",
    "MAX_RECOMPOSE",
    "TaskScheduler",
    "compute_gap",
    "route",
    "select_collab",
]