"""TDD for TaskScheduler (Phase 3, plan §3.1-§3.4).

Covers the three pure decisions (``route`` C1~C5 with attempted降权,
``select_collab`` confidence降级, ``compute_gap`` reroute/split + atomic
termination), ``start`` (PLANNED → EXECUTING + build DAG), ``tick`` (topo-unlock
+ dispatch + settle → VALIDATING + termination guards), and ``on_event``
(acceptance FAIL → gap reroute/split; NODE_FAILED → retry→reroute).

Fakes stand in for the orchestration Ports so the loop is exercised without the
real engine/BCS dispatch (Phase 4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pytest

from agentclaw.community.core.task.protocols import (
    BotCandidate,
    DispatchResult,
    RouteRecommendation,
)
from agentclaw.community.core.task.domain.events import EventKind, TaskEvent
from agentclaw.community.core.task.domain.models import (
    AttemptOutcome,
    AttemptedRecord,
    AttemptTrigger,
    CollabMode,
    Node,
    NodeStatus,
    Plan,
    RouteClass,
    RunMode,
    SubTaskSpec,
    TaskStatus,
    WatchdogAction,
)
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
)
from agentclaw.community.core.task.services import TaskScheduler, TaskService
from agentclaw.community.core.task.services.task_scheduler import (
    MAX_PROBES,
    MAX_RECOMPOSE,
    MAX_REDRIVES,
    PROBE_AFTER_TICKS,
    compute_gap,
    route,
    select_collab,
    watchdog,
)
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


# --- fakes ------------------------------------------------------------------


class FakeDiscover:
    def __init__(self, candidates: List[BotCandidate], confidence: float = 0.9,
                 route_class: RouteClass = RouteClass.C1) -> None:
        self._candidates = candidates
        self._confidence = confidence
        self._route_class = route_class

    def recommend(self, task_id: str, node_id: str) -> RouteRecommendation:
        return RouteRecommendation(
            route_class=self._route_class,
            run_mode=RunMode.SINGLE_BOT,
            candidates=list(self._candidates),
            confidence=self._confidence,
        )


class FakeDriver:
    def __init__(self) -> None:
        self.dispatched: List[str] = []
        self.redispatched: List[tuple[str, RouteClass]] = []
        self.escalated: List[str] = []

    def dispatch_node(self, task_id: str, node_id: str) -> DispatchResult:
        self.dispatched.append(node_id)
        return DispatchResult(node_id=node_id, executor_id=f"bot-{node_id}", run_mode=RunMode.SINGLE_BOT)

    def redispatch(self, task_id: str, node_id: str, route_class: RouteClass) -> DispatchResult:
        self.redispatched.append((node_id, route_class))
        return DispatchResult(node_id=node_id, executor_id="bot-r", run_mode=RunMode.SINGLE_BOT)

    def escalate_to_bbs(self, task_id: str, reason: str = "") -> DispatchResult:
        self.escalated.append(reason)
        return DispatchResult(node_id="", executor_id="", run_mode=RunMode.BBS)


class FakeDecomposer:
    def decompose(self, task_id: str) -> Plan:
        return Plan(
            sub_tasks=[
                SubTaskSpec(node_id="n1a", spec="sub a"),
                SubTaskSpec(node_id="n1b", spec="sub b"),
            ],
            confidence=0.6,
        )


def _scheduler(
    candidates=None,
    confidence=0.9,
    route_class=RouteClass.C1,
) -> tuple[TaskService, TaskScheduler, FakeDriver]:
    svc = TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())
    discover = FakeDiscover(candidates or [BotCandidate(bot_id="bot-1", fit_score=0.9)], confidence, route_class)
    driver = FakeDriver()
    sched = TaskScheduler(svc, discover, driver, FakeDecomposer())
    return svc, sched, driver


def _planned(svc: TaskService, nodes=("n1", "n2"), edges=()) -> str:
    t = svc.create(title="t")
    svc.amend(t.id, {"summary": "s"})
    subs = [SubTaskSpec(node_id=n, spec=f"do {n}") for n in nodes]
    from agentclaw.community.core.task.domain.models import EdgeSpec

    plan = Plan(sub_tasks=subs, edges=[EdgeSpec(edge_id=f"e{i}", from_node=a, to_node=b) for i, (a, b) in enumerate(edges)], confidence=0.8)
    svc.finalize_plan(t.id, plan)
    return t.id


# --- pure decisions ---------------------------------------------------------


def test_route_c1_clean_node():
    rec = RouteRecommendation(route_class=RouteClass.C1, run_mode=RunMode.SINGLE_BOT, confidence=0.9)
    assert route(rec, Node(node_id="n", spec="simple")) is RouteClass.C1


def test_route_one_failure_demotes_c1_to_c2():
    rec = RouteRecommendation(route_class=RouteClass.C1, run_mode=RunMode.SINGLE_BOT, confidence=0.9)
    node = Node(
        node_id="n",
        spec="x",
        attempted_executors=[AttemptedRecord(executor_id="b", paradigm=RunMode.SINGLE_BOT, round=1, outcome=AttemptOutcome.FAIL)],
    )
    assert route(rec, node) is RouteClass.C2


def test_route_two_failures_escalate_to_c5():
    rec = RouteRecommendation(route_class=RouteClass.C1, run_mode=RunMode.SINGLE_BOT, confidence=0.9)
    node = Node(
        node_id="n",
        spec="x",
        attempted_executors=[
            AttemptedRecord(executor_id="b1", paradigm=RunMode.SINGLE_BOT, round=1, outcome=AttemptOutcome.FAIL),
            AttemptedRecord(executor_id="b2", paradigm=RunMode.SINGLE_BOT, round=2, outcome=AttemptOutcome.FAIL),
        ],
    )
    assert route(rec, node) is RouteClass.C5


def test_route_c4_compound_low_confidence():
    rec = RouteRecommendation(route_class=RouteClass.C1, run_mode=RunMode.SINGLE_BOT, confidence=0.4)
    node = Node(node_id="n", spec="do a, b, c, d, e, f, g, h, i, j, k, l, m, n, o, p")
    assert route(rec, node) is RouteClass.C4


def test_select_collab_low_confidence_manager_worker():
    rec = RouteRecommendation(route_class=RouteClass.C3, run_mode=RunMode.COOP_GROUP, confidence=0.6)
    assert select_collab(rec) is CollabMode.MANAGER_WORKER


def test_select_collab_high_confidence_chat():
    rec = RouteRecommendation(route_class=RouteClass.C3, run_mode=RunMode.COOP_GROUP, confidence=0.9)
    assert select_collab(rec) is CollabMode.CHAT


def test_compute_gap_reroute_partial_failed():
    svc, sched, _ = _scheduler()
    tid = _planned(svc, nodes=("n1",))
    task = svc.get(tid)
    sched.start(tid)
    task = svc.get(tid)
    task.execution_graph.nodes[0].status = NodeStatus.PARTIAL_FAILED
    svc._task_repo.save(task)  # noqa: SLF001
    gap = compute_gap(task)
    assert gap["need_reroute"] is True
    assert "n1" in gap["reroute_nodes"]
    assert gap["need_split"] is False


def test_compute_gap_split_when_max_attempts_exceeded():
    svc, sched, _ = _scheduler()
    tid = _planned(svc, nodes=("n1",))
    sched.start(tid)
    task = svc.get(tid)
    node = task.execution_graph.nodes[0]
    node.status = NodeStatus.FAILED
    node.attempted_executors = [
        AttemptedRecord(executor_id="b1", paradigm=RunMode.SINGLE_BOT, round=1, outcome=AttemptOutcome.FAIL),
        AttemptedRecord(executor_id="b2", paradigm=RunMode.SINGLE_BOT, round=2, outcome=AttemptOutcome.FAIL),
    ]
    svc._task_repo.save(task)  # noqa: SLF001
    gap = compute_gap(task, recompose_count=0)
    assert gap["need_split"] is True
    assert "n1" in gap["split_nodes"]


def test_compute_gap_atomic_suppresses_split_at_ceiling():
    svc, sched, _ = _scheduler()
    tid = _planned(svc, nodes=("n1",))
    sched.start(tid)
    task = svc.get(tid)
    node = task.execution_graph.nodes[0]
    node.status = NodeStatus.FAILED
    node.attempted_executors = [
        AttemptedRecord(executor_id="b1", paradigm=RunMode.SINGLE_BOT, round=1, outcome=AttemptOutcome.FAIL),
        AttemptedRecord(executor_id="b2", paradigm=RunMode.SINGLE_BOT, round=2, outcome=AttemptOutcome.FAIL),
    ]
    svc._task_repo.save(task)  # noqa: SLF001
    gap = compute_gap(task, recompose_count=MAX_RECOMPOSE)
    assert gap["atomic"] is True
    assert gap["need_split"] is False
    assert gap["need_reroute"] is True


# --- watchdog (6.5) ---------------------------------------------------------


def _wd_node(running_ticks: int = 0, probe_count: int = 0, redrive_count: int = 0) -> Node:
    """A RUNNING node carrying watchdog state in its ``properties`` bag."""
    return Node(
        node_id="n",
        spec="hung",
        status=NodeStatus.RUNNING,
        properties={
            "retry_count": 0,
            "max_attempts": 2,
            "loop_round": 0,
            "running_ticks": running_ticks,
            "probe_count": probe_count,
            "redrive_count": redrive_count,
        },
    )


def test_watchdog_waits_within_probe_window():
    """Below PROBE_AFTER_TICKS, probes/redispatches unused → WAIT."""
    assert watchdog(_wd_node(running_ticks=0)) is WatchdogAction.WAIT
    assert watchdog(_wd_node(running_ticks=PROBE_AFTER_TICKS - 1)) is WatchdogAction.WAIT


def test_watchdog_probes_when_tick_threshold_reached():
    """running_ticks >= PROBE_AFTER_TICKS, probes remaining → PROBE."""
    assert watchdog(_wd_node(running_ticks=PROBE_AFTER_TICKS, probe_count=0)) is WatchdogAction.PROBE
    assert watchdog(_wd_node(running_ticks=PROBE_AFTER_TICKS + 3, probe_count=MAX_PROBES - 1)) is WatchdogAction.PROBE


def test_watchdog_redrives_when_probes_exhausted_redispatches_remain():
    """probe_count >= MAX_PROBES, redrive_count < MAX_REDRIVES → REDRIVE."""
    assert watchdog(_wd_node(running_ticks=0, probe_count=MAX_PROBES, redrive_count=0)) is WatchdogAction.REDRIVE
    assert watchdog(_wd_node(running_ticks=99, probe_count=MAX_PROBES, redrive_count=MAX_REDRIVES - 1)) is WatchdogAction.REDRIVE


def test_watchdog_escalates_when_probes_and_redispatches_both_exhausted():
    """probe_count >= MAX_PROBES AND redrive_count >= MAX_REDRIVES → ESCALATE."""
    assert watchdog(_wd_node(running_ticks=0, probe_count=MAX_PROBES, redrive_count=MAX_REDRIVES)) is WatchdogAction.ESCALATE
    assert watchdog(_wd_node(running_ticks=99, probe_count=MAX_PROBES, redrive_count=MAX_REDRIVES)) is WatchdogAction.ESCALATE


def test_watchdog_uses_defaults_when_properties_absent():
    """A node with no watchdog state yet (fresh dispatch) → WAIT (ticks default 0)."""
    fresh = Node(node_id="n", spec="just dispatched", status=NodeStatus.RUNNING)
    assert watchdog(fresh) is WatchdogAction.WAIT


def test_watchdog_priority_escalate_over_redrive_over_probe():
    """probe_count dominates running_ticks: a probed-out node never WAITs/PROBEs
    again — it's either REDRIVE or ESCALATE regardless of running_ticks."""
    assert watchdog(_wd_node(running_ticks=0, probe_count=MAX_PROBES, redrive_count=0)) is WatchdogAction.REDRIVE
    # Even at tick 0 (a freshly-redriven node would reset ticks), if probe_count
    # somehow stayed at MAX the rule still drives REDRIVE/ESCALATE — defensive.
    assert watchdog(_wd_node(running_ticks=0, probe_count=MAX_PROBES, redrive_count=MAX_REDRIVES)) is WatchdogAction.ESCALATE


