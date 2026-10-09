"""TDD tests for the EXECUTE/VERIFY trajectory gates (REQ-5).

P3 item 4 of the task-trajectory spec. The EXECUTE/VERIFY gates live in
``CentralizedExecutionAdapter.on_report`` (engine.py:~1907/1916/1926 — the three sites where
``_log_action(NodeAction.EXECUTE/VERIFY, ...)`` fires). They must fire **additive**
``TrajectoryActionType.EXECUTE`` / ``TrajectoryActionType.VERIFY`` trajectory rows
with:

    error_type   = ReasonCatalog mapped from the surfaced ``_exec_error_origin``:
                     bot_interface    → underlying_interface_error
                     parse            → parse_error
                     terminal_invalid → terminal_invalid
                     transport        → transport_error
                     success / no-origin → None
    error_msg    = ``patch.exec_error`` truncated ≤500 (EXECUTE(err) path only;
                   None for EXECUTE(ok) / VERIFY)
    action_input = the downstream request原文 sent to the bot — read defensively from
                   ``node.run_info.extend_props["_exec_request_input"]`` (NOT
                   truncated; None when absent). 生产侧该 key 由 executor 投递时落
                   节点;gate 仅消费,缺失即 None(不阻塞闸门)。
    ext_info     = ``{"interface_error_code": <code|None>}`` if an interface error
                   code is available on the patch; else None
    status_from / status_to / attempt mirror ``_log_action(EXECUTE/VERIFY)``.

Origin surfacing (Part A): ``callback_adapter.CallbackAdapter`` classifies the
failure path via ``_classify_exec_error_origin`` and writes the origin onto
``TaskNodePatch.extend_props_patch["_exec_error_origin"]`` at the failure patch
paths (``bot_interface`` at the exec_error path, ``terminal_invalid`` at the
non-bool-success / failed-no-gaps paths). ``parse`` (ingest_parse_error) and
``transport`` (plan/dispatch HTTP-layer exception) do NOT build a TaskNodePatch
in the adapter — the classifier classifies them, the gate maps them; they are
covered at the helper-unit + gate-mapping level (gate-level coverage limit
noted, mirroring the spec's allowed fallback for transport).

Invariants the tests pin (cross-cutting with the task constraints):
    * ``_log_action(NodeAction.EXECUTE/VERIFY, ...)`` call stays byte-unchanged
      (additive ``_log_trajectory`` alongside, NOT a replacement); verified by
      asserting the node's ``action_log`` entry for EXECUTE/VERIFY is present and
      shape-faithful AND the trajectory row is additive.
    * ``NodeAction`` enum / ``append_action_event`` / ``task_action_log``
      untouched (the trajectory row goes to the ``_TrajRepo`` fake via
      ``emit_trajectory_event`` direct-INSERT only).
    * Zero intrusion: forward-driving path completes even when the trajectory
      repo raises or a hostile origin/request read raises (the swallow guarantee
      lives in the emitter + the gate's defensive reads; main logic NOT
      swallowed — 决策 #14).
"""
from __future__ import annotations

from tests.community.core.task.task_trajectory._task_context_support import _tcs

import asyncio
import json
import logging


from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskCallbackData,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_runner.execution_adapters import CentralizedExecutionAdapter
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
    EXEC_ERROR_ORIGIN_BOT_INTERFACE,
    EXEC_ERROR_ORIGIN_PARSE,
    EXEC_ERROR_ORIGIN_TERMINAL_INVALID,
    EXEC_ERROR_ORIGIN_TRANSPORT,
    TaskLoopCallback,
    _classify_exec_error_origin,
)
from agentclaw.community.core.task.task_runner.client.ports import BotSendResult
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
    TaskExecutor,
)


# ---------------------------------------------------------------------------
# Shared helpers / fakes
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
        owner_bot_id="b1",
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
    return AcceptanceResult(
        verdict=verdict,
        done_items=[],
        gap_items=gaps or [],
    )


def _data(loop_task_id: str = "t1::c1", *,
          success: object = True,
          data=None,
          fail_detail: str | None = None,
          exec_error: str | None = None,
          ext_info: dict | None = None) -> TaskCallbackData:
    """Build a poller-shape TaskCallbackData (``result`` is a dict with
    ``success`` / ``exec_error`` / etc.) for driving ``CallbackAdapter.adapt``.
    """
    result: dict = {}
    if success is not True or exec_error is not None or data is not None or fail_detail is not None:
        result["success"] = success
    if data is not None:
        result["data"] = data
    if fail_detail is not None:
        result["fail_detail"] = fail_detail
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


class _TrajRepo:
    """Fake trajectory repo — captures every ``insert_event`` call (no SQLite)."""

    def __init__(self) -> None:
        self.records: list[TrajectoryEventRecord] = []
        self.calls = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self.calls += 1
        self.records.append(record)
        return record


class _BoomRepo:
    """Repo whose ``insert_event`` always raises — pins 决策 #14 (emission is
    swallowed + WARNING; the gate's main driving logic is NOT masked)."""

    def __init__(self) -> None:
        self.calls = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self.calls += 1
        raise RuntimeError("trajectory repo boom")


class _StubPlanner:
    async def plan(self, graph, target_node_id=None):
        from agentclaw.community.core.task.domain.models import PlanResult
        return PlanResult(children=[], has_gap=True)


class _StubDispatcher:
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
    """Mirrors the existing ``StubRunner`` / the RESET test's stub: ``_drain``
    calls ``start_run(toDoTaskList) -> list[bool]`` + ``form_coop_group``."""

    def __init__(self):
        self.run_calls: list[list[TaskNode]] = []

    async def start_run(self, toDoTaskList):
        self.run_calls.append(list(toDoTaskList))
        return [True] * len(toDoTaskList)

    async def form_coop_group(self, gf):
        return "grp_stub"


