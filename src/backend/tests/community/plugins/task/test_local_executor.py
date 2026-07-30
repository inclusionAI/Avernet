"""TDD for the 6.5.4 local in-process ExecutionPort doubles.

:class:`LocalBotExecutorPort` fakes a well-behaved bot: dispatch self-reports
``NODE_ACCEPTED`` through the public :meth:`TaskService.on_event` (instant, or
deferred via :meth:`pump`). :class:`HangingBotExecutor` fakes a hung bot that
never self-reports — the scheduler watchdog (6.5) then drives it to ESCALATE
with zero kernel change.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.protocols import (
    BotCandidate,
    DispatchResult,
    RouteRecommendation,
)
from agentclaw.community.core.task.domain.models import (
    EdgeSpec,
    NodeStatus,
    Plan,
    RouteClass,
    RunMode,
    SubTaskSpec,
    TaskStatus,
)
from agentclaw.community.core.task.services import TaskScheduler, TaskService
from agentclaw.community.core.task.services.decomposer_service import DecomposerService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.local_executor import (
    HangingBotExecutor,
    LocalBotExecutorPort,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


def _svc() -> TaskService:
    return TaskService(
        InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher()
    )


def _planned(svc: TaskService, nodes=("n1",), edges=()) -> str:
    t = svc.create(title="t")
    svc.amend(t.id, {"summary": "s"})
    plan = Plan(
        sub_tasks=[SubTaskSpec(node_id=n, spec=f"do {n}") for n in nodes],
        edges=[EdgeSpec(edge_id=f"e{i}", from_node=a, to_node=b) for i, (a, b) in enumerate(edges)],
        confidence=0.9,
    )
    svc.finalize_plan(t.id, plan)
    return t.id


class _C1Discover:
    """Always recommends a single clean candidate (C1, SINGLE_BOT)."""

    def recommend(self, task_id, node_id) -> RouteRecommendation:
        return RouteRecommendation(
            route_class=RouteClass.C1,
            run_mode=RunMode.SINGLE_BOT,
            candidates=[BotCandidate(bot_id="bot-1", fit_score=0.9)],
            confidence=0.9,
        )


class _NoopDriver:
    def dispatch_node(self, task_id, node_id) -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id="bot-d", run_mode=RunMode.SINGLE_BOT)

    def redispatch(self, task_id, node_id, route_class) -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id="bot-r", run_mode=RunMode.SINGLE_BOT)

    def escalate_to_bbs(self, task_id, reason="") -> DispatchResult:
        return DispatchResult(node_id="", executor_id="", run_mode=RunMode.BBS)


def _scheduler(svc: TaskService, exec_port) -> TaskScheduler:
    return TaskScheduler(
        svc, _C1Discover(), _NoopDriver(), DecomposerService(svc._task_repo), exec_port  # noqa: SLF001
    )


# --- LocalBotExecutorPort: instant ----------------------------------------


def test_local_instant_dispatch_self_reports_node_done_and_advances():
    """Instant: the start tick dispatches n1 AND the bot self-reports DONE → all
    settled → VALIDATING, in a single ``start`` call (full self-drive)."""
    svc = _svc()
    tid = _planned(svc)
    exec_port = LocalBotExecutorPort(svc, settle_mode="instant")
    sched = _scheduler(svc, exec_port)
    sched.start(tid)
    task = svc.get(tid)
    assert task.execution_graph.nodes[0].status is NodeStatus.DONE
    assert task.status is TaskStatus.VALIDATING
    assert exec_port.single_bots == [(tid, "n1", "bot-1")]


def test_local_instant_multi_node_closes_to_validating():
    """n1 self-reports in the start tick; n2 (locked behind n1 in the stale tick
    view) is dispatched on the next tick and self-reports → all DONE →
    VALIDATING."""
    svc = _svc()
    tid = _planned(svc, nodes=("n1", "n2"), edges=(("n1", "n2"),))
    exec_port = LocalBotExecutorPort(svc, settle_mode="instant")
    sched = _scheduler(svc, exec_port)
    sched.start(tid)
    sched.tick(tid)
    task = svc.get(tid)
    assert all(n.status is NodeStatus.DONE for n in task.execution_graph.nodes)
    assert task.status is TaskStatus.VALIDATING


def test_local_instant_dispatch_result_shape():
    svc = _svc()
    exec_port = LocalBotExecutorPort(svc, settle_mode="instant")
    r = exec_port.dispatch_single_bot("t1", "n1", "bot-a")
    assert isinstance(r, DispatchResult)
    assert r.executor_id == "bot-a"
    assert r.run_mode is RunMode.SINGLE_BOT


# --- LocalBotExecutorPort: deferred (pump) --------------------------------


def test_local_deferred_dispatch_enqueues_until_pump():
    """Deferred: dispatch enqueues the self-report; the node stays RUNNING until
    :meth:`pump` flushes it (faithful async — bot completes between ticks)."""
    svc = _svc()
    tid = _planned(svc)
    exec_port = LocalBotExecutorPort(svc, settle_mode="deferred")
    sched = _scheduler(svc, exec_port)
    sched.start(tid)
    # dispatched but NOT yet self-reported → n1 still RUNNING
    assert svc.get(tid).execution_graph.nodes[0].status is NodeStatus.RUNNING
    delivered = exec_port.pump()
    assert delivered == 1
    assert svc.get(tid).execution_graph.nodes[0].status is NodeStatus.DONE
    # next tick sees the settled node → VALIDATING
    result = sched.tick(tid)
    assert result["action"] == "advance_validating"
    assert svc.get(tid).status is TaskStatus.VALIDATING


def test_local_deferred_pump_returns_zero_when_nothing_pending():
    svc = _svc()
    exec_port = LocalBotExecutorPort(svc, settle_mode="deferred")
    assert exec_port.pump() == 0


def test_local_deferred_pump_clears_queue():
    """A second pump after flush delivers nothing (queue was cleared)."""
    svc = _svc()
    tid = _planned(svc)
    exec_port = LocalBotExecutorPort(svc, settle_mode="deferred")
    sched = _scheduler(svc, exec_port)
    sched.start(tid)
    assert exec_port.pump() == 1
    assert exec_port.pump() == 0


def test_local_instant_pump_is_noop():
    svc = _svc()
    exec_port = LocalBotExecutorPort(svc, settle_mode="instant")
    assert exec_port.pump() == 0


def test_local_invalid_settle_mode_raises():
    svc = _svc()
    with pytest.raises(ValueError):
        LocalBotExecutorPort(svc, settle_mode="bogus")


# --- HangingBotExecutor: never self-reports --------------------------------


def test_hanging_bot_drives_watchdog_to_escalate():
    """A hung bot (never self-reports) → the scheduler watchdog (6.5) drives the
    node PROBE→…→ESCALATE→``FAILED`` with zero kernel change. Mirrors the 6.5.3
    scheduler integration test but via the packaged community double."""
    from agentclaw.community.core.task.services.task_scheduler import (
        MAX_PROBES,
        MAX_REDRIVES,
    )

    svc = _svc()
    tid = _planned(svc)
    exec_port = HangingBotExecutor()
    sched = _scheduler(svc, exec_port)
    sched.start(tid)
    for _ in range(40):
        sched.tick(tid)
        if svc.get(tid).execution_graph.nodes[0].status is NodeStatus.FAILED:
            break
    node = svc.get(tid).execution_graph.nodes[0]
    assert node.status is NodeStatus.FAILED  # only reachable via ESCALATE
    assert len(exec_port.redispatches) == MAX_REDRIVES
    # 2 probes × 3 windows (2 redrives + 1 final escalate window) = 6 probes
    assert len(exec_port.probes) == MAX_PROBES * (MAX_REDRIVES + 1)
    assert exec_port.single_bots == [(tid, "n1", "bot-1")]


def test_hanging_bot_structurally_conforms_to_execution_port():
    """HangingBotExecutor is runtime-checkable against ExecutionPort (all 5
    methods present)."""
    from agentclaw.community.core.task.protocols import ExecutionPort

    assert isinstance(HangingBotExecutor(), ExecutionPort)
    assert isinstance(LocalBotExecutorPort(_svc()), ExecutionPort)