# --- start ------------------------------------------------------------------


def test_start_advances_planned_to_executing_and_spawns_dag():
    svc, sched, _ = _scheduler()
    tid = _planned(svc, nodes=("n1", "n2"))
    task = sched.start(tid)
    assert task.status is TaskStatus.EXECUTING
    assert task.execution_graph is not None
    assert len(task.execution_graph.nodes) == 2
    # start also fires an initial tick → n1 (no preds) dispatched to RUNNING
    refreshed = svc.get(tid)
    assert refreshed.execution_graph.nodes[0].status is NodeStatus.RUNNING


def test_start_rejects_non_planned():
    svc, sched, _ = _scheduler()
    t = svc.create(title="t")
    with pytest.raises(IllegalTransitionError):
        sched.start(t.id)


# --- tick -------------------------------------------------------------------


def test_tick_topo_unlock_respects_predecessors():
    svc, sched, _ = _scheduler()
    tid = _planned(svc, nodes=("n1", "n2"), edges=(("n1", "n2"),))
    sched.start(tid)
    # after start: n1 RUNNING, n2 PENDING (locked behind n1)
    task = svc.get(tid)
    assert task.execution_graph.nodes[1].status is NodeStatus.PENDING
    # complete n1
    svc.set_node_status(task, "n1", NodeStatus.DONE)
    svc._task_repo.save(task)  # noqa: SLF001
    sched.tick(tid)
    task = svc.get(tid)
    assert task.execution_graph.nodes[1].status is NodeStatus.RUNNING