class _TrajectoryCaseEngine(CentralizedExecutionAdapter):
    """Test subclass — injects stubs + a trajectory repo (mirrors the RESET
    / DISPATCH / PLAN gate test subclasses)."""

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


def _execute_records(repo) -> list[TrajectoryEventRecord]:
    return [r for r in repo.records if r.action_type == "execute"]


def _verify_records(repo) -> list[TrajectoryEventRecord]:
    return [r for r in repo.records if r.action_type == "verify"]


def _set_running_node(svc, task_id, node_id, *, run_mode: str = "single_bot",
                      assignee: str = "b", start_time: int | None = None,
                      harness_retries: int = 0,
                      request_input: str | None = None) -> None:
    """Preset a node into RUNNING (with optional harness_retries / exec request
    input on extend_props). Seeded via the graph service so both the engine and
    the gate's defensive read observe the same live in-memory node."""
    svc.add_task_nodes([_child(node_id, task_id)], parent_node_id=task_id)
    ep: dict = {"harness_retries": harness_retries}
    if request_input is not None:
        ep["_exec_request_input"] = request_input
    svc.update_task_node_info(
        _patch(task_id, node_id, status=Status.RUNNING, run_mode=run_mode,
               assignee=assignee, start_time=start_time, extend_props_patch=ep)
    )


def _set_running_root(svc, task_id, *, run_mode: str = "single_bot",
                      assignee: str = "b", start_time: int | None = None,
                      harness_retries: int = 0,
                      request_input: str | None = None) -> None:
    """Like ``_set_running_node`` but on the task root itself (no parent)."""
    ep: dict = {"harness_retries": harness_retries}
    if request_input is not None:
        ep["_exec_request_input"] = request_input
    svc.update_task_node_info(
        _patch(task_id, task_id, status=Status.RUNNING, run_mode=run_mode,
               assignee=assignee, start_time=start_time, extend_props_patch=ep)
    )


# ---------------------------------------------------------------------------
# Part A — _classify_exec_error_origin + CallbackAdapter surfaces origin
# ---------------------------------------------------------------------------


class TestClassifyExecErrorOrigin:
    """REQ-5 helper: ``_classify_exec_error_origin`` maps a callback's failure
    mode to one of four origins. Pure function, no IO."""

    def test_bot_interface_when_exec_error_set(self):
        assert _classify_exec_error_origin(bot_interface_error="boom") == EXEC_ERROR_ORIGIN_BOT_INTERFACE

    def test_terminal_invalid_non_bool_success(self):
        assert _classify_exec_error_origin(success="false") == EXEC_ERROR_ORIGIN_TERMINAL_INVALID

    def test_terminal_invalid_missing_success(self):
        assert _classify_exec_error_origin(success=None) == EXEC_ERROR_ORIGIN_TERMINAL_INVALID

    def test_terminal_invalid_failed_without_gaps(self):
        assert _classify_exec_error_origin(success=False, has_gaps=False) == EXEC_ERROR_ORIGIN_TERMINAL_INVALID

    def test_parse_when_parse_failure_flag(self):
        assert _classify_exec_error_origin(parse_failure=True) == EXEC_ERROR_ORIGIN_PARSE

    def test_transport_when_transport_failure_flag(self):
        assert _classify_exec_error_origin(transport_failure=True) == EXEC_ERROR_ORIGIN_TRANSPORT

    def test_success_passes_no_exec_error_origin(self):
        # success=True (execution produced output) — no exec-error origin.
        assert _classify_exec_error_origin(success=True) is None

    def test_failed_with_gaps_passes_no_exec_error_origin(self):
        # success=False WITH gaps → acceptance FAIL (verdict=FAILED), NOT a
        # terminal_invalid; no exec-error origin (acceptance_failed is separate).
        assert _classify_exec_error_origin(success=False, has_gaps=True) is None

    def test_precedence_parse_over_others(self):
        # parse_failure wins even if other signals present (explicit flag).
        assert _classify_exec_error_origin(
            parse_failure=True, bot_interface_error="x", success="bad"
        ) == EXEC_ERROR_ORIGIN_PARSE

    def test_precedence_transport_over_bot_interface(self):
        assert _classify_exec_error_origin(
            transport_failure=True, bot_interface_error="x"
        ) == EXEC_ERROR_ORIGIN_TRANSPORT

    def test_precedence_bot_interface_over_terminal_invalid(self):
        # On the exec_error path the adapter passes bot_interface_error; the
        # (None-by-default) success MUST NOT downgrade this to terminal_invalid.
        assert _classify_exec_error_origin(
            bot_interface_error="boom", success=None
        ) == EXEC_ERROR_ORIGIN_BOT_INTERFACE


