"""TDD test for the consolidated trajectory zero-intrusion guard (决策 #14).

P3 item "intrusion guard (cross-cutting)" of the task-trajectory spec. The
individual gate tests (P3-1 DISPATCH, P3-2 PLAN, P3-3 RESET, P3-4 EXECUTE/VERIFY,
P3-5 SUBMIT) each pin that a broken trajectory repo doesn't break THAT gate.
This file consolidates the 决策 #14 guarantee across a fuller lifecycle span
in ONE place:

    SUBMIT (via TaskService.execute)
      → a PLAN attempt (via _plan_with_retry)
      → a DISPATCH (HIT_SINGLE via _prepare_into + _drain)
      → an EXECUTE/VERIFY report (success acceptance via on_report)
      → a harness RESET (SLA-timeout via on_harness)

with ONE raising-and-capturing trajectory repo injected across every gate
drive in the span. The repo records every ``insert_event`` attempt per
action_type AND raises on every call, so the consolidated test asserts:

    * every gate's main logic completes and the task drives forward (statuses
      advance, no exception propagates from trajectory emission);
    * the repo recorded ≥1 attempt per action_type in the span — the gate
      really did call ``insert_event`` for each action (so a future regression
      that removes a gate's ``_log_trajectory`` call surfaces here, not just
      by silence at the per-gate test);
    * a WARNING log landed on logger ``"task.trajectory"`` for every raised
      emission attempt (the emitter's 决策 #14 contract: swallow-but-visible).

The second class pins the "独立旁路 / no ``task_action_log`` coupling"
invariant globally at the AST level so a future change can't silently
re-couple the trajectory path to the action-log enum/table:

    * the trajectory module (models + payloads) must NOT import or reference
      ``NodeAction`` at the code level (docstrings naming it as "do not touch"
      are prose — AST walk ignores them);
    * the trajectory emitter must NOT invoke ``append_action_event`` (the graph
      method that mutates the in-memory action_log) — the trajectory side is a
      direct-INSERT to ``task_trajectory_events`` via ``repo.insert_event``;
    * ``submit`` stays a ``TrajectoryActionType`` member ONLY, absent from
      ``NodeAction``.

Authoritative source: ``specs/2026-09-16-task-trajectory-collection-and-analysis/spec.md``
(决策 #14, "发射阻塞正向驱动" 风险与边界 line, 验收(端到端) intrusion expectation)
+ ``plan.md`` Global constraints (the "gate test that breaks the repo"
discipline).

Harness note: per the task's "lean toward NOT touching the four existing
reviewed test files" guidance, the minimal harness (``_TrajectoryCaseEngine``,
``_RaisingAndCapturingRepo``, ``_StubPlanner`` / ``_RationaleStubDispatcher`` /
``_StubDispatcher`` / ``_StubRunner``, ``_CaseTaskService``, the domain
fixtures) is copied in-place from the four gate-test files rather than
extracted to a shared helper — keeping the diff contained at one new file.
"""
from __future__ import annotations

from tests.community.core.task.task_trajectory._task_context_support import _tcs

import ast
import asyncio
import inspect
import logging
import time

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskOpResult,
    TaskSourceType,
    TaskSpec,
    TaskType,
)
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance,
    RequestContext,
    RequestGoal,
    RequestMetadata,
    RequestTaskSpec,
    TaskInfoRequest,
)
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)


# ---------------------------------------------------------------------------
# Shared helpers / fakes — copied in-place (per the task's guidance to NOT
# touch the four existing reviewed test files). These mirror the harness
# proven across the P3-1..P3-5 gate test files.
# ---------------------------------------------------------------------------


