"""End-to-end loop tests (Phase 4.9, plan §4.9) — Case A (single-bot happy) and
Case D (acceptance fail → runtime decompose/split). Real TaskService +
TaskScheduler; fake Ports stand in for engine/BCS dispatch.
"""
from __future__ import annotations

from typing import List

from agentclaw.community.core.task.protocols import BotCandidate, DispatchResult, RouteRecommendation
from agentclaw.community.core.task.domain.events import EventKind, TaskEvent
from agentclaw.community.core.task.domain.models import (
    AttemptOutcome,
    AttemptedRecord,
    NodeStatus,
    Plan,
    RouteClass,
    RunMode,
    SubTaskSpec,
    TaskStatus,
)
from agentclaw.community.core.task.domain.models import GraphStatus
from agentclaw.community.core.task.services import TaskScheduler, TaskService
from agentclaw.community.core.task.services.decomposer_service import DecomposerService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


class _Discover:
    def __init__(self, candidates: List[BotCandidate]) -> None:
        self._c = candidates

    def recommend(self, task_id: str, node_id: str) -> RouteRecommendation:
        return RouteRecommendation(
            route_class=RouteClass.C1,
            run_mode=RunMode.SINGLE_BOT,
            candidates=list(self._c),
            confidence=0.9,
        )


class _Driver:
    def dispatch_node(self, task_id, node_id) -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id=f"bot-{node_id}", run_mode=RunMode.SINGLE_BOT)

    def redispatch(self, task_id, node_id, route_class) -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id="bot-r", run_mode=RunMode.SINGLE_BOT)

    def escalate_to_bbs(self, task_id, reason="") -> DispatchResult:
        return DispatchResult(node_id="", executor_id="", run_mode=RunMode.BBS)


def _stack() -> tuple[TaskService, TaskScheduler]:
    repo = InMemoryTaskRepo()
    svc = TaskService(repo, InMemoryTaskEventRepo(), RecordingPanelPublisher(), None)
    sched = TaskScheduler(
        svc,
        _Discover([BotCandidate(bot_id="bot-1", fit_score=0.95)]),
        _Driver(),
        DecomposerService(repo),
        _NoopExecution(),
    )
    return svc, sched


class _NoopExecution:
    """6.5: ExecutionPort double for the e2e stack — records nothing, the e2e
    cases drive completion via explicit ``on_event`` 回投 (bot self-report)."""

    def dispatch_single_bot(self, task_id, node_id, bot_id):
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def coop_group(self, task_id, node_id, bot_ids):
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.COOP_GROUP)

    def redispatch_node(self, task_id, node_id, bot_id):
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def probe(self, task_id, node_id, bot_id):
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def bbs(self, task_id, node_id, reason=""):
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.BBS)


def _planned(svc: TaskService, nodes=("n1",)) -> str:
    t = svc.create(title="t")
    svc.amend(t.id, {"summary": "s"})
    svc.finalize_plan(
        t.id,
        Plan(sub_tasks=[SubTaskSpec(node_id=n, spec=f"do {n}") for n in nodes], confidence=0.9),
    )
    return t.id


# --- Case A: single-bot happy path ----------------------------------------


def test_case_a_single_bot_happy_to_delivered():
    svc, sched = _stack()
    tid = _planned(svc)
    # approve → start → initial tick dispatches n1 to RUNNING
    sched.start(tid)
    task = svc.get(tid)
    assert task.status is TaskStatus.EXECUTING
    assert task.execution_graph.nodes[0].status is NodeStatus.RUNNING

    # owner-bot 回投: node accepted
    svc.on_event(TaskEvent(task_id=tid, seq=svc._event_repo.latest_seq(tid) + 1, kind=EventKind.NODE_ACCEPTED, payload={"node_id": "n1", "verifier": "bot-1"}))  # noqa: SLF001

    # tick: all settled → VALIDATING
    result = sched.tick(tid)
    assert result["action"] == "advance_validating"
    assert svc.get(tid).status is TaskStatus.VALIDATING

    # owner-bot 终验 回投: goal verified → DELIVERED + graph VERIFIED
    svc.on_event(TaskEvent(task_id=tid, seq=svc._event_repo.latest_seq(tid) + 1, kind=EventKind.GOAL_VERIFIED, payload={"verifier": "bot-1", "verdict": "pass"}))  # noqa: SLF001
    final = svc.get(tid)
    assert final.status is TaskStatus.DELIVERED
    assert final.execution_graph.graph_status is GraphStatus.VERIFIED


# --- Case D: acceptance fail → decompose/split ----------------------------


def test_case_d_acceptance_fail_triggers_split():
    svc, sched = _stack()
    tid = _planned(svc, nodes=("n1",))
    sched.start(tid)
    # n1 fails acceptance after exhausting retries
    task = svc.get(tid)
    node = task.execution_graph.nodes[0]
    node.status = NodeStatus.FAILED
    node.attempted_executors = [
        AttemptedRecord(executor_id="bot-1", paradigm=RunMode.SINGLE_BOT, round=1, outcome=AttemptOutcome.FAIL),
        AttemptedRecord(executor_id="bot-1", paradigm=RunMode.SINGLE_BOT, round=2, outcome=AttemptOutcome.FAIL),
    ]
    svc._task_repo.save(task)  # noqa: SLF001

    before = len(svc.get(tid).execution_graph.nodes)
    sched.on_event(TaskEvent(task_id=tid, seq=svc._event_repo.latest_seq(tid) + 1, kind=EventKind.NODE_REJECTED, payload={"node_id": "n1"}))  # noqa: SLF001
    after = svc.get(tid)
    # decomposer split added sibling sub-nodes
    assert len(after.execution_graph.nodes) > before