class TestAdapterSurfacesExecErrorOrigin:
    """REQ-5 Part A: ``CallbackAdapter.adapt`` writes ``_exec_error_origin`` onto
    ``patch.extend_props_patch`` at the failure patch paths."""

    def test_bot_interface_exec_error_path_surfaces_origin(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=True, exec_error="底层接口 boom"))
        assert patch.exec_error == "底层接口 boom"
        assert patch.status == Status.FAILED
        assert patch.extend_props_patch is not None
        assert patch.extend_props_patch["_exec_error_origin"] == "bot_interface"

    def test_bot_interface_preserves_existing_ext_info(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=True, exec_error="boom",
                                     ext_info={"k": "v", "interface_error_code": 42}))
        # existing _ext_info keys are preserved alongside the new origin key
        assert patch.extend_props_patch["k"] == "v"
        assert patch.extend_props_patch["interface_error_code"] == 42
        assert patch.extend_props_patch["_exec_error_origin"] == "bot_interface"

    def test_terminal_invalid_non_bool_success_surfaces_origin(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success="false"))  # non-bool → terminal_invalid
        assert patch.exec_error == "terminal_result_invalid: success must be bool"
        assert patch.extend_props_patch is not None
        assert patch.extend_props_patch["_exec_error_origin"] == "terminal_invalid"

    def test_terminal_invalid_failed_without_gaps_surfaces_origin(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=False))  # no gaps → terminal_invalid
        assert patch.exec_error == "terminal_result_invalid: failed result requires gaps"
        assert patch.extend_props_patch is not None
        assert patch.extend_props_patch["_exec_error_origin"] == "terminal_invalid"

    def test_success_path_does_not_set_origin(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=True, data="ok"))
        assert patch.exec_error is None
        # success path → no origin key (None or absent); error means no origin
        assert not (patch.extend_props_patch or {}).get("_exec_error_origin")

    def test_acceptance_failed_with_gaps_does_not_set_origin(self):
        """success=False WITH gaps → acceptance FAILED (verdict=FAILED), which is
        NOT an exec-error origin (acceptance_failed is a separate ReasonCatalog).
        The adapter MUST NOT set ``_exec_error_origin`` here."""
        adapter = CallbackAdapter()
        patch = adapter.adapt(_data(success=False, fail_detail="gap1"))
        assert patch.acceptance_result is not None
        assert patch.acceptance_result.verdict == AcceptanceVerdict.FAILED
        assert not (patch.extend_props_patch or {}).get("_exec_error_origin")


# ---------------------------------------------------------------------------
# Part B — EXECUTE/VERIFY gate emits trajectory (origin → ReasonCatalog)
# ---------------------------------------------------------------------------


