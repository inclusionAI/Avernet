"""TDD tests for the TRANSITION trajectory gate (REQ-1 / 决策 #14).

P3 wired submit/plan/dispatch/execute/verify/reset but NOT ``transition``
(an oversight the P7 e2e uncovered: the analyzer's ``_terminal_status`` /
production ``failure_reason`` need a terminal TRANSITION row, which was
emitted only by a manual test injection, masking the gap). This file wires
the 3 ``_log_action(NodeAction.TRANSITION, ...)`` sites additively:

    * ``_on_pass_collect`` non-root gap closure (PLANNING→SUCCESS, parent) —
      engine.py ``_on_pass_collect`` ~site 1.
    * ``_hung_and_escalate`` node → HUNG — engine.py ~site 2.
    * ``_maybe_finish_graph`` root gap closure (root→SUCCESS) — engine.py ~site 3.

Each site fires an ADDITIVE ``TrajectoryActionType.TRANSITION`` trajectory
event next to the byte-unchanged ``_log_action`` call:

    action_type   = "transition"
    action_result = status_to-derived lowercase name
                    (SUCCESS→"success", HUNG→"hung", FAILED→"failed", …;
                     open-ended enum, REQ-1)
    action_input  = None  (REQ-1: "reset/transition→null"; trigger reason
                    rides in ext_info)
    status_from / status_to = the transition's from/to (mirror _log_action)
    attempt       = 0  (transitions are structural/terminal flips; the
                    analyzer's terminal gate reads status_to, not attempt)
    ext_info      = {"reason": <trigger>} (gap_closed_propagate /
                    root_gap_closed / the hung_reason)
    error_type /  = None for SUCCESS transitions; ``ReasonCatalog.HUNG`` +
    error_msg      the hung_reason for the HUNG transition (so the analyzer's
                    bullet 4 derives "hung: {reason}" from the real gate).

决策 #14: only the emission is swallowed (inside ``emit_trajectory_event`` —
try/except + WARNING, no re-raise); the gate's STATUS MUTATION
(``update_task_node_info`` flip, ``_escalate_hung``) is NOT wrapped. Pinned by
the raising-repo test below.

Invariants the tests pin (cross-cutting with the task constraints):
    * The existing ``_log_action(NodeAction.TRANSITION, ...)`` stays byte-
      unchanged (additive ``_log_trajectory`` alongside, NOT a replacement) —
      the node action_log still carries the TRANSITION entry with the existing
      payload ({"reason": ..., "to": ...}), AND a transition trajectory row
      fires alongside.
    * ``NodeAction`` enum / ``append_action_event`` / ``task_action_log`` are
      untouched — ``TrajectoryActionType`` is a SEPARATE string set.
    * The analyzer's ``_terminal_status`` / ``failure_reason`` derivation now
      works from REAL transition rows (not manual injection).
Authoritative: spec REQ-1 / REQ-9 / 决策 #14.
"""
from __future__ import annotations

from tests.community.core.task.task_trajectory._task_context_support import _tcs

import asyncio
import json
import time

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    PlanResult,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_runner.execution_adapters import CentralizedExecutionAdapter
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
)
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)
from agentclaw.community.core.task.task_context.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
    _terminal_status,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryEvent,
)


# ---------------------------------------------------------------------------
# Shared helpers / fakes (mirror the P3 gate-test harnesses)
# ---------------------------------------------------------------------------