def test_tick_all_settled_advances_to_validating():
    svc, sched, _ = _scheduler()
    tid = _planned(svc, nodes=("n1",), edges=())
    sched.start(tid)
    task = svc.get(tid)
    svc.set_node_status(task, "n1", NodeStatus.DONE)
    svc._task_repo.save(task)  # noqa: SLF001
    result = sched.tick(tid)
    assert result["action"] == "advance_validating"
    assert svc.get(tid).status is TaskStatus.VALIDATING


def test_tick_noop_when_not_executing():
    svc, sched, _ = _scheduler()
    tid = _planned(svc, nodes=("n1",))
    # not started → still PLANNED
    result = sched.tick(tid)
    assert result["action"] == "noop"


# --- on_event ---------------------------------------------------------------


def test_on_event_node_failed_retries_same_executor_under_max():
    svc, sched, driver = _scheduler()
    tid = _planned(svc, nodes=("n1",))
    sched.start(tid)
    task = svc.get(tid)
    node = task.execution_graph.nodes[0]
    node.status = NodeStatus.FAILED
    node.attempted_executors = [
        AttemptedRecord(executor_id="bot-1", paradigm=RunMode.SINGLE_BOT, round=1, outcome=AttemptOutcome.FAIL, trigger=AttemptTrigger.ROUTED),
    ]
    svc._task_repo.save(task)  # noqa: SLF001
    sched.on_event(TaskEvent(task_id=tid, seq=99, kind=EventKind.NODE_FAILED, payload={"node_id": "n1"}))
    # under max_attempts → retried (RUNNING), no redispatch yet
    assert driver.redispatched == []
    assert svc.get(tid).execution_graph.nodes[0].status is NodeStatus.RUNNING