class TestExecuteVerifyGateOriginMapping:
    """REQ-5 Part B: the EXECUTE/VERIFY gates map the surfaced origin to a
    ReasonCatalog and emit an additive trajectory row."""

    def test_bot_interface_exec_error_maps_to_underlying_interface_error(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99,  # >= MAX_HARNESS → HUNG (no re-dispatch)
                          request_input="bot-request-payload")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        # Build the patch via the real adapter so Part A is exercised end-to-end.
        patch = CallbackAdapter().adapt(
            _data(loop_task_id="t1::c1", success=True, exec_error="底层接口 boom",
                   ext_info={"interface_error_code": 42})
        )
        _run(eng.on_report(patch))

        rows = _execute_records(repo)
        assert len(rows) == 1, [(r.action_type, r.action_result) for r in repo.records]
        rec = rows[0]
        assert rec.action_type == "execute"
        assert rec.action_result == "failed"
        assert rec.error_type == "underlying_interface_error"
        assert rec.error_msg is not None
        assert "底层接口 boom" in rec.error_msg
        # error_msg truncated ≤500
        assert len(rec.error_msg) <= 500
        # action_input = request原文 (untruncated; read defensively from node)
        assert rec.action_input == "bot-request-payload"
        # ext_info carries the surfaced interface_error_code
        assert rec.ext_info is not None
        payload = json.loads(rec.ext_info)
        assert payload["schema_v"] == 1
        assert payload["interface_error_code"] == "42"  # coerced to str
        # origin is restorable from the patch's extend_props (Part A contract)
        assert patch.extend_props_patch["_exec_error_origin"] == "bot_interface"

    def test_long_exec_error_msg_is_truncated_to_500(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        long_msg = "x" * 2000
        patch = CallbackAdapter().adapt(_data(loop_task_id="t1::c1", success=True,
                                                exec_error=long_msg))
        _run(eng.on_report(patch))

        rec = _execute_records(repo)[0]
        assert rec.error_type == "underlying_interface_error"
        assert rec.error_msg is not None
        assert len(rec.error_msg) <= 500  # truncated
        assert "..." in rec.error_msg  # mirror the PLAN-gate truncation convention

    def test_terminal_invalid_non_bool_success_maps_to_terminal_invalid(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99,
                          request_input="req-orig")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        # non-bool success → terminal_invalid (adapter surfaces origin)
        patch = CallbackAdapter().adapt(_data(loop_task_id="t1::c1", success="false"))
        _run(eng.on_report(patch))

        rec = _execute_records(repo)[0]
        assert rec.error_type == "terminal_invalid"
        assert rec.error_msg is not None
        assert "terminal_result_invalid" in rec.error_msg
        assert rec.action_input == "req-orig"
        assert patch.extend_props_patch["_exec_error_origin"] == "terminal_invalid"

    def test_terminal_invalid_failed_no_gaps_maps_to_terminal_invalid(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = CallbackAdapter().adapt(_data(loop_task_id="t1::c1", success=False))
        _run(eng.on_report(patch))

        rec = _execute_records(repo)[0]
        assert rec.error_type == "terminal_invalid"
        assert "terminal_result_invalid" in rec.error_msg

    def test_parse_origin_maps_to_parse_error_at_gate(self):
        """``parse`` origin: ingest_parse_error does NOT build a TaskNodePatch (it
        never reaches the engine gate), so this is covered at the
        helper-unit + gate-mapping level: a gate processing a patch carrying
        ``_exec_error_origin="parse"`` emits ``error_type=parse_error``.

        Gate-level coverage limit: in production no patch carries ``parse`` today
        (ingest_parse_error bypasses on_report); the gate's mapping is uniform
        with the other origins in ``_EXEC_ERROR_ORIGIN_TO_REASON``."""
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        # Construct a patch carrying the parse origin (an adapter surfacing it
        # in a future path would set exec_error + this origin).
        patch = _patch("t1", "c1", status=Status.FAILED, exec_error="parse boom",
                       extend_props_patch={"_exec_error_origin": "parse"})
        _run(eng.on_report(patch))

        rec = _execute_records(repo)[0]
        assert rec.error_type == "parse_error"
        assert "parse boom" in rec.error_msg

    def test_transport_origin_maps_to_transport_error_at_gate(self):
        """``transport`` origin: plan/dispatch HTTP-layer exceptions aren't a bot
        callback per se; no adapter path surfaces them today. Covered at
        helper-unit + gate-mapping level (gate-level coverage limit noted, per
        tasks.md REQ-5 transport allowance)."""
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = _patch("t1", "c1", status=Status.FAILED, exec_error="dispatch http boom",
                       extend_props_patch={"_exec_error_origin": "transport"})
        _run(eng.on_report(patch))

        rec = _execute_records(repo)[0]
        assert rec.error_type == "transport_error"
        assert "dispatch http boom" in rec.error_msg

    def test_unknown_origin_value_falls_back_to_none_error_type(self):
        """A surfaced origin that isn't one of the 4 known values must NOT raise;
        ``error_type`` falls back to None (defensive — a future / foreign origin)."""
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = _patch("t1", "c1", status=Status.FAILED, exec_error="mystery",
                       extend_props_patch={"_exec_error_origin": "not_a_real_origin"})
        _run(eng.on_report(patch))

        rec = _execute_records(repo)[0]
        assert rec.error_type is None  # unmapped origin → None (no raise)
        assert rec.error_msg is not None  # exec_error still carried


class TestExecuteVerifyGateSuccessPath:
    """REQ-5 Part B: a successful execute / verify emits trajectory rows with
    ``error_type=None`` / ``error_msg=None`` (no error → no origin)."""

    def test_acceptance_done_emits_execute_ok_and_verify_with_no_error(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        # Use the ROOT "t1" as the executing node (no children) so DONE makes the
        # graph terminal and the gate short-circuits before the heavy re-plan
        # path. Only EXECUTE(ok) + VERIFY trajectory rows should fire here.
        _set_running_root(svc, "t1", request_input="root-req-payload")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = _patch("t1", "t1", status=Status.DONE,
                       acceptance_result=_accept(AcceptanceVerdict.DONE))
        _run(eng.on_report(patch))

        ex = _execute_records(repo)
        vf = _verify_records(repo)
        assert len(ex) == 1
        assert len(vf) == 1
        assert ex[0].action_result == "success"
        assert ex[0].error_type is None
        assert ex[0].error_msg is None
        assert ex[0].action_input == "root-req-payload"
        assert vf[0].action_result == "accept_pass"
        assert vf[0].error_type is None
        assert vf[0].error_msg is None
        assert vf[0].action_input == "root-req-payload"

    def test_acceptance_fail_verify_emits_accept_fail_error_none(self):
        """acceptance FAIL → VERIFY action_result=accept_fail (the acceptance
        verdict is captured in action_result, NOT error_type; acceptance_failed
        is a separate ReasonCatalog outside REQ-5's 4 exec-error origins)."""
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        _set_running_root(svc, "t1", request_input="r")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = _patch("t1", "t1", status=Status.DONE,
                       acceptance_result=_accept(AcceptanceVerdict.FAILED, gap_items=["g1"]))
        _run(eng.on_report(patch))

        vf = _verify_records(repo)
        assert len(vf) == 1
        assert vf[0].action_result == "accept_fail"
        assert vf[0].error_type is None  # acceptance failure is not an exec origin
        assert vf[0].error_msg is None


# ---------------------------------------------------------------------------
# 5. action_input carries the request原文 (untruncated)
# ---------------------------------------------------------------------------


class TestActionInputRequestOriginal:
    """REQ-1/REQ-5: execute/verify ``action_input`` = 下发请求原文 (NOT truncated)."""

    def test_long_request_input_is_not_truncated(self):
        """A request原文 longer than 500 chars flows into ``action_input``
        verbatim (the only field that is NOT truncated — contrast ``error_msg``
        which IS truncated to ≤500)."""
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        long_req = "P" * 2000
        _set_running_node(svc, "t1", "c1", harness_retries=99,
                          request_input=long_req)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = CallbackAdapter().adapt(_data(loop_task_id="t1::c1", success=True,
                                                 exec_error="boom"))
        _run(eng.on_report(patch))

        rec = _execute_records(repo)[0]
        assert rec.action_input == long_req  # NOT truncated — full 2000 chars
        assert len(rec.action_input) == 2000

    def test_request_input_absent_yields_none_action_input(self):
        """When no ``_exec_request_input`` was surfaced on the node (production
        today: the executor does not yet write it), the gate reads None
        defensively — the row still fires with ``action_input=None``."""
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99)  # no request_input
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = CallbackAdapter().adapt(_data(loop_task_id="t1::c1", success=True,
                                                 exec_error="boom"))
        _run(eng.on_report(patch))

        rec = _execute_records(repo)[0]
        assert rec.action_input is None  # request not reachable → None (no raise)
        # other fields still collected
        assert rec.error_type == "underlying_interface_error"


# ---------------------------------------------------------------------------
# 7. _log_action(EXECUTE/VERIFY) byte-unchanged + additive trajectory
# ---------------------------------------------------------------------------


class TestLogActionUnchangedAndAdditive:
    """决策 #14 / spec invariant: ``_log_action(NodeAction.EXECUTE/VERIFY, ...)``
    fires byte-unchanged; the trajectory row is ADDITIVE (separate row, separate
    sink). ``NodeAction`` enum / ``append_action_event`` / ``task_action_log``
    are untouched — the trajectory row goes to ``_TrajRepo`` only."""

    def test_execute_err_action_log_entry_present_and_shape_faithful(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = CallbackAdapter().adapt(_data(loop_task_id="t1::c1", success=True,
                                                 exec_error="boom"))
        _run(eng.on_report(patch))

        # action_log: the existing _log_action(NodeAction.EXECUTE) entry is present
        node = svc._get_node(graph, "c1")
        exec_entries = [
            ev for ev in node.run_info.action_log
            if ev.action.value == "execute"
        ]
        assert exec_entries, [ev.action.value for ev in node.run_info.action_log]
        # payload shape mirrors the byte-unchanged _log_action call — the exec_error
        # adapter path carries no output_patch, so the action_log ``output`` is {}.
        p = exec_entries[0].payload
        assert p["success"] is False
        assert p["exec_error"] == "boom"
        assert p["output"] == {}
        # AND the trajectory row is additive (separate sink — _TrajRepo)
        assert len(_execute_records(repo)) == 1
        assert _execute_records(repo)[0].error_type == "underlying_interface_error"

    def test_verify_action_log_entry_present_and_shape_faithful(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        _set_running_root(svc, "t1")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = _patch("t1", "t1", status=Status.DONE,
                       acceptance_result=_accept(AcceptanceVerdict.FAILED, gap_items=["g1"]))
        _run(eng.on_report(patch))

        node = svc._get_node(graph, "t1")
        verify_entries = [
            ev for ev in node.run_info.action_log
            if ev.action.value == "verify"
        ]
        # exactly ONE verify action_log entry (byte-unchanged single _log_action(VERIFY))
        assert len(verify_entries) == 1
        p = verify_entries[0].payload
        assert p["verdict"] == "FAILED"
        assert p["gaps"] == ["g1"]
        # AND the trajectory VERIFY row is additive (one row)
        assert len(_verify_records(repo)) == 1

    def test_node_action_enum_unchanged(self):
        """NodeAction must still be the original 6-member enum (no `submit` /
        no trajectory members added — submit lives in TrajectoryActionType)."""
        from agentclaw.community.core.task.domain.models import NodeAction
        members = {m.value for m in NodeAction}
        assert members == {"plan", "dispatch", "execute", "verify", "reset", "transition"}
        assert "submit" not in members  # submit is TrajectoryActionType only


# ---------------------------------------------------------------------------
# 8. Defensive / 决策 #14 scope (hostile reads + raising emitter)
# ---------------------------------------------------------------------------


class _HostileDict(dict):
    """A dict subclass that raises on ``.get("_exec_error_origin")`` — pins that
    the gate's origin read is defensive (one raising read → that field None,
    the row still fires with the other collected fields)."""

    def get(self, key, default=None):  # type: ignore[override]
        if key == "_exec_error_origin":
            raise RuntimeError("hostile origin read boom")
        return super().get(key, default)


class TestDefensiveAndDecision14:
    """决策 #14: the trajectory EMISSION + the origin/request reads at the gate
    are swallowed/defensive; the gate's main driving logic (status mutation,
    patch application, harness reset) is NOT swallowed."""

    def test_hostile_origin_read_still_fires_row_with_error_type_none(self):
        """A patch whose ``_exec_error_origin`` read raises → the origin read is
        defensive (None); the EXECUTE(err) row still fires with error_type=None
        (the hostile origin couldn't be mapped), error_msg + action_input still
        collected. The gate completes (node driven to harness/HUNG)."""
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99,
                          request_input="req")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        # Hostile extend_props_patch: .get("_exec_error_origin") raises; the
        # request_input key still reads fine (different key path / different dict).
        hostile = _HostileDict({"_exec_request_input_note": "irrelevant"})
        patch = _patch("t1", "c1", status=Status.FAILED, exec_error="boom",
                       extend_props_patch=hostile)
        _run(eng.on_report(patch))

        rec = _execute_records(repo)
        assert len(rec) == 1  # the row still fired (hostile read didn't kill it)
        assert rec[0].error_type is None  # hostile origin → unmapped → None
        assert rec[0].error_msg is not None  # exec_error still carried (truncated)
        assert "boom" in rec[0].error_msg
        assert rec[0].action_input == "req"  # request still collected from node

    def test_raising_emitter_is_swallowed_and_warns(self, caplog):
        """A trajectory repo whose insert_event raises → the emitter swallows
        + logs WARNING (决策 #14); the gate's main driving logic completes (node
        driven), and ``_log_action(EXECUTE)`` is NOT masked (action_log entry
        still present)."""
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99)
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=_BoomRepo(),
        )
        patch = CallbackAdapter().adapt(_data(loop_task_id="t1::c1", success=True,
                                                 exec_error="boom"))
        with caplog.at_level(logging.WARNING, logger="task.trajectory"):
            _run(eng.on_report(patch))

        # gate main logic completed: node was driven (harness_max → HUNG)
        node = svc._get_node(graph, "c1")
        assert node.status == Status.HUNG
        # _log_action(EXECUTE) NOT masked — action_log entry present
        exec_entries = [ev for ev in node.run_info.action_log
                        if ev.action.value == "execute"]
        assert exec_entries, " EXECUTE action_log entry must survive a raising emitter"
        # WARNING logged (observable, not DEBUG)
        warnings = [r for r in caplog.records
                   if r.levelno == logging.WARNING and "task.trajectory" in r.name]
        assert any("boom" in r.getMessage() for r in warnings), \
            [r.getMessage() for r in caplog.records]

    def test_trajectory_repo_none_still_drives_gate(self):
        """``trajectory_repo=None`` (engine without a trajectory repo — lightweight
        DI / tests) → emitter no-ops; the gate completes and _log_action fires."""
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", harness_retries=99)
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=None,
        )
        patch = CallbackAdapter().adapt(_data(loop_task_id="t1::c1", success=True,
                                                 exec_error="boom"))
        _run(eng.on_report(patch))  # must NOT raise

        node = svc._get_node(graph, "c1")
        assert node.status == Status.HUNG  # gate main logic completed
        assert [ev for ev in node.run_info.action_log if ev.action.value == "execute"]


