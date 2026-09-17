"""TDD tests for the RESET trajectory gate (REQ-4).

P3 item 3 of the task-trajectory spec. The harness RESET gate lives in
``ExecutionEngine._on_harness_collect`` (engine.py), reached via ``on_harness``
(harness polls) and via ``on_report`` exec-error callbacks. The gate must fire
an **additive** ``TrajectoryActionType.RESET`` trajectory event next to each
harness reset point with:

    action_result ∈ {sla_timeout, exec_failed_retry, pending_dispatch_stuck,
                     bbs_lease_expired, harness_max}
    ext_info = {trigger, elapsed_ms, sla_threshold_ms, attempts_seen}
      * trigger         == action_result (the normalized category)
      * elapsed_ms      = int(time.time()*1000) - run_info.start_time (None if no
                          start_time — defensive; for pending_dispatch_stuck the
                          dwell baseline is the node start_time when available)
      * sla_threshold_ms = effective SLA in ms for timeout-class triggers only:
                          sla_timeout → 600_000 single_bot / 900_000 coop_group
                          (overridable via execution_config["SLA_TIMEOUT"]);
                          pending_dispatch_stuck → 180_000 (overridable via
                          execution_config["PENDING_TIMEOUT"]);
                          exec_failed_retry / bbs_lease_expired / harness_max → None
      * attempts_seen   = harness retries seen at this reset (mirrors what
                          ``_log_action(NodeAction.RESET, ...)`` records for the
                          FAILED/RUNNING→PENDING site; pre-increment for harness_max)

Covers (per tasks.md P3 REQ-4):
    1. SLA-timeout RESET row (single-bot 600s; coop-group 900s) carries
       failure_reason-derivable elapsed/threshold.
    2. pending_dispatch_stuck carries the dwell + 180s threshold.
    3. Non-timeout triggers (exec_failed_retry / bbs_lease_expired / harness_max)
       carry sla_threshold_ms=None; elapsed_ms still set.
    4. execution_config["SLA_TIMEOUT"] override honored from the engine read path.
    5. ``_log_action(NodeAction.RESET, ...)`` byte-unchanged; ``NodeAction`` /
       ``append_action_event`` / ``task_action_log`` untouched (additive only).
    6. Defensive: a hostile threshold read still yields a RESET row with
       ``sla_threshold_ms=None`` and the gate completes (the swallow guarantee
       lives in the emitter; the gate's threshold-read defense is silent / DEBUG).

Invariants the tests pin (cross-cutting with the task constraints):
    * The existing ``_log_action(NodeAction.RESET, ...)`` call stays byte-
      unchanged (additive ``_log_trajectory`` alongside, NOT a replacement).
    * ``NodeAction`` enum is never touched; ``TrajectoryActionType`` is a
      SEPARATE string-typed set (``"reset"``).
    * No ``task_action_log`` writes/reads — trajectory rows go to the
      ``_TrajRepo`` fake via ``emit_trajectory_event`` direct-INSERT only.
    * Zero intrusion: forward-driving reset/re-dispatch completes even when the
      trajectory repo raises (swallow guarantee, 决策 #14).
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


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


class _TrajRepo:
    """Fake trajectory repo — captures every ``insert_event`` call (no SQLite)."""

    def __init__(self) -> None:
        self.records: list[TrajectoryEventRecord] = []
        self.calls = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self.calls += 1
        self.records.append(record)
        return record


class _RaisingOnResetRepo:
    """Repo that raises **only for RESET** rows — pins that the RESET emission
    was attempted (so the test is RED before the gate is wired) AND that the
    emitter swallow+WARNING (决策 #14) keeps the gate driving forward. Non-reset
    rows (PLAN/DISPATCH from re-dispatch) succeed so the rest of the path is
    not perturbed by this defense probe.
    """

    def __init__(self) -> None:
        self.reset_attempts = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        if record.action_type == "reset":
            self.reset_attempts += 1
            raise RuntimeError("simulated RESET insert failure")
        return record


class _StubPlanner:
    async def plan(self, graph, target_node_id=None):
        from agentclaw.community.core.task.domain.models import PlanResult
        return PlanResult(children=[], has_gap=True)


class _StubDispatcher:
    def __init__(self, run_mode="single_bot", assignee="bot1"):
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
    """Mirrors the existing ``StubRunner`` in test_engine.py: the engine's
    ``_drain`` calls ``start_run(toDoTaskList) -> list[bool]`` + ``form_coop_group``.
    """

    def __init__(self):
        self.run_calls: list[list[TaskNode]] = []
        self.bbs_calls: list = []
        self._groups: list = []

    async def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        self.run_calls.append(list(toDoTaskList))
        self.bbs_calls.extend(
            node.task_id
            for node in toDoTaskList
            if node.run_info.run_mode == "bbs"
        )
        return [True] * len(toDoTaskList)

    async def form_coop_group(self, gf):
        self._groups.append(gf)
        return "grp_stub"


class _TrajectoryCaseEngine(ExecutionEngine):
    """Test subclass — injects stubs + a trajectory repo (mirrors the PLAN and
    DISPATCH gate test subclasses but adds the trajectory_repo passthrough)."""

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


def _reset_records(repo: _TrajRepo) -> list[TrajectoryEventRecord]:
    return [r for r in repo.records if r.action_type == "reset"]


def _ext_info(record: TrajectoryEventRecord) -> dict:
    """Parse the wrapped ``{"schema_v": 1, **ext_info}`` envelope."""
    assert record.ext_info is not None, "RESET row must carry ext_info"
    payload = json.loads(record.ext_info)
    assert payload["schema_v"] == 1, "ext_info must be wrapped in schema_v envelope"
    return payload


def _set_running_node(svc, task_id, node_id, *, run_mode="single_bot",
                     start_time=None, harness_retries=0, assignee="b"):
    """Preset a child node into RUNNING with the given run_mode/start_time/retries."""
    svc.add_task_nodes([_child(node_id, task_id)], parent_node_id=task_id)
    svc.update_task_node_info(
        _patch(task_id, node_id, status=Status.RUNNING,
               run_mode=run_mode, assignee=assignee,
               start_time=start_time)
    )
    if harness_retries:
        svc.update_task_node_info(
            _patch(task_id, node_id,
                   extend_props_patch={"harness_retries": harness_retries})
        )


# ---------------------------------------------------------------------------
# 1. SLA-timeout RESET row (single-bot 600s, coop-group 900s)
# ---------------------------------------------------------------------------


class TestResetSlaTimeout:
    """REQ-4: an SLA-timeout RESET (harness RUNNING→PENDING, exec_error=None →
    ``external_harness`` sentinel) yields a trajectory row whose
    ``action_result=sla_timeout`` + ``ext_info.{trigger, elapsed_ms,
    sla_threshold_ms, attempts_seen}`` are failure_reason-derivable."""

    def test_single_bot_threshold_is_600s(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        t0 = int(time.time() * 1000)
        start_time = t0 - 10_000  # 10s ago
        _set_running_node(svc, "t1", "c1", run_mode="single_bot",
                          start_time=start_time, harness_retries=0)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        # Harness SLA-timeout patch: no exec_error → on_harness substitutes the
        # ``external_harness`` sentinel → gate maps to action_result=sla_timeout.
        _run(eng.on_harness(_patch("t1", "c1", status=Status.PENDING,
                                    extend_props_patch={"harness_reset": "timeout"})))

        records = _reset_records(repo)
        assert len(records) == 1, [(r.action_type, r.action_result) for r in repo.records]
        rec = records[0]
        assert rec.action_type == "reset"
        assert rec.action_result == "sla_timeout"
        assert rec.status_from == Status.RUNNING
        assert rec.status_to == Status.PENDING
        assert rec.attempt == 1  # post-increment (mirrors _log_action RESET attempt)
        info = _ext_info(rec)
        assert info["trigger"] == "sla_timeout"
        assert info["sla_threshold_ms"] == 600_000  # 600.0s * 1000
        assert info["attempts_seen"] == 1
        # elapsed_ms ≈ now - start_time (allow generous tolerance for test latency)
        assert info["elapsed_ms"] is not None
        assert 10_000 <= info["elapsed_ms"] <= 15_000, info["elapsed_ms"]

    def test_coop_group_threshold_is_900s(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        start_time = int(time.time() * 1000) - 8_000
        _set_running_node(svc, "t1", "c1", run_mode="coop_group",
                          start_time=start_time, harness_retries=0)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", status=Status.PENDING,
                                    extend_props_patch={"harness_reset": "timeout"})))

        rec = _reset_records(repo)[0]
        assert rec.action_result == "sla_timeout"
        info = _ext_info(rec)
        assert info["sla_threshold_ms"] == 900_000  # 900.0s * 1000
        assert info["trigger"] == "sla_timeout"
        assert info["attempts_seen"] == 1
        assert info["elapsed_ms"] is not None


# ---------------------------------------------------------------------------
# 2. pending_dispatch_stuck — dwell + 180s threshold
# ---------------------------------------------------------------------------


class TestResetPendingDispatchStuck:
    """REQ-4: a pending-dispatch-stuck reset (PENDING node, dispatch_error set)
    fires a RESET trajectory at the PENDING elif branch (no _log_action(RESET)
    there) with ``sla_threshold_ms=180_000`` and ``elapsed_ms`` = the dwell."""

    def test_pending_dispatch_stuck_carries_180s_threshold_and_dwell(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        start_time = int(time.time() * 1000) - 5_000  # dwell baseline
        # PENDING node (undispatched: no run_mode/assignee) + dispatch_error set
        svc.add_task_nodes([_child("c1", "t1")], parent_node_id="t1")
        svc.update_task_node_info(
            _patch("t1", "c1", status=Status.PENDING, start_time=start_time,
                   extend_props_patch={"dispatch_error": "stuck"})
        )
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", exec_error="pending_dispatch_stuck")))

        records = _reset_records(repo)
        assert len(records) == 1, [(r.action_type, r.action_result) for r in repo.records]
        rec = records[0]
        assert rec.action_type == "reset"
        assert rec.action_result == "pending_dispatch_stuck"
        assert rec.status_from == Status.PENDING
        assert rec.status_to == Status.PENDING
        info = _ext_info(rec)
        assert info["trigger"] == "pending_dispatch_stuck"
        assert info["sla_threshold_ms"] == 180_000  # 180.0s * 1000
        assert info["attempts_seen"] == 1  # retries incremented for this branch too
        assert info["elapsed_ms"] is not None
        assert 5_000 <= info["elapsed_ms"] <= 10_000, info["elapsed_ms"]


# ---------------------------------------------------------------------------
# 3. Non-timeout triggers — sla_threshold_ms=None, elapsed_ms still set
# ---------------------------------------------------------------------------


class TestResetNonTimeoutTriggers:
    """REQ-4: exec_failed_retry / bbs_lease_expired / harness_max carry
    ``sla_threshold_ms=None`` (non-timeout triggers); ``elapsed_ms`` still set."""

    def test_exec_failed_retry_threshold_none(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        start_time = int(time.time() * 1000) - 3_000
        # exec_failed_retry reaches the FAILED→PENDING reset site.
        _set_running_node(svc, "t1", "c1", run_mode="single_bot",
                          start_time=start_time, harness_retries=0)
        # Flip to FAILED to mirror the harness FAILED-scan entry status.
        svc.update_task_node_info(_patch("t1", "c1", status=Status.FAILED))
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", exec_error="exec_failed_retry")))

        rec = _reset_records(repo)[0]
        assert rec.action_result == "exec_failed_retry"
        info = _ext_info(rec)
        assert info["trigger"] == "exec_failed_retry"
        assert info["sla_threshold_ms"] is None
        assert info["elapsed_ms"] is not None
        assert info["attempts_seen"] == 1

    def test_bbs_lease_expired_threshold_none(self):
        # bbs_lease_expired does not currently arrive from the harness (it writes
        # the graph directly), but the gate mapping must still categorize it as
        # a non-timeout trigger → sla_threshold_ms=None.
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        start_time = int(time.time() * 1000) - 2_000
        _set_running_node(svc, "t1", "c1", run_mode="single_bot",
                          start_time=start_time, harness_retries=0)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", exec_error="bbs_lease_expired")))

        rec = _reset_records(repo)[0]
        assert rec.action_result == "bbs_lease_expired"
        info = _ext_info(rec)
        assert info["trigger"] == "bbs_lease_expired"
        assert info["sla_threshold_ms"] is None
        assert info["elapsed_ms"] is not None

    def test_harness_max_threshold_none(self):
        # retries >= MAX_HARNESS (default 2) → harness_max → HUNG escalation
        # path. The RESET trajectory fires there with attempts_seen=retries
        # (pre-increment, == max_harness) and sla_threshold_ms=None.
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t_bbs", max_depth=1))
        graph.extend_props["execution_config"]["MAX_LOOP"] = 10  # avoid loop-cap detour
        start_time = int(time.time() * 1000) - 4_000
        _set_running_node(svc, "t_bbs", "c1", run_mode="single_bot",
                          start_time=start_time, harness_retries=3)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )

        async def _go():
            await eng.on_harness(_patch("t_bbs", "c1", exec_error="exec_failed_retry"))
            if eng._bg_tasks:
                for bg in list(eng._bg_tasks):
                    try:
                        await asyncio.wrap_future(bg) if isinstance(
                            bg, asyncio.Future) else await bg
                    except Exception:  # noqa: BLE001 swallow bg fallout in test
                        pass

        _run(_go())

        rec = _reset_records(repo)[0]
        assert rec.action_result == "harness_max"
        info = _ext_info(rec)
        assert info["trigger"] == "harness_max"
        assert info["sla_threshold_ms"] is None
        assert info["elapsed_ms"] is not None
        assert info["attempts_seen"] == 3


# ---------------------------------------------------------------------------
# 4. execution_config override honored from the engine read path
# ---------------------------------------------------------------------------


class TestResetSlaOverride:
    """REQ-4: ``execution_config["SLA_TIMEOUT"]=120`` is honored by the engine
    gate's threshold read → ``sla_threshold_ms == 120_000`` for an sla_timeout
    row. (PENDING_TIMEOUT override likewise for pending_dispatch_stuck.)"""

    def test_sla_timeout_override(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1", extra_cfg={"SLA_TIMEOUT": 120}))
        start_time = int(time.time() * 1000) - 1_000
        _set_running_node(svc, "t1", "c1", run_mode="single_bot",
                          start_time=start_time, harness_retries=0)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", status=Status.PENDING,
                                    extend_props_patch={"harness_reset": "timeout"})))

        rec = _reset_records(repo)[0]
        info = _ext_info(rec)
        assert info["sla_threshold_ms"] == 120_000  # 120.0s * 1000 override

    def test_pending_timeout_override(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1", extra_cfg={"PENDING_TIMEOUT": 60}))
        start_time = int(time.time() * 1000) - 500
        svc.add_task_nodes([_child("c1", "t1")], parent_node_id="t1")
        svc.update_task_node_info(
            _patch("t1", "c1", status=Status.PENDING, start_time=start_time,
                   extend_props_patch={"dispatch_error": "stuck"})
        )
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", exec_error="pending_dispatch_stuck")))

        rec = _reset_records(repo)[0]
        info = _ext_info(rec)
        assert info["sla_threshold_ms"] == 60_000  # 60.0s * 1000 override


# ---------------------------------------------------------------------------
# 5. _log_action(RESET) byte-unchanged + NodeAction/append_action_event untouched
# ---------------------------------------------------------------------------


class TestResetLogActionUnchanged:
    """REQ-4 additive: the existing ``_log_action(NodeAction.RESET, ...)`` call
    stays byte-unchanged — the node action_log still carries the RESET entry
    with the existing payload (reason/prev_status/harness_retries_after), AND a
    RESET trajectory event fires alongside (additive, not a replacement)."""

    def test_action_log_reset_entry_preserved_and_trajectory_additive(self):
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        start_time = int(time.time() * 1000) - 2_000
        _set_running_node(svc, "t1", "c1", run_mode="single_bot",
                          start_time=start_time, harness_retries=0)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", status=Status.PENDING,
                                    extend_props_patch={"harness_reset": "timeout"})))

        # 1) The node action_log still has the existing RESET entry (byte-unchanged
        #    payload shape: reason == the external_harness sentinel, prev_status,
        #    harness_retries_after == post-increment retries).
        node = svc._get_node(graph, "c1")
        reset_events = [e for e in node.run_info.action_log
                        if e.action.value == "reset"]
        assert len(reset_events) == 1, [e.action.value for e in node.run_info.action_log]
        ev = reset_events[0]
        assert ev.payload["reason"] == "external_harness"
        assert ev.payload["prev_status"] == "RUNNING"  # Status.<x>.value is uppercase
        assert ev.payload["harness_retries_after"] == 1
        assert ev.attempt == 1
        assert ev.status_from.value == "RUNNING"
        assert ev.status_to.value == "PENDING"

        # 2) The trajectory side fired ONE additive RESET row (independent
        #    direct-INSERT, not via append_action_event).
        assert len(_reset_records(repo)) == 1


# ---------------------------------------------------------------------------
# 6. Defensive — threshold read failure → sla_threshold_ms=None, gate completes
# ---------------------------------------------------------------------------


class TestResetDefensive:
    """REQ-4 决策 #14 scope: the SLA-threshold read + elapsed computation at the
    gate are NOT emission — they are defensive (try/except → None so a missing
    threshold doesn't break the gate) but do NOT silently swallow a real bug
    beyond reasonable defensiveness. Only the trajectory *emission* is
    swallow+WARNING (inside the emitter)."""

    def test_hostile_sla_value_yields_none_threshold_and_still_fires(self):
        # A non-numeric SLA_TIMEOUT would make float() raise; the gate's
        # threshold-read defense catches → sla_threshold_ms=None, the RESET
        # trajectory row still fires, and the gate completes (re-dispatch).
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1", extra_cfg={"SLA_TIMEOUT": "not_a_number"}))
        start_time = int(time.time() * 1000) - 1_500
        _set_running_node(svc, "t1", "c1", run_mode="single_bot",
                          start_time=start_time, harness_retries=0)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", status=Status.PENDING,
                                    extend_props_patch={"harness_reset": "timeout"})))

        rec = _reset_records(repo)[0]
        assert rec.action_result == "sla_timeout"
        info = _ext_info(rec)
        assert info["sla_threshold_ms"] is None  # defensive fallback
        # elapsed_ms is independent of the threshold read → still computed
        assert info["elapsed_ms"] is not None
        # the gate completed: node was reset to PENDING then re-dispatched → RUNNING
        assert svc._get_node(graph, "c1").status == Status.RUNNING

    def test_missing_start_time_yields_none_elapsed(self):
        # A fresh PENDING node (never dispatched) has start_time=None → elapsed_ms
        # degrades to None (defensive); the RESET row still fires.
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        svc.add_task_nodes([_child("c1", "t1")], parent_node_id="t1")
        svc.update_task_node_info(
            _patch("t1", "c1", status=Status.PENDING,
                   extend_props_patch={"dispatch_error": "stuck"})
        )
        # start_time intentionally left None (undispatched PENDING)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=repo,
        )
        _run(eng.on_harness(_patch("t1", "c1", exec_error="pending_dispatch_stuck")))

        rec = _reset_records(repo)[0]
        info = _ext_info(rec)
        assert rec.action_result == "pending_dispatch_stuck"
        assert info["elapsed_ms"] is None  # start_time was None → defensive None
        assert info["sla_threshold_ms"] == 180_000

    def test_emitter_raise_swallowed_gate_still_completes(self, caplog):
        # The swallow guarantee (决策 #14): if the trajectory repo raises, the
        # gate still drives forward (reset + re-dispatch); the emitter logs
        # WARNING. The gate's threshold-read defense is NOT the emission swallow.
        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info("t1"))
        start_time = int(time.time() * 1000) - 1_000
        _set_running_node(svc, "t1", "c1", run_mode="single_bot",
                          start_time=start_time, harness_retries=0)
        raising = _RaisingOnResetRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_StubPlanner(), dispatcher=_StubDispatcher(),
            runner=_StubRunner(), trajectory_repo=raising,
        )
        with caplog.at_level("WARNING", logger="task.trajectory"):
            _run(eng.on_harness(_patch("t1", "c1", status=Status.PENDING,
                                        extend_props_patch={"harness_reset": "timeout"})))
        # The RESET row was attempted (proves the gate fired RESET specifically)
        assert raising.reset_attempts == 1, "RESET emission not attempted"
        # ... and the emitter logged a WARNING (swallow + WARN, 决策 #14)
        assert any("发射失败" in rec.message for rec in caplog.records)
        # gate drove forward: reset + re-dispatch → RUNNING
        assert svc._get_node(graph, "c1").status == Status.RUNNING
