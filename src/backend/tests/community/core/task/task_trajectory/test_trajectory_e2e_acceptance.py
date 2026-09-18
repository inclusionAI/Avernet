"""End-to-end acceptance for the task-trajectory feature (spec 验收端到端, P7).

The SPEC's HEADLINE ACCEPTANCE (``specs/2026-09-16-task-trajectory-collection-
and-analysis/spec.md`` §"验收(端到端)"). Exercises the REAL assembly + analysis
path built in P0-P5 against a REAL in-memory SQLite ``TaskTrajectoryRepository``
(not a fake). For each of the four spec acceptance cases it drives a task
through the REAL engine gates (SUBMIT via ``emit_submit_trajectory`` / PLAN via
``_plan_with_retry`` / DISPATCH via ``_prepare_into``+``_drain`` / EXECUTE+
VERIFY via ``on_report`` / RESET via ``on_harness``), persists every event
through the real emitter into the real repo, then reads it back with the real
assembler and runs the real ``TaskTrajectoryAnalyzer`` (``rule`` executor —
the deterministic 7-bullet ``failure_reason`` derivation, REQ-9).

The four acceptance cases (spec §验收(端到端) lines 250-255):

1. **success** — converges to ``Status.SUCCESS``; ``timeline[0].action_type
   == "submit"`` with the full submit→plan→dispatch→execute timeline present;
   ``analysis.failure_reason is None``.
2. **interface_error** (底层接口报错) — a bot callback with ``exec_error`` →
   EXECUTE event ``error_type=underlying_interface_error``; after analysis
   ``failure_reason`` starts with ``underlying_interface_error:`` + carries
   the interface error msg.
3. **timeout** (执行超时) — an SLA-timeout RESET (single-bot 600s) → RESET
   ``action_result=sla_timeout`` + ext_info ``elapsed_ms``/``sla_threshold_ms``;
   after analysis ``failure_reason`` starts with ``execution_timeout:``.
4. **hung** — a node hung (terminal ``HUNG``) → after analysis
   ``failure_reason`` starts with ``hung:``.

Plus **boost_reason** (spec §验收 line 255 / REQ-9): a DISPATCH event with a
``DispatchRationale`` in ext_info → ``analysis.boost_reason`` contains
strategy + decision_mode + assignee + candidate_count + JOIN-drop summary.

Harness: the minimal ``_TrajectoryCaseEngine`` / stubs / domain fixtures are
copied in-place from the P3 gate-test files (NOT imported / NOT modified, per
the P7 task's "don't touch the four reviewed files" guidance). The real
``TaskTrajectoryRepository`` (in-memory SQLite) replaces the per-gate
``_TrajRepo`` fake so the end-to-end persistence + assemble path is exercised.

Terminal TRANSITION gap: the engine wires trajectory emission for submit/plan/
dispatch/execute/verify/reset but NOT for ``transition`` (status-flip) today.
The analyzer's ``failure_reason`` terminal gate requires a terminal TRANSITION
event. Each case completes the timeline by emitting the terminal TRANSITION via
the REAL ``emit_trajectory_event`` helper into the SAME real repo — a faithful
"transition gate" emission (what a future engine wiring would emit) so the real
assembly + analysis path is exercised end-to-end. Tests only — NOT a prod-code
change.

Two additional P7 deliverables:

* ``TestDoAnalysisTrueServicePath`` — the REAL ``TaskTrajectoryService`` with
  ``do_analysis=True`` + a fake bot (scripted ``tc_bot`` response) — proves the
  live orchestration: assemble → ``ext_info_lookup`` → bot → backfill → return
  (REQ-8, "首期 live 链路走 tc_bot").
* ``TestCrossRestartReadability`` — drives events into the real repo, then
  SIMULATES a restart with a FRESH assembler/service against the SAME DB —
  assert the full timeline + persisted ``analysis`` survive (spec §验收 line
  251: "实例重启后轨迹/分析接口仍可读"). Proves the trajectory/analysis is
  DB-backed, not in-memory-only.

Authoritative: spec REQ-8 / REQ-9 / §验收(端到端) + 决策 #10/#13/#14.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.task.task_trajectory_repository import (
    TaskTrajectoryRepository,
)
from agentclaw.community.core.task.domain.errors import TrajectoryAnalysisError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    PlanResult,
    RuntimeInfo,
    Status,
    TaskCallbackData,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
)
from agentclaw.community.core.task.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
)
from agentclaw.community.core.task.task_trajectory.assembler import (
    TaskTrajectoryAssembler,
)
from agentclaw.community.core.task.task_trajectory.models import (
    AnalysisType,
    ReasonCatalog,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryEvent,
    TrajectoryAnalysis,
)
from agentclaw.community.core.task.task_trajectory.payloads import (
    emit_submit_trajectory,
    emit_trajectory_event,
)
from agentclaw.community.core.task.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
    _build_ext_info_lookup,
)
from agentclaw.community.di.task_trajectory_config import (
    TrajectoryAnalysisConfig,
)

# Side-effect import: registers the task ORM models on Base.metadata so
# create_all builds the trajectory tables (task_trajectory + task_trajectory_events).
import agentclaw.community.core.task.repository.models  # noqa: F401


# ---------------------------------------------------------------------------
# In-memory SQLite harness — a REAL TaskTrajectoryRepository (not a fake)
# ---------------------------------------------------------------------------


class _InMemorySqliteDB:
    """Minimal DatabasePlugin stand-in offering ``orm_session()`` — mirrors
    ``tests/community/repository/task/conftest.py``'s ``InMemorySqliteDB`` so
    the REAL ``TaskTrajectoryRepository`` (prod code) is exercised end-to-end
    against a real in-memory SQLite engine with ``create_all``-built tables."""

    def __init__(self, engine) -> None:
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _make_db():
    """Build a fresh in-memory SQLite DB with the trajectory tables created.

    Each e2e case gets its own DB (one task_id per DB, no cross-case timeline
    pollution). StaticPool keeps one connection so ``:memory:`` survives across
    sessions (the repo opens a session per call)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return _InMemorySqliteDB(eng)