# ---------------------------------------------------------------------------
# Issue 1 (prod gap) — executor surfaces _exec_request_input onto the node
# ---------------------------------------------------------------------------


class _ExecBot:
    """Minimal OpenApiBotPort stub: ``send_message`` returns a BotSendResult;
    ``ensure_grant`` no-op (the executor may call it)."""

    def __init__(self):
        self.sent = []

    async def ensure_grant(self, bot_id):
        return None

    async def send_message(self, *, bot_id, message, metadata):
        self.sent.append((bot_id, message, metadata))
        return BotSendResult(run_id="rid_stub", session_id="sid_stub")


class _LongFormatter:
    """Stub PromptFormatter returning a fixed long request原文 (>500 chars) to
    assert the gate carries it verbatim (NOT truncated)."""

    def __init__(self, payload: str):
        self._payload = payload

    def format_execute(self, ctx, node):
        return self._payload


class _ExecCtx:
    def build(self, task_id, node_id):
        return {"mode": "execute", "node_spec": None}


class TestExecutorSurfacesExecRequestInput:
    """Issue 1: ``TaskExecutor._dispatch_single_bot`` writes the downstream
    request原文 (``message``) onto the dispatched node's
    ``run_info.extend_props["_exec_request_input"]`` — persisted via
    ``update_task_node_info`` (the same extend_props seam that carries
    group_id/session_id/run_id, survives restart) — so the engine EXECUTE/VERIFY
    gate can carry it as ``action_input`` (request原文, NOT truncated)."""

    def test_executor_surfaces_long_request_input_then_gate_carries_it_verbatim(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", run_mode="single_bot", assignee="bot9:ent1")
        long_req = "Q" * 2000  # >500 chars to assert NOT truncated
        bot = _ExecBot()
        exe = TaskExecutor(
            bot=bot, bcs=None, formatter=_LongFormatter(long_req),
            context=_ExecCtx(), sink=None, poller=None, graph=svc,
            task_settings=None,  # skill_report defaults True → no poller.register
        )
        node = svc._get_node(graph, "c1")
        ok = _run(exe.dispatch([node]))
        assert ok == [True]
        # the bot was actually sent the long request原文
        assert bot.sent[0][1] == long_req
        # the executor persisted _exec_request_input onto the node (non-truncated, persisted)
        updated = svc._get_node(graph, "c1")
        assert updated.run_info.extend_props["_exec_request_input"] == long_req
        assert len(updated.run_info.extend_props["_exec_request_input"]) == 2000

        # the engine EXECUTE(err) gate reads the surfaced request原文 as action_input
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = CallbackAdapter().adapt(
            _data(loop_task_id="t1::c1", success=True, exec_error="底层接口 boom")
        )
        _run(eng.on_report(patch))
        rec = _execute_records(repo)[0]
        assert rec.action_input == long_req  # the gate read the surfaced request原文
        assert len(rec.action_input) == 2000  # NOT truncated

    def test_executor_send_failure_does_not_surface_request_input(self):
        """If ``send_message`` raises a non-retryable OpenAPI error, the dispatch
        returns False early (before the persist point) → no ``_exec_request_input``
        is written; the gate later reads None. Asserts the persist does NOT happen
        on a failed dispatch (defensive: a failed send must not leak a request原文)."""
        from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
            OpenApiBadRequestError,
        )

        class _FailBot(_ExecBot):
            async def send_message(self, *, bot_id, message, metadata):
                raise OpenApiBadRequestError("400")

        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        _set_running_node(svc, "t1", "c1", run_mode="single_bot", assignee="bot9:ent1")
        exe = TaskExecutor(
            bot=_FailBot(), bcs=None, formatter=_LongFormatter("Q" * 600),
            context=_ExecCtx(), sink=None, poller=None, graph=svc, task_settings=None,
        )
        node = svc._get_node(graph, "c1")
        ok = _run(exe.dispatch([node]))
        assert ok == [False]
        # no _exec_request_input written (the dispatch bailed before the persist point)
        updated = svc._get_node(graph, "c1")
        assert "_exec_request_input" not in updated.run_info.extend_props