def _run(coro):
    """Sync wrapper to drive async engine / service methods in unit tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


class _RaisingAndCapturingRepo:
    """The consolidated zero-intrusion probe. Records every ``insert_event``
    attempt per action_type (so the test can assert "every gate really
    attempted emission") and THEN raises on every call — so the gate's
    emitter swallow+WARNING (决策 #14) fires for every gate, and the
    forward-driving path is provably unaffected. ``insert_event`` ALWAYS
    raises (the intrusion guard is about a RAISING repo, not None — the
    None case is already covered by the per-gate tests).
    """

    def __init__(self) -> None:
        self.attempts_by_type: dict[str, int] = {}
        self.calls = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self.calls += 1
        self.attempts_by_type[record.action_type] = (
            self.attempts_by_type.get(record.action_type, 0) + 1
        )
        raise RuntimeError(f"trajectory {record.action_type} boom")


# -- Domain fixtures ---------------------------------------------------------


def _task_info(task_id: str = "guard-t1", max_depth: int = 3) -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(
                objective="o",
                acceptances=[AcceptanceCriteria(id="ac1", description="d")],
            ),
        ),
        source_type="bot",
        owner_bot_id="owner:1",
        execution_config={
            "MAX_DEPTH": max_depth,
            "BBS_MAX_DEPTH": 3,
            "task_type": "dynamic",
        },
    )


def _child(node_id: str, task_id: str = "guard-t1") -> TaskNode:
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
    return AcceptanceResult(
        verdict=verdict, acceptances_metric=[], gaps=gaps or [],
    )


def _request(*, task_type=TaskType.DYNAMIC,
             source_type=TaskSourceType.API,
             task_id: str = "guard-submit") -> TaskInfoRequest:
    """Build a TaskInfoRequest matching the SUBMIT-gate test's ``_request``
    helper (REQ-6) — drives ``TaskService.execute`` for the SUBMIT phase."""
    cfg: dict = {"task_type": task_type}
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="T", instruction="do"),
            context=RequestContext(background="bg"),
            goal=RequestGoal(
                objective="o",
                acceptances=[RequestAcceptance(id="ac1", acceptance="acc")],
            ),
        ),
        source_type=source_type,
        owner_user_id="U1",
        owner_bot_id="B1",
        execution_config=cfg,
    )


# -- Stubs (planner / dispatcher / runner) -----------------------------------


class _StubPlanner:
    """Single-attempt planner — returns one ``PlanResult`` per call (``has_gap=False``
    so the retry loop converges on attempt 0, firing exactly one plan row)."""

    async def plan(self, graph, target_node_id=None):
        from agentclaw.community.core.task.domain.models import PlanResult
        return PlanResult(children=[], has_gap=False, gap_detail="done")


class _RationaleStubDispatcher:
    """DISPATCH-gate stub that mirrors ``TaskDispatcher``'s ``_dispatch_rationale``
    carrier write. Writes a minimal rationale dict + sets run_mode/assignee
    for HIT_SINGLE (the trajectory ``dispatch`` event then reads the carrier)."""

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


class _StubDispatcher:
    """Plain dispatcher stub (mirrors the RESET / EXEC-VERIFY gate tests)."""

    def __init__(self, run_mode: str = "single_bot", assignee: str = "bot1"):
        self.run_mode = run_mode
        self.assignee = assignee

    async def dispatch(self, toDoTaskList):
        out = []
        for n in toDoTaskList:
            n.run_info.run_mode = self.run_mode
            n.run_info.assignee = self.assignee
            out.append(n)
        return out


class _StubRunner:
    """Mirrors the existing StubRunner across the gate tests: ``_drain`` calls
    ``start_run(toDoTaskList) -> list[bool]`` + ``form_coop_group``."""

    def __init__(self) -> None:
        self.run_calls: list[list[TaskNode]] = []

    async def start_run(self, toDoTaskList):
        self.run_calls.append(list(toDoTaskList))
        return [True] * len(toDoTaskList)

    async def form_coop_group(self, gf):
        return "grp_stub"


_MINIMAL_RATIONALE: dict = {
    "strategy_name": "direct",
    "decision_mode": "direct",
    "candidates": [],
    "prefetch_tokens": [],
    "join_filter_applied": False,
    "join_dropped": [],
    "skill_prompt_digest": None,
    "skill_response_digest": None,
}


# -- Engine / service test subclasses ---------------------------------------


class _TrajectoryCaseEngine(ExecutionEngine):
    """Engine test subclass — injects stubs + a trajectory repo (mirrors the
    four P3-1..P3-5 gate-test subclasses); used for the PLAN/DISPATCH/
    EXECUTE/VERIFY/RESET drives in the consolidated guard."""

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


class _CaseTaskService(TaskService):
    """SUBMIT-phase facade mirroring ``test_trajectory_gates_submit``'s
    ``_CaseTaskService``: stubs the workflow/yaml/bbs branches to a no-op
    success so the SUBMIT gate fires deterministically; the dynamic branch
    schedules ``on_execute`` in the background (drained via ``drain_background``
    which swallows any background fallout — the SUBMIT row already fired)."""

    async def _run_workflow(self, task_id, request, task_info, run_id):
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id)

    async def _run_yaml(self, task_id, request, task_info, run_id):
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id)

    async def _run_bbs(self, task_id, request, task_info, run_id):
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id)


def _exec(facade, request):
    """execute (fire-and-forget) → drain_background so the background task
    settles (same event loop, deterministic). Background errors are swallowed
    by ``drain_background`` (returns ``return_exceptions=True``) — the SUBMIT
    row already fired by then."""

    async def _go():
        r = await facade.execute(request)
        await facade.drain_background()
        return r

    return asyncio.new_event_loop().run_until_complete(_go())


def _set_running_node(svc, task_id, node_id, *, run_mode: str = "single_bot",
                     assignee: str = "bot1", start_time=None,
                     harness_retries: int = 0,
                     request_input: str | None = None) -> None:
    """Add a child node + preset it to RUNNING (mirrors the RESET /
    EXEC-VERIFY gate tests' helper). Used for the RESET phase setup."""
    svc.add_task_nodes([_child(node_id, task_id)], parent_node_id=task_id)
    ep: dict = {"harness_retries": harness_retries}
    if request_input is not None:
        ep["_exec_request_input"] = request_input
    svc.update_task_node_info(
        _patch(task_id, node_id, status=Status.RUNNING, run_mode=run_mode,
               assignee=assignee, start_time=start_time, extend_props_patch=ep)
    )


def _set_running_root(svc, task_id, *, request_input: str | None = None) -> None:
    """Preset the ROOT node to RUNNING (the root exists from ``initialize_graph``).
    Used for the EXECUTE/VERIFY-phase success drive (DONE makes the graph
    terminal, avoiding the re-plan path — same pattern as the EXEC-VERIFY
    gate test)."""
    ep: dict = {"harness_retries": 0}
    if request_input is not None:
        ep["_exec_request_input"] = request_input
    svc.update_task_node_info(
        _patch(task_id, task_id, status=Status.RUNNING, run_mode="single_bot",
               assignee="b", start_time=None, extend_props_patch=ep)
    )


# The lifecycle span action_types in order. The consolidated guard asserts
# each was attempted and each got a WARNING in the cross-gate assertions.
_TRAJECTORY_ACTION_TYPES = (
    "submit", "plan", "dispatch", "execute", "verify", "reset",
)


# ---------------------------------------------------------------------------
# 1. Cross-gate consolidated zero-intrusion guard
# ---------------------------------------------------------------------------


class TestTrajectoryIntrusionGuard:
    """决策 #14 consolidated cross-gate guard: with ONE raising-and-capturing
    trajectory repo injected across the SUBMIT → PLAN → DISPATCH →
    EXECUTE/VERIFY → RESET lifecycle span, every gate's main logic completes
    and drives forward, every gate's emission is attempted (repo records it),
    and every raised attempt lands a WARNING on ``logger "task.trajectory"``.

    The unification is the point: the SAME raising repo + SAME caplog context
    threads across all five gate drives in ONE test. Each phase reuses the
    in-memory fake harness proven in the per-gate tests (no SQLite, no real
    BCS/OceanBase stack) — the cross-gate guard exercises the *swallow* across
    gates, not a production e2e. Phases use distinct task_ids (``guard-submit``,
    ``guard-plan``, ``guard-disp``, ``guard-ev``, ``guard-rst``) so a failure
    points at exactly which gate broke the swallow guarantee.
    """

    def test_cross_gate_zero_intrusion_across_full_lifecycle_span(self, caplog):
        repo = _RaisingAndCapturingRepo()
        # Single caplog context spans all five gate drives so the cross-gate
        # WARNING assertions at the end see every raised attempt.
        with caplog.at_level(logging.WARNING, logger="task.trajectory"):

            # ----- SUBMIT phase (TaskService.execute) -----
            # The SUBMIT gate sits inside TaskService.execute (after the
            # task_info persist point). The raising repo makes the emitter
            # swallow+WARNING; execute's main logic (persist + schedule
            # background on_execute) still completes and returns success.
            graph_svc_submit = TaskGraphService()
            svc = _CaseTaskService(
                graph_svc_submit,
                task_info_repo=None,
                task_id_provider=lambda: "guard-submit",
                task_context_service=_tcs(repo),
            )
            result = _exec(svc, _request(task_id="guard-submit"))
            # SUBMIT gate's main logic (execute) completes + drives forward
            assert result.task_id == "guard-submit"
            assert result.success is True

            # ----- PLAN phase (_plan_with_retry, one attempt) -----
            # One-attempt successful plan (``has_gap=False`` → loop converges
            # on attempt 0) fires one plan trajectory row. The raising repo
            # makes the emitter swallow+WARNING; the plan result still returns.
            graph_svc_plan = TaskGraphService()
            graph_plan = graph_svc_plan.initialize_graph(_task_info("guard-plan"))
            eng_plan = _TrajectoryCaseEngine(
                graph_svc_plan, planner=_StubPlanner(), trajectory_repo=repo,
            )
            pr = _run(eng_plan._plan_with_retry("guard-plan", graph_plan))
            # PLAN gate's main logic completes + drives forward (PlanResult)
            assert pr is not None
            assert pr.has_gap is False

            # ----- DISPATCH phase (_prepare_into + _drain, HIT_SINGLE) -----
            # Seed a PENDING child for the dispatcher; ``_prepare_into`` +
            # ``_drain`` flip it to RUNNING and fire one `dispatch` trajectory
            # row (reading the rationale carrier the stub dispatcher wrote).
            graph_svc_disp = TaskGraphService()
            graph_disp = graph_svc_disp.initialize_graph(_task_info("guard-disp"))
            graph_svc_disp.add_task_nodes([_child("c1", "guard-disp")], "guard-disp")
            eng_disp = _TrajectoryCaseEngine(
                graph_svc_disp,
                planner=_StubPlanner(),
                dispatcher=_RationaleStubDispatcher(
                    outcome="hit_single", rationale=_MINIMAL_RATIONALE,
                ),
                runner=_StubRunner(),
                trajectory_repo=repo,
            )
            side: list[tuple] = []
            _run(eng_disp._prepare_into("guard-disp", side))
            _run(eng_disp._drain("guard-disp", side))
            # DISPATCH gate's main logic completes + drives forward: PENDING
            # child dispatched → RUNNING.
            assert graph_svc_disp._get_node(graph_disp, "c1").status == Status.RUNNING

            # ----- EXECUTE/VERIFY phase (on_report, success acceptance) -----
            # Use the ROOT as the executing node (no children) so DONE makes
            # the graph terminal and the gate short-circuits before the heavy
            # re-plan path — the same pattern as the EXEC-VERIFY gate test's
            # ``test_acceptance_done_emits_execute_ok_and_verify_with_no_error``.
            # The on_report success path fires BOTH an ``execute`` row (ok)
            # and a ``verify`` row (accept_pass).
            graph_svc_ev = TaskGraphService()
            graph_ev = graph_svc_ev.initialize_graph(_task_info("guard-ev"))
            _set_running_root(graph_svc_ev, "guard-ev", request_input="req-orig")
            eng_ev = _TrajectoryCaseEngine(
                graph_svc_ev,
                planner=_StubPlanner(),
                dispatcher=_StubDispatcher(),
                runner=_StubRunner(),
                trajectory_repo=repo,
            )
            ev_patch = _patch(
                "guard-ev", "guard-ev", status=Status.DONE,
                acceptance_result=_accept(AcceptanceVerdict.DONE),
            )
            _run(eng_ev.on_report(ev_patch))
            # EXECUTE/VERIFY gate's main logic completes + drives forward: the
            # root transitioned away from RUNNING to a terminal state (engine
            # normalises a DONE+acceptance-DONE root to ``Status.SUCCESS`` for
            # a lone-root graph — the "task completed successfully" terminal;
            # production-coupled but pins the gate's driving logic completed).
            ev_status = graph_svc_ev._get_node(graph_ev, "guard-ev").status
            assert ev_status != Status.RUNNING, (
                f"EXECUTE/VERIFY gate did not drive the node forward; status "
                f"still RUNNING. ev_status={ev_status}"
            )
            assert ev_status in (Status.SUCCESS, Status.DONE), (
                f"EXECUTE/VERIFY gate left the node in an unexpected state "
                f"(expected SUCCESS/DONE terminal); ev_status={ev_status}"
            )

            # ----- RESET phase (on_harness, SLA-timeout) -----
            # Seed a RUNNING child with a start_time 1s ago; the SLA-timeout
            # harness patch (``harness_reset="timeout"``, no exec_error →
            # ``external_harness`` sentinel → action_result=sla_timeout) fires
            # one `reset` trajectory row, then the gate re-dispatches the node
            # back to RUNNING. The raising repo makes the emitter
            # swallow+WARNING; the gate's reset + re-dispatch main logic still
            # completes.
            graph_svc_rst = TaskGraphService()
            graph_rst = graph_svc_rst.initialize_graph(_task_info("guard-rst"))
            _set_running_node(
                graph_svc_rst, "guard-rst", "c1",
                start_time=int(time.time() * 1000) - 1_000,
            )
            eng_rst = _TrajectoryCaseEngine(
                graph_svc_rst,
                planner=_StubPlanner(),
                dispatcher=_StubDispatcher(),
                runner=_StubRunner(),
                trajectory_repo=repo,
            )
            _run(eng_rst.on_harness(
                _patch("guard-rst", "c1", status=Status.PENDING,
                       extend_props_patch={"harness_reset": "timeout"})
            ))
            # RESET gate's main logic completes + drives forward: reset to
            # PENDING then re-dispatched back to RUNNING.
            assert graph_svc_rst._get_node(graph_rst, "c1").status == Status.RUNNING

        # ----- consolidated cross-gate assertions -----

        # 1. Every gate in the span attempted its trajectory emission (the
        #    repo records the call before it raises) — pins that each gate
        #    really did call ``insert_event`` for its action_type. A future
        #    regression that drops a ``_log_trajectory`` call at, say, the
        #    RESET gate surfaces here (the corresponding action_type count
        #    stays 0) rather than by silent omission at the per-gate test.
        for action_type in _TRAJECTORY_ACTION_TYPES:
            assert repo.attempts_by_type.get(action_type, 0) >= 1, (
                f"no emission attempted for action_type={action_type!r}; "
                f"the gate did not call insert_event (决策 #14 regression at "
                f"that gate). attempts_by_type={repo.attempts_by_type}"
            )

        # 2. A WARNING landed on ``logger "task.trajectory"`` for every raised
        #    emission attempt — the emitter's 决策 #14 contract: swallow but
        #    keep the failure visible at WARNING (not DEBUG). The emitter's log
        #    format is ``... action=%s 发射失败:%s`` so each action_type shows
        #    up verbatim as ``action=<type>`` in at least one WARNING message.
        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "task.trajectory" in r.name
        ]
        assert warning_records, (
            "no WARNING records captured on task.trajectory across the span — "
            "the emitter swallowed silently (a 决策 #14 regression: the "
            "visibility half of the contract is missing)"
        )
        for action_type in _TRAJECTORY_ACTION_TYPES:
            needle = f"action={action_type}"
            assert any(needle in r.getMessage() for r in warning_records), (
                f"no WARNING for action_type={action_type!r} "
                f"(expected substring {needle!r} in at least one WARNING "
                f"message); msgs={[r.getMessage() for r in warning_records]}"
            )

        # 3. No exception propagated across the full span — implicit: if any
        #    gate had let the emitter exception bubble (the 决策 #14 regression
        #    the guard is named for), the test body above would have raised
        #    before reaching these consolidated assertions.


# ---------------------------------------------------------------------------
# 2. No-regression invariant — trajectory path stays structurally decoupled
#    from task_action_log / NodeAction (pins the "独立旁路" globally)
# ---------------------------------------------------------------------------


class TestTrajectoryActionLogDecoupling:
    """决策 #14 / spec "风险与边界:发射阻塞正向驱动" pins that the trajectory
    path is a **独立旁路**: the trajectory module stays decoupled from
    ``task_action_log`` / ``NodeAction`` / ``append_action_event`` so a future
    PR can't silently re-couple (e.g., routing the trajectory emission through
    the in-memory graph action_log path). The individual gate tests prove the
    runtime decoupling (their ``_TrajRepo`` captures direct-INSERT calls only,
    never ``append_action_event``); this class pins the STRUCTURAL decoupling
    at the module's AST — no imports of ``NodeAction`` / references to
    ``append_action_event`` / action-log symbols in the trajectory module.
    """

    @staticmethod
    def _module_source(mod) -> str:
        # ``inspect.getsource`` returns the module's source including
        # docstrings (AST ignores docstrings' textual content — they're just
        # string literals to the walker, so prose mentions of ``NodeAction``
        # / ``task_action_log`` do NOT trip the AST-level assertions below).
        return inspect.getsource(mod)

    def test_trajectory_module_does_not_import_or_reference_node_action(self):
        """The trajectory module (``models.py`` + ``payloads.py``) must NOT
        import or reference ``NodeAction`` at the code level. ``NodeAction`` is
        the action-log enum; the trajectory action-type set is the SEPARATE
        ``TrajectoryActionType`` (which includes ``submit``). A future PR that
        adds ``import NodeAction`` to the trajectory module (e.g., to "share"
        the enum between the two paths) re-couples them and breaks this
        assertion. AST-level: no ``Import``/``ImportFrom`` with ``NodeAction``
        as a name + no code-level ``Name`` reference (re-routing through the
        action_log path would need to resolve the enum).
        """
        from agentclaw.community.core.task.task_context.task_trajectory import models, payloads

        for mod in (models, payloads):
            src = self._module_source(mod)
            tree = ast.parse(src)
            # AST-level: no import of NodeAction anywhere in the module
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        assert alias.name != "NodeAction", (
                            f"{mod.__name__} imports NodeAction (from "
                            f"{node.module}) — re-couples the trajectory path "
                            f"to the action-log enum"
                        )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name != "NodeAction", (
                            f"{mod.__name__} imports NodeAction — re-couples "
                            f"the trajectory path to the action-log enum"
                        )
            # AST-level: no code-level Name reference to NodeAction (a
            # re-route through the action_log path would need to resolve the
            # enum by name).
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "NodeAction":
                    raise AssertionError(
                        f"{mod.__name__} references NodeAction at code level "
                        f"(line {node.lineno}) — re-couples the trajectory path "
                        f"to the action-log enum"
                    )

    def test_trajectory_emitter_does_not_invoke_action_log_writer(self):
        """The trajectory emission path (``emit_trajectory_event`` /
        ``emit_submit_trajectory``) must NOT invoke ``append_action_event``
        — the graph method that mutates the in-memory ``action_log`` (the
        ``task_action_log`` backbone). The trajectory side is a direct-INSERT
        to ``task_trajectory_events`` via ``repo.insert_event``; routing it
        through ``append_action_event`` would re-couple to ``task_action_log``.
        AST-level: the emitter + builder function bodies in ``payloads.py``
        make no call to ``append_action_event`` (neither as a bare ``Name``
        call nor as a method ``Attribute`` call like ``self._graph.append_action_event``).
        """
        from agentclaw.community.core.task.task_context.task_trajectory import payloads

        src = self._module_source(payloads)
        tree = ast.parse(src)

        emitter_function_names = {
            "emit_trajectory_event",
            "emit_submit_trajectory",
            "build_trajectory_event_record",
        }
        emitter_function_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name in emitter_function_names
        ]
        assert emitter_function_defs, (
            "expected emit_trajectory_event / emit_submit_trajectory / "
            "build_trajectory_event_record definitions in payloads.py"
        )
        found_names = {fn.name for fn in emitter_function_defs}
        assert found_names == emitter_function_names, (
            f"missing emitter function definitions; found {found_names} "
            f"expected {emitter_function_names}"
        )

        for fn in emitter_function_defs:
            for node in ast.walk(fn):
                # Bare Name call: append_action_event(...)
                if isinstance(node, ast.Name) and node.id == "append_action_event":
                    raise AssertionError(
                        f"{fn.name}() invokes append_action_event (Name) "
                        f"at line {node.lineno} — re-couples trajectory "
                        f"emission to task_action_log (must direct-INSERT "
                        f"via repo.insert_event only)"
                    )
                # Method call: <x>.append_action_event(...)
                if isinstance(node, ast.Attribute) and node.attr == "append_action_event":
                    raise AssertionError(
                        f"{fn.name}() invokes .append_action_event "
                        f"(Attribute) at line {node.lineno} — re-couples "
                        f"trajectory emission to task_action_log (must "
                        f"direct-INSERT via repo.insert_event only)"
                    )

    def test_submit_remains_trajectory_action_type_only_not_node_action(self):
        """Lightweight re-pin (cross-cutting invariant visible in this guard
        file): ``submit`` is a ``TrajectoryActionType`` member ONLY, absent
        from ``NodeAction``. The existing enum-membership tests
        (``test_trajectory_models`` / the SUBMIT gate test) already pin the
        enum shape; this is the guard-file-local assertion so a regression
        surfaces here too, where the structural decoupling is pinned. A future
        PR that adds ``submit`` to ``NodeAction`` (re-coupling the trajectory
        path to the action-log enum) breaks this assertion loudly in this
        guard file.
        """
        from agentclaw.community.core.task.domain.models import NodeAction
        from agentclaw.community.core.task.task_context.task_trajectory.models import (
            TrajectoryActionType,
        )

        node_values = {a.value for a in NodeAction}
        assert "submit" not in node_values, (
            "NodeAction gained a `submit` member — re-couples the trajectory "
            "path to the action-log enum; submit must stay "
            "TrajectoryActionType only (REQ-6: submit is a trajectory action-"
            f"type, NOT a NodeAction). NodeAction values: {node_values}"
        )
        traj_values = {a.value for a in TrajectoryActionType}
        assert "submit" in traj_values, (
            "TrajectoryActionType lost its `submit` member — the SUBMIT gate "
            "would no longer have a distinct action-type. TrajectoryActionType "
            f"values: {traj_values}"
        )