# ---------------------------------------------------------------------------
# Shared helpers / fakes — copied in-place from the P3 gate-test files
# (per the P7 task's guidance to NOT touch the four reviewed gate-test files).
# These mirror the harness proven across test_trajectory_gates_*.py.
# ---------------------------------------------------------------------------


def _run(coro):
    """Sync wrapper to drive async engine / service methods in unit tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _task_info(task_id: str = "t1", max_depth: int = 3, extra_cfg: dict | None = None) -> TaskInfo:
    cfg = {"MAX_DEPTH": max_depth, "BBS_MAX_DEPTH": 3, "task_type": "dynamic"}
    if extra_cfg:
        cfg.update(extra_cfg)
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(
                objective="存储架构分析",
                acceptances=[AcceptanceCriteria(id="ac1", description="d")],
            ),
        ),
        source_type="bot",
        owner_bot_id="owner:1",
        execution_config=cfg,
    )


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec,
        run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


def _accept(verdict: AcceptanceVerdict = AcceptanceVerdict.DONE,
            gaps: list[str] | None = None) -> AcceptanceResult:
    return AcceptanceResult(verdict=verdict, acceptances_metric=[], gaps=gaps or [])


def _data(loop_task_id: str = "t1::c1", *,
          success: object = True,
          exec_error: str | None = None,
          ext_info: dict | None = None) -> TaskCallbackData:
    """Build a poller-shape ``TaskCallbackData`` for driving ``CallbackAdapter.adapt``."""
    result: dict = {}
    if success is not True or exec_error is not None:
        result["success"] = success
    if exec_error is not None:
        result["exec_error"] = exec_error
    if ext_info is not None:
        result["_ext_info"] = ext_info
    return TaskCallbackData(data={
        "loop_task_id": loop_task_id,
        "workflow_type": "single_bot",
        "workflow_id": 1,
        "instance_id": 10,
        "result": result,
    })


class _StubPlanner:
    """Single-attempt planner — returns one ``PlanResult`` per call (``has_gap=False``
    so the retry loop converges on attempt 0, firing exactly one plan row)."""

    def __init__(self, children: list[TaskNode] | None = None) -> None:
        self._children = children or []

    async def plan(self, graph, target_node_id=None):
        return PlanResult(children=self._children, has_gap=False, gap_detail="done")


class _RationaleStubDispatcher:
    """DISPATCH-gate stub that mirrors ``TaskDispatcher``'s ``_dispatch_rationale``
    carrier write. Writes a rationale dict + sets run_mode/assignee for HIT_SINGLE
    (the trajectory ``dispatch`` event then reads the carrier)."""

    def __init__(self, *, outcome: str = "hit_single", bot_id: str = "bot1",
                 rationale: dict | None = None) -> None:
        self._outcome = outcome
        self._bot_id = bot_id
        self._rationale = rationale

    async def dispatch(self, nodes: list[TaskNode]) -> list[TaskNode]:
        for n in nodes:
            if self._rationale is not None:
                n.run_info.extend_props["_dispatch_rationale"] = dict(self._rationale)
            if self._outcome == "hit_single":
                n.run_info.run_mode = "single_bot"
                n.run_info.assignee = self._bot_id
        return nodes


class _StubRunner:
    """Mirrors the existing ``StubRunner`` across the gate tests: ``_drain``
    calls ``start_run(toDoTaskList) -> list[bool]`` + ``form_coop_group``."""

    def __init__(self) -> None:
        self.run_calls: list[list[TaskNode]] = []

    async def start_run(self, toDoTaskList):
        self.run_calls.append(list(toDoTaskList))
        return [True] * len(toDoTaskList)

    async def form_coop_group(self, gf):
        return "grp_stub"


class _TrajectoryCaseEngine(ExecutionEngine):
    """Engine test subclass — injects stubs + a trajectory repo (mirrors the
    four P3-1..P3-5 gate-test subclasses). Used for the PLAN/DISPATCH/
    EXECUTE/VERIFY/RESET drives."""

    def __init__(self, graph, planner=None, dispatcher=None, runner=None,
                 trajectory_repo=None) -> None:
        self._case_planner = planner
        self._case_dispatcher = dispatcher
        self._case_runner = runner
        super().__init__(graph, trajectory_repo=trajectory_repo)

    def _build_planner(self):
        return self._case_planner if self._case_planner is not None else super()._build_planner()

    def _build_dispatcher(self):
        return self._case_dispatcher if self._case_dispatcher is not None else super()._build_dispatcher()

    def _build_runner(self):
        return self._case_runner if self._case_runner is not None else super()._build_runner()


def _set_running_node(svc, task_id, node_id, *, run_mode: str = "single_bot",
                     assignee: str = "bot1", start_time=None,
                     harness_retries: int = 0,
                     request_input: str | None = None) -> None:
    """Preset a child node to RUNNING (mirrors the gate-test helper). Idempotent
    on the node existence: if the child already exists (e.g., DISPATCH already
    added+flipped it to RUNNING), skip the add and only apply the update patch
    (so the e2e can drive EXECUTE on an already-dispatched node without a
    re-add conflict)."""
    graph = svc._graphs.get(task_id)
    existing = None
    if graph is not None:
        try:
            existing = svc._get_node(graph, node_id)
        except Exception:  # noqa: BLE001  node not in graph yet
            existing = None
    if existing is None:
        svc.add_task_nodes([_child(node_id, task_id)], parent_node_id=task_id)
    ep: dict = {"harness_retries": harness_retries}
    if request_input is not None:
        ep["_exec_request_input"] = request_input
    svc.update_task_node_info(
        _patch(task_id, node_id, status=Status.RUNNING, run_mode=run_mode,
               assignee=assignee, start_time=start_time, extend_props_patch=ep)
    )


# A sample DispatchRationale the real DISPATCH gate writes into ext_info. The
# analyzer's boost_reason derivation reads this from the event's ext_info via
# the ``ext_info_lookup`` closure the service builds. Carries strategy +
# decision_mode + 3 candidates + 1 JOIN-drop (covers the spec's "策略+决策模式+
# 选中+候选数+JOIN 丢因" acceptance).
_SAMPLE_RATIONALE: dict = {
    "strategy_name": "search",
    "decision_mode": "skill",
    "candidates": [
        {"bot_id": "bot1", "recommend_score": 0.9, "short_profile": "owns skill"},
        {"bot_id": "bot2", "recommend_score": 0.7, "short_profile": "backup"},
        {"bot_id": "bot3", "recommend_score": 0.5, "short_profile": "rookie"},
    ],
    "prefetch_tokens": ["存储", "分析"],
    "join_filter_applied": True,
    "join_dropped": [{"bot_id": "bot9", "reason": "claim_mode_off"}],
    "skill_prompt_digest": "a" * 64,
    "skill_response_digest": "b" * 64,
}


def _drive_submit(repo, graph_svc, *, task_id: str) -> TaskGraphService:
    """SUBMIT phase: emit the REAL SUBMIT gate row via the REAL
    ``emit_submit_trajectory`` helper (the same helper ``TaskService.execute``
    calls, REQ-6) into ``repo`` + initialize the graph (``task_type="dynamic"``).

    Driven directly (not via ``TaskService.execute``) because: (1) the DYNAMIC
    branch schedules a background ``on_execute`` that would pollute the repo
    with spurious events; (2) the WORKFLOW branch marks the task
    "external-managed" so ``_prepare_into`` would SKIP dynamic dispatch. The
    SUBMIT helper IS the real SUBMIT gate's emission path (production code);
    only ``TaskService.execute``'s task_info persist + branch dispatch is
    skipped (immaterial — the SUBMIT row is what the spec exercises)."""
    task_info = _task_info(task_id)
    graph_svc.initialize_graph(task_info)
    emit_submit_trajectory(
        repo, task_id, task_info,
        submitted_at_ms=int(time.time() * 1000),
    )
    return graph_svc


def _drive_plan(repo, graph_svc, *, task_id: str, child_node_id: str) -> None:
    """PLAN phase: drive ``_plan_with_retry`` (real PLAN gate) so one ``plan``
    row fires into ``repo``. The stub planner returns an empty-children
    PlanResult (DISPATCH seeds the child itself; PLAN only fires the row)."""
    graph = graph_svc._graphs[task_id]
    eng = _TrajectoryCaseEngine(
        graph_svc, planner=_StubPlanner(), trajectory_repo=repo,
    )
    pr = _run(eng._plan_with_retry(task_id, graph))
    assert pr is not None, "PLAN phase returned None"


def _drive_dispatch(repo, graph_svc, *, task_id: str, child_node_id: str,
                    rationale: dict | None = None) -> None:
    """DISPATCH phase: seed a PENDING child, ``_prepare_into`` + ``_drain`` so
    the REAL DISPATCH gate fires one ``dispatch`` row into ``repo`` carrying
    the rationale in ext_info; the child flips to RUNNING (assignee set)."""
    graph_svc.add_task_nodes([_child(child_node_id, task_id)], parent_node_id=task_id)
    eng = _TrajectoryCaseEngine(
        graph_svc,
        planner=_StubPlanner(),
        dispatcher=_RationaleStubDispatcher(
            outcome="hit_single", bot_id="bot1", rationale=rationale,
        ),
        runner=_StubRunner(),
        trajectory_repo=repo,
    )
    side: list[tuple] = []
    _run(eng._prepare_into(task_id, side))
    _run(eng._drain(task_id, side))
    assert graph_svc._get_node(graph_svc._graphs[task_id], child_node_id).status == Status.RUNNING


def _drive_execute_success(repo, graph_svc, *, task_id: str,
                           child_node_id: str) -> None:
    """EXECUTE+VERIFY (success): ``on_report`` on the child with acceptance
    DONE → real gates fire ``execute(ok)`` + ``verify(accept_pass)``."""
    _set_running_node(graph_svc, task_id, child_node_id, request_input="bot-request-payload")
    eng = _TrajectoryCaseEngine(
        graph_svc, planner=_StubPlanner(),
        dispatcher=_RationaleStubDispatcher(outcome="hit_single", bot_id="bot1"),
        runner=_StubRunner(), trajectory_repo=repo,
    )
    patch = _patch(task_id, child_node_id, status=Status.DONE,
                   acceptance_result=_accept(AcceptanceVerdict.DONE))
    _run(eng.on_report(patch))


def _drive_execute_interface_error(repo, graph_svc, *, task_id: str,
                                   child_node_id: str,
                                   exec_error: str = "底层接口 boom") -> None:
    """EXECUTE(err) (interface_error): ``on_report`` via the REAL
    ``CallbackAdapter`` with a non-empty ``exec_error`` → real EXECUTE gate
    fires ``execute(failed)`` with ``error_type=underlying_interface_error``.
    Harness retries=99 (>= MAX_HARNESS) so the gate escalates to HUNG (no
    re-dispatch loop); the terminal TRANSITION is emitted separately."""
    _set_running_node(graph_svc, task_id, child_node_id, harness_retries=99,
                     request_input="bot-request-payload")
    eng = _TrajectoryCaseEngine(
        graph_svc, planner=_StubPlanner(),
        dispatcher=_RationaleStubDispatcher(outcome="hit_single", bot_id="bot1"),
        runner=_StubRunner(), trajectory_repo=repo,
    )
    patch = CallbackAdapter().adapt(
        _data(loop_task_id=f"{task_id}::{child_node_id}", success=True, exec_error=exec_error,
              ext_info={"interface_error_code": 42})
    )
    _run(eng.on_report(patch))


def _drive_reset_sla_timeout(repo, graph_svc, *, task_id: str,
                            child_node_id: str) -> int:
    """RESET (timeout): ``on_harness`` with an SLA-timeout patch (no
    ``exec_error`` → ``external_harness`` → ``action_result=sla_timeout``) →
    real RESET gate fires one ``reset`` row with ``ext_info.{trigger,
    elapsed_ms, sla_threshold_ms, attempts_seen}``. Returns the start_time."""
    t0 = int(time.time() * 1000)
    start_time = t0 - 10_000  # 10s ago
    _set_running_node(graph_svc, task_id, child_node_id, run_mode="single_bot",
                     start_time=start_time, harness_retries=0, assignee="bot1")
    eng = _TrajectoryCaseEngine(
        graph_svc, planner=_StubPlanner(),
        dispatcher=_RationaleStubDispatcher(outcome="hit_single", bot_id="bot1"),
        runner=_StubRunner(), trajectory_repo=repo,
    )
    _run(eng.on_harness(_patch(task_id, child_node_id, status=Status.PENDING,
                               extend_props_patch={"harness_reset": "timeout"})))
    return start_time


def _drive_reset_harness_max(repo, graph_svc, *, task_id: str,
                            child_node_id: str) -> None:
    """RESET (hung): ``on_harness`` with ``exec_error`` + retries >=
    ``MAX_HARNESS`` → real RESET gate fires a ``reset`` row (non-SLA
    ``action_result``) and escalates the node to ``HUNG``. The hung case's
    ``failure_reason`` is derived from the TERMINAL TRANSITION to HUNG
    (carrying ``error_type=HUNG``), emitted separately via the real emitter."""
    start_time = int(time.time() * 1000) - 4_000
    _set_running_node(graph_svc, task_id, child_node_id, run_mode="single_bot",
                     start_time=start_time, harness_retries=3, assignee="bot1")
    eng = _TrajectoryCaseEngine(
        graph_svc, planner=_StubPlanner(),
        dispatcher=_RationaleStubDispatcher(outcome="hit_single", bot_id="bot1"),
        runner=_StubRunner(), trajectory_repo=repo,
    )

    async def _go():
        await eng.on_harness(_patch(task_id, child_node_id, exec_error="exec_failed_retry"))
        for bg in list(eng._bg_tasks):  # drain any HUNG-escalation bg fallout
            try:
                await (asyncio.wrap_future(bg) if isinstance(bg, asyncio.Future) else bg)
            except Exception:  # noqa: BLE001  swallow bg fallout in test
                pass

    _run(_go())


def _emit_terminal_transition(repo, *, task_id: str, node_id: str,
                             status_to: Status, error_type: ReasonCatalog | None = None,
                             error_msg: str | None = None) -> None:
    """Emit a terminal TRANSITION via the REAL ``emit_trajectory_event`` helper
    into ``repo`` (the engine wires submit/plan/dispatch/execute/verify/reset
    but NOT ``transition`` today; this completes the timeline the real
    analyzer's terminal gate reads — same helper the gates use, same repo)."""
    emit_trajectory_event(
        repo,
        task_id,
        node_id,
        TrajectoryActionType.TRANSITION,
        action_result=status_to.value.lower(),  # SUCCESS→"success", FAILED→"failed", HUNG→"hung"
        action_input=None,
        error_type=error_type,
        error_msg=error_msg,
        ext_info=None,
        status_from=None,
        status_to=status_to,
        attempt=0,
    )


def _assemble_and_analyze(repo, *, task_id: str) -> tuple[TaskTrajectory, TrajectoryAnalysis]:
    """The REAL read + analysis path: real assembler → real ``ext_info_lookup``
    closure (the service's seam) → real analyzer ``rule`` executor (REQ-9).
    Returns ``(trajectory, analysis)`` for the caller's assertions."""
    assembler = TaskTrajectoryAssembler(repo)
    trajectory = assembler.assemble(task_id)
    ext_info_lookup = _build_ext_info_lookup(repo, task_id)
    analyzer = TaskTrajectoryAnalyzer()  # rule executor needs no bot
    analysis = _run(analyzer.analyze(
        trajectory, ext_info_lookup,
        analysis_type=AnalysisType.RULE, analysis_executor="rule_engine",
    ))
    return trajectory, analysis


def _timeline_action_types(trajectory: TaskTrajectory) -> list[str]:
    """Convenience: the timeline's action_type values in order (lowercase strings)."""
    return [
        ev.action_type.value if hasattr(ev.action_type, "value") else str(ev.action_type)
        for ev in trajectory.timeline
    ]


# ---------------------------------------------------------------------------
# A — e2e four failure-types via the real assembly + analysis path
# (spec §验收(端到端) lines 250-255)
# ---------------------------------------------------------------------------


class TestE2EFourFailureTypesAcceptance:
    """Spec §验收(端到端): for each of the four acceptance cases, drive a task
    to terminal via the REAL engine gates into a REAL in-memory SQLite
    ``TaskTrajectoryRepository``, assemble with the REAL assembler, and run
    the REAL analyzer's ``rule`` executor — assert the timeline shape + the
    spec's ``failure_reason`` / ``boost_reason`` derivation.

    The real emission gates fire submit/plan/dispatch/execute/verify/reset
    into the real repo (SQL INSERTs). The terminal TRANSITION is emitted via
    the real ``emit_trajectory_event`` helper (the one gate the engine
    doesn't wire today — see module docstring). The real
    ``_build_ext_info_lookup`` closure re-queries the repo so the analyzer
    reads the persisted ``ext_info`` JSON (the assembler dropped it)."""

    def test_success_task_full_timeline_and_failure_reason_none(self):
        """Case 1 (success): submit→plan→dispatch→execute(→verify) timeline,
        ``timeline[0].action_type == "submit"``, terminal TRANSITION to
        SUCCESS → ``failure_reason is None``; boost_reason carries the
        DISPATCH rationale summary."""
        db = _make_db()
        repo = TaskTrajectoryRepository(db)
        task_id, child = "e2e-success", "c1"
        graph_svc = TaskGraphService()

        _drive_submit(repo, graph_svc, task_id=task_id)
        _drive_plan(repo, graph_svc, task_id=task_id, child_node_id=child)
        _drive_dispatch(repo, graph_svc, task_id=task_id, child_node_id=child,
                        rationale=_SAMPLE_RATIONALE)
        _drive_execute_success(repo, graph_svc, task_id=task_id, child_node_id=child)
        _emit_terminal_transition(repo, task_id=task_id, node_id=child,
                                 status_to=Status.SUCCESS)

        trajectory, analysis = _assemble_and_analyze(repo, task_id=task_id)

        # timeline shape: submit first, full submit→plan→dispatch→execute(+verify) present
        actions = _timeline_action_types(trajectory)
        assert actions, "timeline empty"
        assert actions[0] == "submit", f"timeline[0] must be submit; got {actions}"
        for required in ("submit", "plan", "dispatch", "execute"):
            assert required in actions, f"missing {required} in timeline: {actions}"
        # EXECUTE success → VERIFY row present (accept_pass)
        assert "verify" in actions, f"success EXECUTE should be followed by VERIFY: {actions}"
        # terminal TRANSITION last
        assert actions[-1] == "transition", f"terminal transition must be last: {actions}"
        assert trajectory.timeline[-1].status_to == Status.SUCCESS

        # success task → failure_reason=None (spec line 256 / REQ-9)
        assert analysis.failure_reason is None, (
            f"success task failure_reason must be None; got {analysis.failure_reason!r}"
        )
        assert analysis.analysis_type == AnalysisType.RULE
        assert analysis.analysis_executor == "rule_engine"

        # boost_reason from the DISPATCH event's ext_info rationale
        assert analysis.boost_reason is not None
        assert "策略=search" in analysis.boost_reason
        assert "模式=skill" in analysis.boost_reason
        assert "选中=bot1(hit_single)" in analysis.boost_reason
        assert "候选3" in analysis.boost_reason
        assert "JOIN 丢=1个(claim_mode_off)" in analysis.boost_reason

    def test_interface_error_failure_reason_starts_with_underlying_interface_error(self):
        """Case 2 (底层接口报错): a bot callback with a non-empty ``exec_error``
        → EXECUTE event ``error_type=underlying_interface_error``; after
        analysis ``failure_reason`` starts with ``underlying_interface_error:``
        and carries the interface error msg (spec line 253)."""
        db = _make_db()
        repo = TaskTrajectoryRepository(db)
        task_id, child = "e2e-iface", "c2"
        graph_svc = TaskGraphService()
        iface_msg = "底层接口 connection refused by upstream"

        _drive_submit(repo, graph_svc, task_id=task_id)
        _drive_plan(repo, graph_svc, task_id=task_id, child_node_id=child)
        _drive_dispatch(repo, graph_svc, task_id=task_id, child_node_id=child,
                        rationale=_SAMPLE_RATIONALE)
        _drive_execute_interface_error(repo, graph_svc, task_id=task_id,
                                      child_node_id=child, exec_error=iface_msg)
        # terminal TRANSITION to FAILED (the interface error failed the task)
        _emit_terminal_transition(repo, task_id=task_id, node_id=child,
                                 status_to=Status.FAILED)

        trajectory, analysis = _assemble_and_analyze(repo, task_id=task_id)

        # the EXECUTE row carries the surfaced origin
        execute_rows = [ev for ev in trajectory.timeline if ev.action_type == TrajectoryActionType.EXECUTE]
        assert execute_rows, f"no execute row in timeline: {_timeline_action_types(trajectory)}"
        execute_err = [ev for ev in execute_rows if ev.action_result == "failed"]
        assert execute_err, (
            f"no execute(failed) row; got action_results={[e.action_result for e in execute_rows]}"
        )
        assert execute_err[0].error_type == ReasonCatalog.UNDERLYING_INTERFACE_ERROR
        assert iface_msg in (execute_err[0].error_msg or "")

        # analyzer: failure_reason starts with underlying_interface_error: + carries the msg
        assert analysis.failure_reason is not None
        assert analysis.failure_reason.startswith("underlying_interface_error:"), (
            f"failure_reason must start with 'underlying_interface_error:'; "
            f"got {analysis.failure_reason!r}"
        )
        assert iface_msg in analysis.failure_reason, (
            f"failure_reason must carry the interface error msg; got {analysis.failure_reason!r}"
        )
        # boost_reason still derived from the DISPATCH event (independent of the failure)
        assert analysis.boost_reason is not None
        assert "策略=search" in analysis.boost_reason

    def test_timeout_failure_reason_starts_with_execution_timeout(self):
        """Case 3 (执行超时): an SLA-timeout RESET (single-bot 600s) → RESET
        event ``action_result=sla_timeout`` + ext_info ``elapsed_ms``/
        ``sla_threshold_ms``; after analysis ``failure_reason`` starts with
        ``execution_timeout:`` with elapsed/threshold (spec line 254)."""
        db = _make_db()
        repo = TaskTrajectoryRepository(db)
        task_id, child = "e2e-timeout", "c3"
        graph_svc = TaskGraphService()

        _drive_submit(repo, graph_svc, task_id=task_id)
        _drive_plan(repo, graph_svc, task_id=task_id, child_node_id=child)
        _drive_dispatch(repo, graph_svc, task_id=task_id, child_node_id=child,
                        rationale=_SAMPLE_RATIONALE)
        start_time = _drive_reset_sla_timeout(repo, graph_svc, task_id=task_id,
                                              child_node_id=child)
        # terminal TRANSITION to FAILED (SLA timeout → re-dispatched, eventually FAILED)
        _emit_terminal_transition(repo, task_id=task_id, node_id=child,
                                 status_to=Status.FAILED)

        trajectory, analysis = _assemble_and_analyze(repo, task_id=task_id)

        # the RESET row carries sla_timeout + ext_info elapsed/threshold
        reset_rows = [ev for ev in trajectory.timeline if ev.action_type == TrajectoryActionType.RESET]
        assert reset_rows, f"no reset row in timeline: {_timeline_action_types(trajectory)}"
        rec = reset_rows[0]
        assert rec.action_result == "sla_timeout"
        assert rec.status_from == Status.RUNNING
        assert rec.status_to == Status.PENDING

        # analyzer: failure_reason starts with execution_timeout: + elapsed/threshold
        assert analysis.failure_reason is not None
        assert analysis.failure_reason.startswith("execution_timeout:"), (
            f"failure_reason must start with 'execution_timeout:'; "
            f"got {analysis.failure_reason!r}"
        )
        # the single-bot SLA threshold (600s = 600_000ms) is in the failure_reason
        assert "600000" in analysis.failure_reason, (
            f"failure_reason must carry the 600s SLA threshold; got {analysis.failure_reason!r}"
        )
        assert "阈值" in analysis.failure_reason  # the spec's Chinese template
        # boost_reason still derived from the DISPATCH event
        assert analysis.boost_reason is not None
        assert "策略=search" in analysis.boost_reason

    def test_hung_failure_reason_starts_with_hung(self):
        """Case 4 (hung): a node hung (terminal ``HUNG``) → the trajectory
        shows the hung transition/RESET; after analysis ``failure_reason``
        starts with ``hung:`` (spec §验收 — the four terminal failure modes)."""
        db = _make_db()
        repo = TaskTrajectoryRepository(db)
        task_id, child = "e2e-hung", "c4"
        graph_svc = TaskGraphService()

        _drive_submit(repo, graph_svc, task_id=task_id)
        _drive_plan(repo, graph_svc, task_id=task_id, child_node_id=child)
        _drive_dispatch(repo, graph_svc, task_id=task_id, child_node_id=child,
                        rationale=_SAMPLE_RATIONALE)
        _drive_reset_harness_max(repo, graph_svc, task_id=task_id, child_node_id=child)
        # terminal TRANSITION to HUNG carrying error_type=HUNG + the hung_reason
        hung_reason = "stuck: retries exhausted (harness_max)"
        _emit_terminal_transition(repo, task_id=task_id, node_id=child,
                                 status_to=Status.HUNG,
                                 error_type=ReasonCatalog.HUNG,
                                 error_msg=hung_reason)

        trajectory, analysis = _assemble_and_analyze(repo, task_id=task_id)

        # the RESET row + the terminal TRANSITION to HUNG both present
        actions = _timeline_action_types(trajectory)
        assert "reset" in actions, f"reset row missing: {actions}"
        assert actions[-1] == "transition"
        assert trajectory.timeline[-1].status_to == Status.HUNG

        # analyzer: failure_reason starts with hung: + the hung_reason
        assert analysis.failure_reason is not None
        assert analysis.failure_reason.startswith("hung:"), (
            f"failure_reason must start with 'hung:'; got {analysis.failure_reason!r}"
        )
        assert hung_reason in analysis.failure_reason, (
            f"failure_reason must carry the hung_reason; got {analysis.failure_reason!r}"
        )
        # boost_reason still derived from the DISPATCH event
        assert analysis.boost_reason is not None
        assert "策略=search" in analysis.boost_reason

    def test_submit_is_timeline_first_across_all_cases(self):
        """Spec §验收 line 250 + REQ-6: ``timeline[0].action_type == "submit"``
        for every task — re-pinned across the four cases' timelines so a
        future regression that reorders the SUBMIT gate surfaces here too."""
        for case in ("success", "interface_error", "timeout", "hung"):
            db = _make_db()
            repo = TaskTrajectoryRepository(db)
            task_id, child = f"e2e-first-{case}", "c0"
            graph_svc = TaskGraphService()
            _drive_submit(repo, graph_svc, task_id=task_id)
            trajectory = TaskTrajectoryAssembler(repo).assemble(task_id)
            actions = _timeline_action_types(trajectory)
            assert actions, f"case={case} timeline empty"
            assert actions[0] == "submit", (
                f"case={case} timeline[0] must be submit; got {actions}"
            )


# ---------------------------------------------------------------------------
# do_analysis=True service path — REAL service + fake bot (live tc_bot)
# (spec §验收 line 252 / REQ-8 / 决策 #10)
# ---------------------------------------------------------------------------


class _FakeBot:
    """Fake ``OpenApiBotPort``-seam bot for the `` tc_bot`` executor. Records
    the call; returns a scripted JSON response carrying ``analysis_output``
    (required) + optional ``boost_reason`` / ``failure_reason`` (the contract
    the analyzer's ``_parse_bot_response`` enforces)."""

    def __init__(self, *, content: str) -> None:
        self._content = content
        self.last_call: dict | None = None

    async def send_and_wait_async(self, *, bot_id, message, metadata=None,
                                 timeout=180.0, poll_interval=2.0) -> dict:
        self.last_call = {
            "bot_id": bot_id, "message": message, "metadata": metadata, "timeout": timeout,
        }
        return {"result": {"content": self._content}}


class TestDoAnalysisTrueServicePath:
    """The REAL ``TaskTrajectoryService`` with ``do_analysis=True`` + a fake
    bot (scripted ``tc_bot`` response) — proves the live orchestration the
    spec's "首期 live 链路走 tc_bot" wires: assemble → ``ext_info_lookup`` →
    bot → backfill → return carrying the fresh analysis. The fake bot returns
    a scripted ``TrajectoryAnalysis`` JSON whose ``failure_reason`` shape
    mirrors what the real ``rule`` executor would derive (the P7 task's named
    seam: "the real service with a stubbed bot — the bot returns a scripted
    ``TrajectoryAnalysis`` whose ``failure_reason``/``boost_reason`` the test
    asserts")."""

    def test_do_analysis_true_assembles_calls_bot_backfills_returns_fresh_analysis(self):
        """E2E service path: drive a real trajectory (interface_error case)
        into the real repo, then call the REAL service with
        ``do_analysis=True``. The fake bot returns a scripted analysis; the
        service backfills it into BOTH tables (head + event rows) and returns
        the ``TaskTrajectory`` carrying the fresh analysis JSON."""
        db = _make_db()
        repo = TaskTrajectoryRepository(db)
        task_id, child = "e2e-svc", "c5"
        graph_svc = TaskGraphService()
        iface_msg = "底层接口 boom from service path"

        _drive_submit(repo, graph_svc, task_id=task_id)
        _drive_plan(repo, graph_svc, task_id=task_id, child_node_id=child)
        _drive_dispatch(repo, graph_svc, task_id=task_id, child_node_id=child,
                        rationale=_SAMPLE_RATIONALE)
        _drive_execute_interface_error(repo, graph_svc, task_id=task_id,
                                      child_node_id=child, exec_error=iface_msg)
        _emit_terminal_transition(repo, task_id=task_id, node_id=child,
                                 status_to=Status.FAILED)

        # Wire the REAL service with a fake bot that returns a scripted analysis
        # mirroring the rule executor's shape for an interface_error terminal task.
        bot_content = json.dumps({
            "analysis_output": "root cause: underlying interface error during execute",
            "boost_reason": "策略=search 模式=skill 选中=bot1(hit_single); 候选3 取最优; JOIN 丢=1个(claim_mode_off)",
            "failure_reason": f"underlying_interface_error: {iface_msg}",
        }, ensure_ascii=False)
        bot = _FakeBot(content=bot_content)
        analyzer = TaskTrajectoryAnalyzer(bot=bot)
        config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
        svc = TaskTrajectoryService(
            TaskTrajectoryAssembler(repo), repo, analyzer, config,
        )

        # do_analysis=True → assemble + call bot + backfill + return fresh analysis
        result = _run(svc.get_trajectory(task_id, do_analysis=True))

        # the bot was called with the configured bot_id + the trajectory summary
        assert bot.last_call is not None
        assert bot.last_call["bot_id"] == "bot-traj-analyst"
        assert "events=" in bot.last_call["message"]

        # the returned trajectory carries the fresh analysis JSON
        assert result.analysis is not None
        parsed = json.loads(result.analysis)
        assert parsed["analysis_type"] == "tc_bot"
        assert parsed["analysis_executor"] == "bot-traj-analyst"
        assert parsed["failure_reason"] == f"underlying_interface_error: {iface_msg}"
        assert "策略=search" in parsed["boost_reason"]
        assert "JOIN 丢=1个(claim_mode_off)" in parsed["boost_reason"]

        # the analysis was backfilled into BOTH tables (head + events rows)
        head = repo.list_head(task_id)
        assert head is not None
        assert head.analysis == result.analysis
        events = repo.list_events_by_task(task_id)
        assert events, "events should have been persisted by the gate drives"
        assert all(e.analysis == result.analysis for e in events), (
            "backfill_analysis must update ALL event rows' analysis (REQ-9/REQ-11)"
        )
        # gmt_modified advanced on the head (backfill sets it explicitly)
        assert head.gmt_modified is not None

    def test_do_analysis_false_is_pure_read_no_backfill(self):
        """``do_analysis=False`` (default) is a PURE READ: assembles and
        returns; no bot call, no backfill. The head row may be upserted
        (existence refresh) but ``analysis`` stays whatever was persisted
        before (None when never backfilled). Re-pinned at the e2e layer so
        the two-mode contract is exercised against the real repo."""
        db = _make_db()
        repo = TaskTrajectoryRepository(db)
        task_id, child = "e2e-read", "c6"
        graph_svc = TaskGraphService()
        _drive_submit(repo, graph_svc, task_id=task_id)
        _drive_plan(repo, graph_svc, task_id=task_id, child_node_id=child)
        _drive_dispatch(repo, graph_svc, task_id=task_id, child_node_id=child,
                        rationale=_SAMPLE_RATIONALE)

        bot = _FakeBot(content=json.dumps({"analysis_output": "should not be called"}))
        analyzer = TaskTrajectoryAnalyzer(bot=bot)
        config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
        svc = TaskTrajectoryService(
            TaskTrajectoryAssembler(repo), repo, analyzer, config,
        )

        result = _run(svc.get_trajectory(task_id, do_analysis=False))

        assert bot.last_call is None, "bot must NOT be called on do_analysis=False"
        assert result.analysis is None, (
            "pure read with no prior backfill must return analysis=None"
        )
        # the timeline is still assembled (read path works)
        actions = _timeline_action_types(result)
        assert "submit" in actions and "dispatch" in actions


# ---------------------------------------------------------------------------
# B — cross-restart readability (spec §验收 line 251: "实例重启后轨迹/分析接口仍可读")
# ---------------------------------------------------------------------------


class TestCrossRestartReadability:
    """Spec §验收 line 251: "实例重启后轨迹/分析接口仍可读" — the trajectory +
    analysis are DB-backed, so a FRESH assembler/service against the SAME DB
    after a simulated restart reads back the full timeline + persisted
    analysis. The in-memory SQLite ``StaticPool`` keeps ONE connection across
    the "restart" so the data survives (a process restart against a real
    OceanBase/SQLite-on-disk behaves identically). Proves the
    trajectory/analysis is NOT in-memory-only."""

    def test_fresh_assembler_against_same_db_reads_full_timeline_and_persisted_analysis(self):
        """Drive submit+plan+dispatch+execute into the real repo (process A),
        backfill an analysis, then SIMULATE a restart with a FRESH assembler
        against the SAME DB (process B) — assert the full timeline + the
        persisted analysis survive."""
        db = _make_db()
        repo = TaskTrajectoryRepository(db)
        task_id, child = "e2e-restart", "c7"
        graph_svc = TaskGraphService()
        _drive_submit(repo, graph_svc, task_id=task_id)
        _drive_plan(repo, graph_svc, task_id=task_id, child_node_id=child)
        _drive_dispatch(repo, graph_svc, task_id=task_id, child_node_id=child,
                        rationale=_SAMPLE_RATIONALE)
        _drive_execute_success(repo, graph_svc, task_id=task_id, child_node_id=child)
        _emit_terminal_transition(repo, task_id=task_id, node_id=child,
                                 status_to=Status.SUCCESS)

        # Backfill a scripted analysis directly (isolates cross-restart READ
        # from the bot-call path). The head row must exist before backfill
        # (mirrors the real service flow: assemble → upsert_head → backfill).
        TaskTrajectoryAssembler(repo).assemble(task_id)
        persisted_analysis = json.dumps({
            "analysis_type": "tc_bot",
            "analysis_executor": "bot-before-restart",
            "analysis_input": "events=5",
            "analysis_output": "conclusion before restart",
            "boost_reason": "策略=search 选中=bot1",
            "failure_reason": None,
            "gmt_create": int(time.time() * 1000),
        }, ensure_ascii=False)
        affected = repo.backfill_analysis(task_id, persisted_analysis)
        assert affected >= 1, "backfill should have updated the head + event rows"

        # --- simulate restart: a FRESH assembler against the SAME DB ---
        fresh_repo = TaskTrajectoryRepository(db)
        fresh_trajectory = TaskTrajectoryAssembler(fresh_repo).assemble(task_id)

        # the full timeline + persisted analysis survived (cross-restart readability)
        actions = _timeline_action_types(fresh_trajectory)
        assert actions, "fresh assembler returned an empty timeline after restart"
        assert actions[0] == "submit"
        for required in ("submit", "plan", "dispatch", "execute"):
            assert required in actions, f"cross-restart: missing {required}: {actions}"
        assert fresh_trajectory.analysis == persisted_analysis, (
            f"cross-restart: persisted analysis must survive; got {fresh_trajectory.analysis!r}"
        )
        parsed = json.loads(fresh_trajectory.analysis)
        assert parsed["analysis_executor"] == "bot-before-restart"
        assert parsed["failure_reason"] is None  # success task

    def test_fresh_service_do_analysis_false_reads_persisted_analysis_after_restart(self):
        """A FRESH ``TaskTrajectoryService`` (the full service, not just the
        assembler) against the SAME DB after a simulated restart —
        ``do_analysis=False`` returns the trajectory with the persisted
        analysis (cross-restart: the service READ path is DB-backed too)."""
        db = _make_db()
        repo = TaskTrajectoryRepository(db)
        task_id, child = "e2e-restart-svc", "c8"
        graph_svc = TaskGraphService()
        _drive_submit(repo, graph_svc, task_id=task_id)
        _drive_dispatch(repo, graph_svc, task_id=task_id, child_node_id=child,
                        rationale=_SAMPLE_RATIONALE)
        # Ensure the head row exists before backfill (mirrors the real service
        # flow: assemble → upsert_head → backfill_analysis).
        TaskTrajectoryAssembler(repo).assemble(task_id)
        persisted = json.dumps({
            "analysis_type": "rule",
            "analysis_executor": "rule_engine",
            "analysis_output": "cross-restart service read",
            "failure_reason": None,
            "gmt_create": int(time.time() * 1000),
        }, ensure_ascii=False)
        repo.backfill_analysis(task_id, persisted)

        # fresh service against the same DB (simulated restart)
        fresh_repo = TaskTrajectoryRepository(db)
        fresh_svc = TaskTrajectoryService(
            TaskTrajectoryAssembler(fresh_repo), fresh_repo,
            TaskTrajectoryAnalyzer(),
            TrajectoryAnalysisConfig(analysis_bot_id="bot-x"),
        )
        result = _run(fresh_svc.get_trajectory(task_id, do_analysis=False))
        assert result.analysis == persisted, (
            "fresh service do_analysis=False must return the persisted analysis"
        )
        actions = _timeline_action_types(result)
        assert "submit" in actions and "dispatch" in actions