# ---------------------------------------------------------------------------
# Issue 2a (prod gap) — ingest_parse_error emits a parse_error trajectory row
# ---------------------------------------------------------------------------


class _CapturingTrajRepo:
    """Fake trajectory repo — captures insert_event calls (no SQLite)."""

    def __init__(self):
        self.records: list[TrajectoryEventRecord] = []

    def insert_event(self, record):
        self.records.append(record)
        return record


class _StubCallbackRepo:
    """Fake callback repo — records upsert / upsert_error calls."""

    def __init__(self):
        self.upserted = []

    def upsert_error(self, rec):
        self.upserted.append(rec)

    def upsert(self, rec):
        self.upserted.append(rec)


class TestIngestParseErrorEmitsParseTrajectory:
    """Issue 2a: ``ingest_parse_error`` never reaches ``engine.on_report`` (no patch)
    so the EXECUTE/VERIFY gate can't classify it. ``ingest_parse_error`` now fires a
    ``parse_error`` trajectory event directly via the injected trajectory emitter
    (optional dependency; None → no-op)."""

    def test_ingest_parse_error_emits_parse_error_trajectory_with_callback_repo(self):
        repo = _CapturingTrajRepo()
        cb_repo = _StubCallbackRepo()
        cb = TaskLoopCallback(
            CallbackAdapter(), engine=None, callback_repo=cb_repo,
            task_context_service=_tcs(repo),
        )
        raw = {"flow_id": "flow-99",
               "ext_info": {"flow_runs": {"origin_session_id": "S-9"}}}
        _run(cb.ingest_parse_error(raw, "parse boom: malformed json"))
        # exactly one parse_error trajectory row
        assert len(repo.records) == 1
        rec = repo.records[0]
        assert rec.action_type == "execute"
        assert rec.action_result == "parse_fail"
        assert rec.error_type == "parse_error"
        assert rec.error_msg is not None
        assert "parse boom" in rec.error_msg
        assert rec.error_msg == "parse boom: malformed json"  # ≤500, untruncated
        assert rec.action_input is None  # request原文 not in scope for parse
        assert rec.ext_info is not None
        payload = json.loads(rec.ext_info)
        assert payload["schema_v"] == 1
        assert "parse_error" in payload
        assert payload["parse_error"] == "parse boom: malformed json"
        assert rec.status_to == "FAILED"
        # the audit record write (main logic) ALSO happened and is NOT masked
        assert len(cb_repo.upserted) == 1
        assert cb_repo.upserted[0].exec_error == "parse boom: malformed json"

    def test_ingest_parse_error_fires_trajectory_even_without_callback_repo(self):
        """The parse trajectory fires regardless of whether the audit repo is
        present (the parse error happened; the trajectory records it for root-cause
        even when audit is skipped)."""
        repo = _CapturingTrajRepo()
        cb = TaskLoopCallback(
            CallbackAdapter(), engine=None, callback_repo=None,
            task_context_service=_tcs(repo),
        )
        _run(cb.ingest_parse_error({"flow_id": "f1"}, "boom"))
        assert len(repo.records) == 1
        assert repo.records[0].error_type == "parse_error"

    def test_ingest_parse_error_with_no_trajectory_repo_is_noop(self):
        """``trajectory_repo=None`` (not injected) → emitter no-ops; the audit
        record write still completes; no raise."""
        cb_repo = _StubCallbackRepo()
        cb = TaskLoopCallback(
            CallbackAdapter(), engine=None, callback_repo=cb_repo,
            task_context_service=None,
        )
        _run(cb.ingest_parse_error({"flow_id": "f1"}, "parse boom"))  # must NOT raise
        assert len(cb_repo.upserted) == 1  # audit still wrote
        # no trajectory repo → no trajectory row (no raise)

    def test_ingest_parse_error_long_error_msg_is_truncated(self):
        """``error_msg`` is truncated ≤500 (mirrors the gate / PLAN-gate convention)."""
        repo = _CapturingTrajRepo()
        cb = TaskLoopCallback(
            CallbackAdapter(), engine=None, callback_repo=_StubCallbackRepo(),
            task_context_service=_tcs(repo),
        )
        long_err = "E" * 2000
        _run(cb.ingest_parse_error({"flow_id": "f1"}, long_err))
        rec = repo.records[0]
        assert rec.error_msg is not None
        assert len(rec.error_msg) <= 500
        assert "..." in rec.error_msg


