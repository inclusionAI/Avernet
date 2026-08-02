"""TaskScheduler — orchestration authority (Phase 3, plan §2.1/§3).

Drives the EXECUTING → REVIEWING loop. Holds NO state of its own: every write
flows through :class:`TaskService` (which guards + folds + appends the event
log). The Scheduler only decides *what to do next*:

- :meth:`start` (approve 委派) — DEFINED → EXECUTING + ``spawn_build_dag`` +
  ``mark_graph(ON_PLAZA)``.
- :meth:`tick` — topo-unlock PENDING nodes whose predecessors are DONE/SKIPPED,
  recommend (Discover) + dispatch (Driver) + ``set_node_status(RUNNING)``;
  when all nodes settle → advance REVIEWING + emit the goal-check trigger
  (owner-bot SKILL, stub). Termination guards: ``loop_round`` ceiling, MAX
  consecutive no-progress ticks → force REVIEWING. Unrecoverable FAILED nodes
  → task FAILED (spec R4).
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
    ExecutionPort,
    RouteRecommendation,
    TaskDriverPort,
    TaskService,
)
from agentclaw.community.core.task.domain.events import EventKind
from agentclaw.community.core.task.services.scheduler_v2_ops import (
    SchedulerV2OpsMixin,
    is_v2_graph,
)
from agentclaw.community.core.task.domain.models import (
    AttemptOutcome,
    CollabMode,
    Node,
    NodeStatus,
    RouteClass,
    RunMode,
    Task,
    TaskStatus,
    WatchdogAction,
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

# --- watchdog ceilings (6.5) ------------------------------------------------
# tick-based 超时(无 wall clock):一个 RUNNING node 撑过 PROBE_AFTER_TICKS 个 tick
# 仍未自上报 → 探活;探活 MAX_PROBES 次仍无响应 → 重驱;重驱 MAX_REDRIVES 次仍
# hang → 升级(FAILED → reroute/split)。每次 PROBE/REDRIVE 由 scheduler 重置
# running_ticks(开新窗口);REDRIVE 还重置 probe_count。bot 会因指令遵从/LLM 服务
# 不稳定 hang 住,故须主动探活 + 重驱(plan §3)。
PROBE_AFTER_TICKS = 2
MAX_PROBES = 2
MAX_REDRIVES = 2


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
    """_compute_gap pure rule (spec R9/R10). Scans FAILED nodes and classifies
    recovery room from node properties (``attempted_executors`` length vs
    ``max_attempts``), NOT from a status-enum distinction (PARTIAL_FAILED is
    gone — acceptance-fail and execution-fail both land in FAILED).

    For each FAILED node:
    - ``attempts < max_attempts`` → reroute candidate (swap executor).
    - ``attempts >= max_attempts`` and not atomic → split candidate (recompose).
    - ``atomic and attempts >= max_attempts`` → **unrecoverable** (spec R4: (a)
      atomic termination with FAILED nodes that can't reroute/split, OR (b)
      node MAX_ATTEMPTS exhausted with no reroute/split room). The scheduler
      escalates a non-empty ``unrecoverable_failed`` set to task-level FAILED.

    ``atomic`` trips when ``recompose_count`` hits the ceiling — no more splits.
    Recovery is attempted first; FAILED only when recovery is impossible.

    Returns ``{need_reroute, need_split, reroute_nodes, split_nodes, atomic,
    unrecoverable_failed}``."""
    graph = task.execution_graph
    reroute_nodes: list[str] = []
    split_nodes: list[str] = []
    unrecoverable_failed: list[str] = []
    atomic = recompose_count >= MAX_RECOMPOSE
    if graph is not None:
        for n in graph.nodes:
            if n.status is not NodeStatus.FAILED:
                continue
            max_attempts = int(n.properties.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
            attempts = len(n.attempted_executors)
            if atomic and attempts >= max_attempts:
                unrecoverable_failed.append(n.node_id)
            elif attempts < max_attempts:
                reroute_nodes.append(n.node_id)
            else:  # not atomic and attempts >= max_attempts → split
                split_nodes.append(n.node_id)
    # atomic suppresses further splits — reroute only.
    need_split = bool(split_nodes) and not atomic
    need_reroute = bool(reroute_nodes) or (bool(split_nodes) and atomic)
    return {
        "need_reroute": need_reroute,
        "need_split": need_split,
        "reroute_nodes": reroute_nodes,
        "split_nodes": split_nodes,
        "atomic": atomic,
        "unrecoverable_failed": unrecoverable_failed,
    }


def watchdog(node: Node) -> WatchdogAction:
    """_watchdog 纯规则 (6.5, plan §3). 对一个 RUNNING node 按 tick 超时决定下一步。

    bot 经 SKILL 完成后主动回投 (NODE_ACCEPTED/NODE_FAILED);但 bot 可能因指令遵从
    或 LLM 服务不稳定 hang 住,故 scheduler 在 tick 上对长期 RUNNING 的 node 主动
    探活 + 重驱。tick-based 超时(无 wall clock),状态读自 ``node.properties``:
    ``running_ticks``(本窗口已等 tick 数)、``probe_count``(已探活次数)、
    ``redrive_count``(已重驱次数)。scheduler 负责自增/重置这些计数,本函数只决策。

    决策(优先级从高到低):
    - 探活耗尽 (``probe_count >= MAX_PROBES``) 且重驱也耗尽 (``redrive_count >=
      MAX_REDRIVES``) → ``ESCALATE``(标 FAILED → reroute/split)。
    - 探活耗尽,重驱还有余量 → ``REDRIVE``(开新一轮窗口:scheduler 重驱 + 重置)。
    - tick 超时 (``running_ticks >= PROBE_AFTER_TICKS``),探活还有余量 → ``PROBE``。
    - 否则 → ``WAIT``(仍在 bot 自上报窗口内)。
    """
    running_ticks = int(node.properties.get("running_ticks", 0))
    probe_count = int(node.properties.get("probe_count", 0))
    redrive_count = int(node.properties.get("redrive_count", 0))

    if probe_count >= MAX_PROBES:
        if redrive_count >= MAX_REDRIVES:
            return WatchdogAction.ESCALATE
        return WatchdogAction.REDRIVE
    if running_ticks >= PROBE_AFTER_TICKS:
        return WatchdogAction.PROBE
    return WatchdogAction.WAIT


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


class TaskScheduler(SchedulerV2OpsMixin):
    """Orchestration authority. All writes via TaskService.

    v2:含动作节点的图(is_v2_graph)走 ``_tick_v2``(NodeType-aware,plan §7.2);
    否则回退旧 tick,保既存 scheduler 测试不破。"""

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
        self._no_progress = 0
        self._recompose_count = 0

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
        logger.info("[Scheduler] task=%s tick status=executing", task_id)

        # v2 门控:含动作节点的图走 NodeType-aware tick(plan §7.2);否则旧链路。
        if task.execution_graph is not None and is_v2_graph(task):
            return self._tick_v2(task)

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
            # Reload so the watchdog + settle phases see RUNNING from claim_node
            # (claim_node reloads+writes internally; the in-memory task is stale).
            task = self._svc.get(task_id)
            if task is not None and task.execution_graph is not None:
                # 6.5 watchdog:探活/重驱 hung RUNNING nodes (bot may hang on
                # instruction-following / LLM-service instability). Mutates
                # node.properties + may FAIL a node; saves the task. Returns
                # whether it acted (PROBE/REDRIVE/ESCALATE) — that counts as
                # progress so the termination guard doesn't cut a hung node's
                # probe/redispatch cycle short.
                if self._watchdog(task):
                    progressed = True

        settled = _all_settled(task)
        if settled:
            # all nodes DONE/SKIPPED → advance REVIEWING + emit goal-check trigger (stub)
            self._advance(task, TaskStatus.REVIEWING)
            self._svc._task_repo.save(task)  # noqa: SLF001
            logger.info("[Scheduler] task %s all settled → REVIEWING", task_id)
            return {"task_id": task_id, "action": "advance_reviewing"}

        # R4: unrecoverable FAILED nodes → task-level FAILED. Recovery (reroute/
        # split) is attempted first via on_event's _handle_acceptance_fail /
        # _handle_node_failed; this branch trips only when recovery is impossible
        # (atomic termination OR node MAX_ATTEMPTS exhausted with no reroute/split
        # room). spec R4 (a)/(b) both surface as a non-empty unrecoverable_failed set.
        gap = compute_gap(task, self._recompose_count)
        if gap["unrecoverable_failed"]:
            self._advance(task, TaskStatus.FAILED)
            self._svc._task_repo.save(task)  # noqa: SLF001
            logger.info(
                "[Scheduler] task %s unrecoverable FAILED nodes=%s → FAILED",
                task_id, gap["unrecoverable_failed"],
            )
            return {"task_id": task_id, "action": "task_failed"}

        # termination guards
        if task.loop_round >= MAX_LOOP_ROUNDS or self._no_progress >= MAX_NO_PROGRESS_TICKS:
            self._advance(task, TaskStatus.REVIEWING)
            self._svc._task_repo.save(task)  # noqa: SLF001
            logger.info(
                "[Scheduler] task %s termination guard → REVIEWING (loop=%d noprog=%d)",
                task_id,
                task.loop_round,
                self._no_progress,
            )
            return {"task_id": task_id, "action": "force_reviewing"}

        self._no_progress = self._no_progress + 1 if not progressed else 0
        return {"task_id": task_id, "action": "ticked", "progressed": progressed}

    def _dispatch(
        self,
        task: Task,
        node: Node,
        recommendation: RouteRecommendation,
        rc: RouteClass,
    ) -> bool:
        """Recommend → dispatch → set RUNNING. Returns True if the node moved.

        6.5: for C1/C3 (has candidates) the scheduler claims the lead bot AND
        fires :class:`ExecutionPort` to actually launch it (single bot /
        coop group) — completion is async (bot self-reports via ``on_event``);
        the watchdog phase in :meth:`tick`探活/重驱 hung nodes. C5 escalates
        to BBS via the Driver; the no-candidate fallback uses the Driver (Noop).
        """
        task_id = task.id
        logger.info(
            "[Scheduler] task=%s dispatch node=%s route=%s run_mode=%s candidates=%d",
            task_id, node.node_id, rc.value,
            recommendation.run_mode.value, len(recommendation.candidates),
        )
        if rc is RouteClass.C5:
            self._driver.escalate_to_bbs(task_id, reason=f"node {node.node_id} C5")
            return False  # BBS escalation does not set RUNNING here
        # No recommended executor — Driver fallback (Noop, Phase 3).
        if not recommendation.candidates:
            result = self._driver.dispatch_node(task_id, node.node_id)
            if result is None:
                return False
            try:
                self._svc.set_node_status(task, node.node_id, NodeStatus.RUNNING)
                self._svc._task_repo.save(task)  # noqa: SLF001
                return True
            except IllegalTransitionError:
                return False
        # Has candidates → claim the lead bot, then fire ExecutionPort (real launch).
        candidates = recommendation.candidates
        lead_bot = candidates[0].bot_id
        try:
            self._svc.claim_node(task_id, node.node_id, lead_bot)
        except IllegalTransitionError:
            return False
        if recommendation.run_mode is RunMode.COOP_GROUP:
            self._execution.coop_group(
                task_id, node.node_id, [c.bot_id for c in candidates]
            )
        else:
            self._execution.dispatch_single_bot(task_id, node.node_id, lead_bot)
        return True

    def _watchdog(self, task: Task) -> bool:
        """6.5: for each RUNNING node with an assignee, advance the tick-based
        watchdog and act on the decision (:func:`watchdog`).

        - WAIT: ``running_ticks`` was incremented (give the bot more time).
        - PROBE: ping ``ExecutionPort.probe`` (ask bot to report status), bump
          ``probe_count``, reset ``running_ticks`` (fresh wait-for-response window).
        - REDRIVE: re-drive via ``ExecutionPort.redispatch_node``, bump
          ``redrive_count``, reset ``running_ticks`` + ``probe_count``.
        - ESCALATE: mark the node ``FAILED`` (the loop's termination guard then
          forces REVIEWING; reroute/split on watchdog-escalation is a follow-up).

        Returns True if any node was PROBED/REDISPATCHED/ESCALATED (acts as
        ``progressed`` so the termination guard doesn't cut a hung node's
        probe/redispatch cycle short). Mutates ``node.properties`` in-memory and
        saves the task once. Nodes without an assignee (Driver fallback) are
        skipped — the watchdog needs a bot id to probe/redispatch.
        """
        if task.execution_graph is None:
            return False
        changed = False
        acted = False
        for n in task.execution_graph.nodes:
            if n.status is not NodeStatus.RUNNING or n.assignee is None:
                continue
            n.properties["running_ticks"] = int(n.properties.get("running_ticks", 0)) + 1
            action = watchdog(n)
            bot_id = n.assignee
            logger.info(
                "[Scheduler] task=%s watchdog node=%s action=%s bot=%s ticks=%d probes=%d redrives=%d",
                task.id, n.node_id, action.value, bot_id,
                n.properties.get("running_ticks", 0),
                n.properties.get("probe_count", 0),
                n.properties.get("redrive_count", 0),
            )
            if action is WatchdogAction.PROBE:
                self._execution.probe(task.id, n.node_id, bot_id)
                n.properties["probe_count"] = int(n.properties.get("probe_count", 0)) + 1
                n.properties["running_ticks"] = 0
                changed = True
                acted = True
            elif action is WatchdogAction.REDRIVE:
                self._execution.redispatch_node(task.id, n.node_id, bot_id)
                n.properties["redrive_count"] = int(n.properties.get("redrive_count", 0)) + 1
                n.properties["running_ticks"] = 0
                n.properties["probe_count"] = 0
                changed = True
                acted = True
            elif action is WatchdogAction.ESCALATE:
                try:
                    self._svc.set_node_status(task, n.node_id, NodeStatus.FAILED)
                except IllegalTransitionError:
                    pass
                changed = True
                acted = True
            else:  # WAIT — running_ticks already incremented.
                changed = True
        if changed:
            self._svc._task_repo.save(task)  # noqa: SLF001
        return acted

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