def _run(coro):
    """Sync wrapper to drive async engine methods in unit tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _task_info(task_id: str = "t1", max_depth: int = 3, extra_cfg: dict | None = None) -> TaskInfo:
    cfg = {"MAX_DEPTH": max_depth, "BBS_MAX_DEPTH": 3, "task_type": "dynamic"}
    if extra_cfg:
        cfg.update(extra_cfg)
    return TaskInfo(task_id=task_id,
        task_spec=TaskSpec(

            context=Context(background="bg", title="T"),
            goal=Goal(
                objective="o",
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


def _accept(verdict: AcceptanceVerdict = AcceptanceVerdict.DONE) -> AcceptanceResult:
    return AcceptanceResult(verdict=verdict, done_items=[], gap_items=[])


class _TrajRepo:
    """Fake trajectory repo — captures every ``insert_event`` call (no SQLite)."""

    def __init__(self) -> None:
        self.records: list[TrajectoryEventRecord] = []

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self.records.append(record)
        return record


class _RaisingOnTransitionRepo:
    """Repo that raises **only for transition** rows — pins that the TRANSITION
    emission was attempted (RED before the gate is wired) AND that the emitter
    swallow+WARNING (决策 #14) keeps the gate's STATUS MUTATION driving
    forward. Non-transition rows succeed so the rest of the path is not
    perturbed by this defense probe."""

    def __init__(self) -> None:
        self.transition_attempts = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        if record.action_type == "transition":
            self.transition_attempts += 1
            raise RuntimeError("simulated TRANSITION insert failure")
        return record


class _StubPlanner:
    """Single-attempt planner — returns one empty-children, no-gap
    ``PlanResult`` so ``_on_pass_collect`` converges to the gap-closed
    transition path (site 1 / site 3) instead of producing new children."""

    async def plan(self, graph, target_node_id=None):
        return PlanResult(children=[], has_gap=False, gap_detail="done")


class _StubDispatcher:
    def __init__(self, run_mode="single_bot", assignee="bot1"):
        self.run_mode = run_mode
        self.assignee = assignee

    async def dispatch(self, toDoTaskList):
        for n in toDoTaskList:
            n.run_info.run_mode = self.run_mode
            n.run_info.assignee = self.assignee
        return toDoTaskList


class _StubRunner:
    async def start_run(self, toDoTaskList):
        return [True] * len(toDoTaskList)

    async def form_coop_group(self, gf):
        return "grp_stub"


class _TrajectoryCaseEngine(CentralizedExecutionAdapter):
    """Test subclass — injects stubs + a trajectory repo (mirrors the P3
    gate-test subclasses)."""

    def __init__(self, graph, planner=None, dispatcher=None, runner=None,
                 trajectory_repo=None) -> None:
        self._case_planner = planner
        self._case_dispatcher = dispatcher
        self._case_runner = runner
        super().__init__(graph, task_context_service=_tcs(trajectory_repo))

    def _build_planner(self):
        return self._case_planner if self._case_planner is not None else super()._build_planner()

    def _build_dispatcher(self):
        return self._case_dispatcher if self._case_dispatcher is not None else super()._build_dispatcher()

    def _build_runner(self):
        return self._case_runner if self._case_runner is not None else super()._build_runner()


def _transition_records(repo: _TrajRepo) -> list[TrajectoryEventRecord]:
    return [r for r in repo.records if r.action_type == "transition"]


def _ext_info(record: TrajectoryEventRecord) -> dict:
    """Parse the wrapped ``{"schema_v": 1, **ext_info}`` envelope."""
    assert record.ext_info is not None, "transition row must carry ext_info"
    payload = json.loads(record.ext_info)
    assert payload["schema_v"] == 1, "ext_info must be wrapped in schema_v envelope"
    return payload


def _set_running_node(svc, task_id, node_id, *, run_mode="single_bot",
                     start_time=None, harness_retries=0, assignee="b1") -> None:
    """Preset a child node into RUNNING (mirrors the gate-test helper)."""
    svc.add_task_nodes([_child(node_id, task_id)], parent_node_id=task_id)
    svc.update_task_node_info(
        _patch(task_id, node_id, status=Status.RUNNING, run_mode=run_mode,
               assignee=assignee, start_time=start_time,
               extend_props_patch={"harness_retries": harness_retries})
    )


# ---------------------------------------------------------------------------
# 1. action_result mapping helper (pure unit)
# ---------------------------------------------------------------------------


class TestTransitionActionResultMapping:
    """``_transition_action_result`` maps ``status_to`` → the lowercase
    status-name ``action_result`` (REQ-1 open-ended enum). Pinned for every
    ``Status`` so a future status addition is caught."""

    @pytest.mark.parametrize("status,expected", [
        (Status.SUCCESS, "success"),
        (Status.HUNG, "hung"),
        (Status.FAILED, "failed"),
        (Status.CANCELLED, "cancelled"),
        (Status.DONE, "done"),
        (Status.RUNNING, "running"),
        (Status.PENDING, "pending"),
        (Status.PLANNING, "planning"),
    ])
    def test_status_to_lowercase_name(self, status, expected):
        assert TaskTrajectoryService._transition_action_result(status) == expected

    def test_none_status_to_transition(self):
        assert TaskTrajectoryService._transition_action_result(None) == "transition"


# ---------------------------------------------------------------------------
# 2. Site 2 — _hung_and_escalate (node → HUNG) via on_harness retries>=MAX
# ---------------------------------------------------------------------------


class TestTransitionGateHung:
    """Site 2 (``_hung_and_escalate``): a node RUNNING with
    ``harness_retries >= MAX_HARNESS`` → ``on_harness(exec_error)`` escalates
    to HUNG. The real transition gate fires a TRANSITION trajectory row
    carrying ``error_type=HUNG`` + the hung_reason (so the analyzer's bullet 4
    derives ``hung: {reason}`` in production)."""

    def test_hung_transition_emits_row_with_hung_error_type(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", run_mode="single_bot",
                          start_time=int(time.time() * 1000) - 1_000,
                          harness_retries=3)  # >= MAX_HARNESS(2) → HUNG
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", exec_error="exec_failed_retry")))

        # the node was flipped to HUNG (the gate's main mutation)
        assert svc._get_node(graph, "c1").status == Status.HUNG

        trans = _transition_records(repo)
        assert len(trans) == 1, [(r.action_type, r.action_result) for r in repo.records]
        rec = trans[0]
        assert rec.action_type == "transition"
        assert rec.action_result == "hung"
        assert rec.action_input is None, "REQ-1: transition action_input=null"
        assert rec.status_from == Status.RUNNING
        assert rec.status_to == Status.HUNG
        assert rec.error_type == ReasonCatalog.HUNG.value
        assert rec.error_msg == "exec_stuck"  # _on_harness_collect's hung_reason
        assert rec.attempt == 0
        info = _ext_info(rec)
        assert info["reason"] == "exec_stuck"

    def test_hung_transition_log_action_byte_unchanged_and_additive(self):
        """The existing ``_log_action(NodeAction.TRANSITION, ...)`` stays
        byte-unchanged — the node action_log still carries the TRANSITION entry
        with the existing payload, AND a transition trajectory row fires
        alongside (additive, not a replacement)."""
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", run_mode="single_bot",
                          start_time=int(time.time() * 1000), harness_retries=3)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", exec_error="exec_failed_retry")))

        # 1) action_log TRANSITION entry preserved (byte-unchanged _log_action)
        node = svc._get_node(graph, "c1")
        trans_events = [e for e in node.run_info.action_log
                        if e.action.value == "transition"]
        assert len(trans_events) == 1, [e.action.value for e in node.run_info.action_log]
        ev = trans_events[0]
        assert ev.payload["reason"] == "exec_stuck"
        assert ev.payload["to"] == "HUNG"
        assert ev.status_from.value == "RUNNING"
        assert ev.status_to.value == "HUNG"
        # 2) trajectory side fired ONE additive transition row
        assert len(_transition_records(repo)) == 1


# ---------------------------------------------------------------------------
# 3. Site 3 — _maybe_finish_graph (root gap closure → root SUCCESS)
# ---------------------------------------------------------------------------


class TestTransitionGateRootSuccess:
    """Site 3 (``_maybe_finish_graph``): the last leaf passes acceptance
    (SUCCESS) → ``_on_pass_collect`` → all siblings SUCCESS → owner re-plans
    root gap → closed → root flips SUCCESS. The real transition gate fires a
    TRANSITION trajectory row with ``action_result="success"``,
    ``status_to=SUCCESS``, ``reason="root_gap_closed"``."""

    def test_root_success_transition_emits_row(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        # seed + dispatch a child so it becomes RUNNING (owner-bot assigned)
        svc.add_task_nodes([_child("c1", "t1")], parent_node_id="t1")
        svc.update_task_node_info(
            _patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot",
                   assignee="bot1")
        )
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        # acceptance DONE + status SUCCESS on the leaf → _on_pass_collect
        # converges to _maybe_finish_graph (root gap closed → root SUCCESS)
        _run(eng.on_report(_patch("t1", "c1", status=Status.SUCCESS,
                                  acceptance_result=_accept(AcceptanceVerdict.DONE))))

        trans = _transition_records(repo)
        assert len(trans) >= 1, [(r.action_type, r.action_result) for r in repo.records]
        # the root-success transition row (reason=root_gap_closed)
        root_trans = [r for r in trans if r.status_to == Status.SUCCESS]
        assert root_trans, f"no SUCCESS transition row: {[(r.status_to, r.action_result) for r in trans]}"
        rec = root_trans[-1]
        assert rec.action_type == "transition"
        assert rec.action_result == "success"
        assert rec.action_input is None
        assert rec.status_to == Status.SUCCESS
        assert rec.error_type is None
        assert rec.error_msg is None
        info = _ext_info(rec)
        assert info["reason"] == "root_gap_closed"
        # root was flipped to SUCCESS by the gate's main mutation
        assert svc._get_node(graph, "t1").status == Status.SUCCESS


# ---------------------------------------------------------------------------
# 4. Site 1 — _on_pass_collect non-root gap closure (PLANNING→SUCCESS, parent)
# ---------------------------------------------------------------------------


class TestTransitionGateNonRootParentSuccess:
    """Site 1 (``_on_pass_collect`` non-root gap closure): a 3-level graph
    (root → middle → leaf) — the leaf passes acceptance (SUCCESS) → the
    MIDDLE (non-root) parent's gap closes → middle flips PLANNING→SUCCESS with
    ``reason="gap_closed_propagate"``. The recursive ``_on_pass_collect`` then
    also fires site 3 (root → SUCCESS)."""

    def test_non_root_parent_success_transition_emits_row(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        # root(t1) → middle(m1) → leaf(l1)
        svc.add_task_nodes([_child("m1", "t1")], parent_node_id="t1")
        svc.add_task_nodes([_child("l1", "t1")], parent_node_id="m1")
        svc.update_task_node_info(
            _patch("t1", "l1", status=Status.RUNNING, run_mode="single_bot",
                   assignee="bot1")
        )
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_report(_patch("t1", "l1", status=Status.SUCCESS,
                                  acceptance_result=_accept(AcceptanceVerdict.DONE))))

        trans = _transition_records(repo)
        assert len(trans) >= 2, [(r.action_type, r.status_from, r.status_to, r.action_result) for r in repo.records]
        # site 1: middle PLANNING→SUCCESS (reason=gap_closed_propagate)
        site1 = [r for r in trans
                 if r.status_from == Status.PLANNING
                 and r.status_to == Status.SUCCESS
                 and _ext_info(r).get("reason") == "gap_closed_propagate"]
        assert site1, (
            f"no non-root gap-closed transition: "
            f"{[(r.status_from, r.status_to, r.action_result) for r in trans]}"
        )
        rec = site1[0]
        assert rec.action_type == "transition"
        assert rec.action_result == "success"
        assert rec.action_input is None
        assert rec.node_id == "m1"
        assert rec.error_type is None
        # site 3: root → SUCCESS (reason=root_gap_closed) — recursive collect
        site3 = [r for r in trans if _ext_info(r).get("reason") == "root_gap_closed"]
        assert site3, "recursive _on_pass_collect should also fire the root-success transition"
        # the parent (middle) was flipped to SUCCESS by the gate's main mutation
        assert svc._get_node(graph, "m1").status == Status.SUCCESS


# ---------------------------------------------------------------------------
# 5. Analyzer — _terminal_status / failure_reason from REAL transition rows
#    (re-asserts the acceptance failure_reason derivations work from real
#    transition rows, not manual injection — the gap P7 uncovered)
# ---------------------------------------------------------------------------


def _ev(action_type, *, action_result, status_to, status_from=None,
        error_type=None, error_msg=None, node_id="n1") -> TrajectoryEvent:
    """Build a TrajectoryEvent shaped like the real gate emits (the assembler
    restores Status enums on status_from/status_to and ReasonCatalog on
    error_type)."""
    return TrajectoryEvent(
        task_id="t1", node_id=node_id,
        action_type=action_type,
        action_result=action_result,
        action_input=None,
        status_from=status_from,
        status_to=status_to,
        attempt=0,
        error_type=error_type,
        error_msg=error_msg,
        analysis=None,
        gmt_create=int(time.time() * 1000),
        gmt_modified=int(time.time() * 1000),
    )


class TestAnalyzerFromRealTransitionRows:
    """The analyzer's ``_terminal_status`` / ``failure_reason`` derivation now
    works from REAL transition rows (the kind the 3 gates emit), not manual
    injection. Re-asserts the 4 acceptance failure_reason derivations."""

    @staticmethod
    def _no_ext(ev):
        return None

    def test_terminal_status_none_when_no_terminal_transition(self):
        # intermediate PENDING→RUNNING transition must NOT be terminal
        timeline = [
            _ev(TrajectoryActionType.SUBMIT, action_result="success",
                status_to=Status.PENDING, status_from=None),
            _ev(TrajectoryActionType.TRANSITION, action_result="running",
                status_to=Status.RUNNING, status_from=Status.PENDING),
        ]
        assert _terminal_status(timeline) is None

    def test_success_terminal_transition_yields_failure_reason_none(self):
        timeline = [
            _ev(TrajectoryActionType.SUBMIT, action_result="success",
                status_to=Status.PENDING, status_from=None),
            _ev(TrajectoryActionType.TRANSITION, action_result="success",
                status_to=Status.SUCCESS, status_from=Status.PLANNING),
        ]
        assert _terminal_status(timeline) == Status.SUCCESS
        analyzer = TaskTrajectoryAnalyzer()
        traj = TaskTrajectory(task_id="t1", timeline=timeline, analysis=None,
                              gmt_create=0, gmt_modified=0)
        out = analyzer._analyze_rule(traj, self._no_ext, "rule_engine")
        assert out.failure_reason is None, "SUCCESS terminal → failure_reason=None"

    def test_hung_terminal_transition_with_error_type_yields_hung_reason(self):
        # real gate: site 2 sets error_type=HUNG + error_msg=hung_reason
        timeline = [
            _ev(TrajectoryActionType.SUBMIT, action_result="success",
                status_to=Status.PENDING, status_from=None),
            _ev(TrajectoryActionType.TRANSITION, action_result="hung",
                status_to=Status.HUNG, status_from=Status.RUNNING,
                error_type=ReasonCatalog.HUNG, error_msg="exec_stuck"),
        ]
        assert _terminal_status(timeline) == Status.HUNG
        analyzer = TaskTrajectoryAnalyzer()
        traj = TaskTrajectory(task_id="t1", timeline=timeline, analysis=None,
                              gmt_create=0, gmt_modified=0)
        out = analyzer._analyze_rule(traj, self._no_ext, "rule_engine")
        assert out.failure_reason is not None
        assert out.failure_reason.startswith("hung:")
        assert "exec_stuck" in out.failure_reason

    def test_interface_error_plus_hung_terminal_yields_underlying_interface_error(self):
        # real drives: interface_error EXECUTE(err) → harness escalates to HUNG
        # (terminal HUNG). Bullet 2 (underlying_interface_error) fires BEFORE
        # bullet 4 (hung) — the terminal HUNG gate only opens failure_reason
        # derivation; the bullet priority picks the interface error.
        timeline = [
            _ev(TrajectoryActionType.SUBMIT, action_result="success",
                status_to=Status.PENDING, status_from=None),
            _ev(TrajectoryActionType.EXECUTE, action_result="failed",
                status_to=Status.HUNG,
                error_type=ReasonCatalog.UNDERLYING_INTERFACE_ERROR,
                error_msg="connection refused"),
            _ev(TrajectoryActionType.TRANSITION, action_result="hung",
                status_to=Status.HUNG, status_from=Status.RUNNING,
                error_type=ReasonCatalog.HUNG, error_msg="exec_stuck"),
        ]
        assert _terminal_status(timeline) == Status.HUNG
        analyzer = TaskTrajectoryAnalyzer()
        traj = TaskTrajectory(task_id="t1", timeline=timeline, analysis=None,
                              gmt_create=0, gmt_modified=0)
        out = analyzer._analyze_rule(traj, self._no_ext, "rule_engine")
        assert out.failure_reason is not None
        assert out.failure_reason.startswith("underlying_interface_error:")
        assert "connection refused" in out.failure_reason


# ---------------------------------------------------------------------------
# 6. 决策 #14 — raising trajectory repo → transition gate still completes
#    the STATUS MUTATION (swallow is emission-only)
# ---------------------------------------------------------------------------


class TestTransitionGateDecision14:
    """决策 #14: if the trajectory repo raises on the transition emission,
    the gate's STATUS MUTATION (the HUNG flip + escalation) still completes —
    the swallow is emission-only (inside ``emit_trajectory_event``), the
    gate's ``update_task_node_info`` flip is NOT wrapped. Pinned by driving
    the HUNG path with a repo that raises only on transition rows."""

    def test_raising_repo_hung_gate_still_flips_to_hung(self, caplog):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", run_mode="single_bot",
                          start_time=int(time.time() * 1000), harness_retries=3)
        raising = _RaisingOnTransitionRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=raising,
        )
        with caplog.at_level("WARNING", logger="task.trajectory"):
            _run(eng.on_harness(_patch("t1", "c1", exec_error="exec_failed_retry")))
        # the TRANSITION emission was attempted (proves the gate fired it)
        assert raising.transition_attempts == 1, "TRANSITION emission not attempted"
        # ... and the emitter logged a WARNING (swallow + WARN, 决策 #14)
        assert any("发射失败" in rec.message for rec in caplog.records), (
            "emitter must log a WARNING on the swallowed transition failure"
        )
        # 决策 #14: the gate's STATUS MUTATION completed — node is HUNG
        # despite the trajectory repo raising (the swallow is emission-only)
        assert svc._get_node(graph, "c1").status == Status.HUNG, (
            "HUNG status flip must survive a raising trajectory repo "
            "(decision #14: emission-only swallow)"
        )