# ---------------------------------------------------------------------------
# Part D — static-plan branch EXECUTE/VERIFY trajectory gate (I-2)
# ---------------------------------------------------------------------------


_STATIC_PLAN_YAML_TRAJ = """
template_id: traj_static_test
entry_bot_id: entry-bot
nodes:
  - id: worker
    type: bot
    bot_id: worker-bot
    input: {task: $.input.task}
    output: {result: $.result}
"""


def _static_task_info(task_id: str = "t1") -> TaskInfo:
    """TaskInfo with a minimal static-plan yaml (single ``worker`` node) so the
    engine's ``_static_runtime`` returns a non-None runtime and the static-plan
    branch in ``on_report`` fires (mirrors the real dispatch + ``_static_auto_report``
    fallback flow's entrypoint)."""
    cfg = {
        "MAX_DEPTH": 3, "BBS_MAX_DEPTH": 3,
        "task_type": "static_plan",
        "static_plan_yaml": _STATIC_PLAN_YAML_TRAJ,
    }
    return TaskInfo(task_id=task_id,
        task_spec=TaskSpec(

            context=Context(background="bg", title="T"),
            goal=Goal(
                objective="o",
                acceptances=[AcceptanceCriteria(id="ac1", description="d")],
            ),
        ),
        source_type="bot",
        owner_bot_id="entry-bot",
        execution_config=cfg,
    )