def test_on_event_node_failed_reroutes_when_max_exceeded():
    svc, sched, driver = _scheduler()
    tid = _planned(svc, nodes=("n1",))
    sched.start(tid)
    task = svc.get(tid)
    node = task.execution_graph.nodes[0]
    node.status = NodeStatus.FAILED
    node.attempted_executors = [
        AttemptedRecord(executor_id="b1", paradigm=RunMode.SINGLE_BOT, round=1, outcome=AttemptOutcome.FAIL),
        AttemptedRecord(executor_id="b2", paradigm=RunMode.SINGLE_BOT, round=2, outcome=AttemptOutcome.FAIL),
    ]
    svc._task_repo.save(task)  # noqa: SLF001
    sched.on_event(TaskEvent(task_id=tid, seq=99, kind=EventKind.NODE_FAILED, payload={"node_id": "n1"}))
    assert ("n1", RouteClass.C5) in driver.redispatched


def test_on_event_acceptance_fail_split_adds_sibling():
    svc, sched, driver = _scheduler()
    tid = _planned(svc, nodes=("n1",))
    sched.start(tid)
    task = svc.get(tid)
    node = task.execution_graph.nodes[0]
    node.status = NodeStatus.FAILED
    node.attempted_executors = [
        AttemptedRecord(executor_id="b1", paradigm=RunMode.SINGLE_BOT, round=1, outcome=AttemptOutcome.FAIL),
        AttemptedRecord(executor_id="b2", paradigm=RunMode.SINGLE_BOT, round=2, outcome=AttemptOutcome.FAIL),
    ]
    svc._task_repo.save(task)  # noqa: SLF001
    before = len(task.execution_graph.nodes)
    sched.on_event(TaskEvent(task_id=tid, seq=99, kind=EventKind.NODE_REJECTED, payload={"node_id": "n1"}))
    after = svc.get(tid)
    # decomposer produced 2 sub-tasks → 2 siblings added
    assert len(after.execution_graph.nodes) == before + 2