class TestStaticPlanExecuteVerifyTrajectoryGate:
    """I-2: the static-plan branch in ``engine.on_report`` (which handles BOTH
    real bot callbacks — static-plan dispatch uses the SAME harness contract
    as dynamic tasks per the L2084 comment "Static plans use the same harness
    contract as dynamic tasks" — AND the ``_static_auto_report`` 80s mock
    fallback) must fire EXECUTE/IFY trajectory rows BEFORE its early return
    (REQ-5 — additive, 决策 #14 emission-only swallow).

    Pre-fix the branch returned before the EXECUTE/VERIFY trajectory emission
    site (engine.py L~2106-2147) so static-plan nodes got NO EXECUTE/VERIFY
    trajectory row — a real gap (static plans route real bot callbacks through
    the same ``on_report`` entrypoint; the mock fallback also routes through
    ``on_report``). The fix fires ``_emit_execute_trajectory`` for the
    acceptance_result path (the path that reaches the static-plan branch in
    practice — the static-plan + exec_error path is intercepted by the L~2050
    guard before this branch). Verdict mapping mirrors the dynamic branch:
    ``DONE`` → ``accept_pass``; ``FAILED`` → ``accept_fail``."""

    def test_static_plan_accept_pass_emits_execute_and_verify_trajectory(self):
        """Positive: a static-plan report with ``acceptance_result=DONE`` fires
        ONE EXECUTE(success) + ONE VERIFY(accept_pass) trajectory row before the
        early return. ``action_input`` is the request原文 (defensive read from
        the node); ``error_type``/``error_msg`` are None (no exec_error origin
        on the success path)."""
        svc = TaskGraphService()
        graph = svc.initialize_graph(_static_task_info("t1"))
        # Manually materialize the "worker" node + set it RUNNING (mirrors what
        # ``_static_execute`` does for wave-0 children, without going through the
        # full dispatch flow on a unit test).
        _set_running_node(svc, "t1", "worker", harness_retries=0,
                          request_input="worker-request-payload")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = _patch(
            "t1", "worker",
            acceptance_result=_accept(AcceptanceVerdict.DONE),
            output_patch={"result": {"ok": True}},
        )
        _run(eng.on_report(patch))

        # The static-plan branch fired BOTH EXECUTE + VERIFY trajectory rows.
        exec_rows = _execute_records(repo)
        verify_rows = _verify_records(repo)
        assert len(exec_rows) == 1, [(r.action_type, r.action_result) for r in repo.records]
        assert exec_rows[0].action_type == "execute"
        assert exec_rows[0].action_result == "success"
        assert exec_rows[0].error_type is None  # no exec_error → no error_type
        assert exec_rows[0].error_msg is None   # EXECUTE ok row has no error_msg
        assert exec_rows[0].action_input == "worker-request-payload"  # request原文
        assert len(verify_rows) == 1
        assert verify_rows[0].action_type == "verify"
        assert verify_rows[0].action_result == "accept_pass"
        assert verify_rows[0].error_type is None  # VERIFY has no origin mapping

    def test_static_plan_accept_fail_emits_verify_accept_fail(self):
        """acceptance FAIL on a static-plan report → VERIFY(accept_fail). The
        EXECUTE(success) row STILL fires (the execution produced output even if
        acceptance failed — mirrors the dynamic branch's accept_fail path
        where EXECUTE(success) precedes VERIFY(accept_fail))."""
        svc = TaskGraphService()
        graph = svc.initialize_graph(_static_task_info("t1"))
        _set_running_node(svc, "t1", "worker", harness_retries=0)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        patch = _patch(
            "t1", "worker",
            acceptance_result=_accept(AcceptanceVerdict.FAILED, gap_items=["g1"]),
            output_patch={"result": {"ok": False}},
        )
        _run(eng.on_report(patch))

        exec_rows = _execute_records(repo)
        verify_rows = _verify_records(repo)
        assert len(exec_rows) == 1, [(r.action_type, r.action_result) for r in repo.records]
        assert exec_rows[0].action_result == "success"
        assert len(verify_rows) == 1
        assert verify_rows[0].action_result == "accept_fail"

    def test_static_plan_report_does_not_raise_when_traj_repo_is_none(self):
        """决策 #14: the trajectory emission is additive + swallowed; a
        ``None`` trajectory repo (or one that raises) is no-op for the emitter —
        the static-plan report's main flow (``_on_static_report``) still runs
        without the trajectory repo (the emission gate's defensive reads +
        emitter; 决策 #14 fire-and-forget). Locks the behavior: a missing repo
        must not raise from ``on_report`` — the gate is additive."""
        svc = TaskGraphService()
        graph = svc.initialize_graph(_static_task_info("t1"))
        _set_running_node(svc, "t1", "worker", harness_retries=0)
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=None,
        )
        patch = _patch(
            "t1", "worker",
            acceptance_result=_accept(AcceptanceVerdict.DONE),
            output_patch={"result": {"ok": True}},
        )
        # Must NOT raise even with trajectory_repo=None — the gate's emitter
        # is a no-op and the main ``_on_static_report`` flow completes.
        _run(eng.on_report(patch))
        # The main flow DID run: the worker node's status flipped away from
        # RUNNING (the static-plan ``_on_static_report`` advances the node
        # + terminal-checks the graph).
        worker = svc._get_node(graph, "worker")
        assert worker.status != Status.RUNNING

    def test_static_plan_report_does_not_raise_when_repo_raises(self):
        """决策 #14 emission-only swallow: a raising ``insert_event`` is
        swallowed by the emitter (NOT the gate) + WARNING; the main
        ``_on_static_report`` flow completes (root may flip to SUCCESS) and
        the patch's acceptance result is reflected in the node."""
        svc = TaskGraphService()
        graph = svc.initialize_graph(_static_task_info("t1"))
        _set_running_node(svc, "t1", "worker", harness_retries=0)
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=_BoomRepo(),
        )
        patch = _patch(
            "t1", "worker",
            acceptance_result=_accept(AcceptanceVerdict.DONE),
            output_patch={"result": {"ok": True}},
        )
        # Must NOT raise — the emitter swallows the raise + WARN; the gate's
        # main driving logic (``_on_static_report``) is NOT masked.
        _run(eng.on_report(patch))
        # Main flow DID run: the worker node's status flipped.
        worker = svc._get_node(graph, "worker")
        assert worker.status != Status.RUNNING

