"""TDD tests for the PLAN trajectory gate (REQ-3).

P3 item 2 of the task-trajectory spec. Covers five surfaces (per tasks.md P3 REQ-3):
    1. **Per-attempt emission (failing-then-succeeding, MAX_HARNESS=2)** — drive
       `_plan_with_retry` with the real `GapBasedPlanningStrategy` + a scripted bot
       that returns an unparseable response first, then a good one. Assert ≥2
       `plan` trajectory rows, `attempt` strictly increasing, the **mid-row** has
       a non-null `error_type`/`error_msg` (`plan_failure` + message), the
       **success row** has `ext_info.children` (the planned node_ids) and `has_gap`
       with `error_*` None.
    2. **workflow strategy → prompt_digest=None** — a workflow-strategy plan
       attempt produces a trajectory row whose `action_input` (prompt_digest)
       is `None`; `ext_info.strategy_name == "workflow"`,
       `ext_info.raw_response_digest is None`.
    3. **gap-based digests** — a gap-based success row has a non-None
       `action_input` (prompt_digest, 64-hex) and `ext_info.raw_response_digest`
       (64-hex); `ext_info.strategy_name == "gap_based"`; `ext_info` is the
       `{"schema_v":1, ...}` envelope (the emitter wraps it) containing
       `strategy_name`/`has_gap`/`children`/`raw_response_digest`/`gap_detail`.
    4. **PlanResult backward-compat** — existing `PlanResult` consumers (old
       kwargs only) still build; all new provenance fields default `None`.
    5. **Defensive** — if a provenance field is missing, planning still completes
       and the PLAN trajectory event still fires with `ext_info` partial (`None`
       for the missing sub-fields; no raise at the plan layer beyond the
       emitter's swallow). Also: if the trajectory repo raises, `_plan_with_retry`
       still completes (the swallow guarantee, 决策 #14).

Invariants the tests pin (cross-cutting with the task constraints):
    * `_log_action(NodeAction.PLAN, ...)` call stays byte-unchanged (additive
      `_log_trajectory` alongside, NOT a replacement).
    * `NodeAction` enum is never touched; `TrajectoryActionType` is a SEPARATE
      string-typed set (`"plan"`).
    * No `task_action_log` writes/reads — trajectory rows go to the
      `_TrajRepo` fake via `emit_trajectory_event` direct-INSERT only.
    * Zero intrusion: forward-driving plan loop completes even when the
      trajectory repo raises (swallow guarantee, 决策 #14).
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    PlanResult,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_plan.planner import TaskPlanner
from agentclaw.community.core.task.task_plan.strategies import (
    GapBasedPlanningStrategy,
    WorkflowPlanningStrategy,
)


# ---------------------------------------------------------------------------
# Shared helpers / fakes
# ---------------------------------------------------------------------------


def _run(coro):
    """Sync wrapper to drive async plan methods in unit tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _task_info(task_id: str = "t1", max_depth: int = 3) -> TaskInfo:
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
        execution_config={"MAX_DEPTH": max_depth, "BBS_MAX_DEPTH": 3, "task_type": "dynamic"},
    )


def _wf_task_info(task_id: str = "t1") -> TaskInfo:
    """TaskInfo with a workflow (yaml-style) execution_config — selects the
    `WorkflowPlanningStrategy` (its `matches()` checks `cfg.get("workflow")`)."""
    return TaskInfo(
        task_spec=_task_info(task_id).task_spec,
        source_type="bot",
        owner_bot_id="owner:1",
        execution_config={
            "MAX_DEPTH": 3,
            "BBS_MAX_DEPTH": 3,
            "task_type": "workflow",
            "workflow": ["wf_child_1", "wf_child_2"],
        },
    )


def _make_graph(svc: TaskGraphService, task_info: TaskInfo | None = None,
                task_id: str = "t1"):
    """Initialize the graph on first call / return the in-memory graph on
    subsequent calls. The helper guards with `task_id in svc._graphs` so the
    fixture and the test body can both access the same initialized graph."""
    if task_id in svc._graphs:
        return svc._graphs[task_id]
    return svc.initialize_graph(task_info or _task_info(task_id))


class _TrajRepo:
    """Fake trajectory repo — captures every `insert_event` call (no SQLite)."""

    def __init__(self) -> None:
        self.records: list[TrajectoryEventRecord] = []
        self.calls = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self.calls += 1
        self.records.append(record)
        return record


class _ScriptedBot:
    """`OpenApiBotPort` fake — returns a scripted list of `run` dicts per
    `send_and_wait_async` call (repeats the last when scripted list is
    exhausted). Mirrors the real bot port surface used by
    `GapBasedPlanningStrategy.apply` (it calls `send_and_wait_async(bot_id=,
    message=, metadata=)`)."""

    def __init__(self, runs: list[dict]) -> None:
        self._runs = list(runs)
        self.calls = 0
        self.kwargs_seen: list[dict] = []

    async def send_and_wait_async(self, **kwargs):
        idx = min(self.calls, len(self._runs) - 1)
        self.calls += 1
        self.kwargs_seen.append(kwargs)
        return self._runs[idx]


class _ScriptedPlanner:
    """Test planner: returns a scripted list of PlanResult per call (repeats the
    last when exhausted). Bypasses `TaskPlanner.plan` so the test isolates the
    engine's per-attempt emission mechanics from strategy plumbing
    (`planned_children` is whatever the scripted caller sets)."""

    def __init__(self, results: list[PlanResult]) -> None:
        self._results = list(results)
        self.calls = 0

    async def plan(self, graph, target_node_id=None):
        idx = min(self.calls, len(self._results) - 1)
        self.calls += 1
        return self._results[idx]


class _TrajectoryCaseEngine(ExecutionEngine):
    """Test subclass — injects stubs + a trajectory repo (mirrors the existing
    `_CaseEngine` in test_engine.py + the dispatch test's `_TrajectoryCaseEngine`
    but adds the trajectory_repo passthrough for PLAN-gate tests)."""

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


def _plan_records(repo: _TrajRepo) -> list[TrajectoryEventRecord]:
    return [r for r in repo.records if r.action_type == "plan"]


# ---------------------------------------------------------------------------
# 1. Per-attempt emission — failing-then-succeeding with MAX_HARNESS=2
# ---------------------------------------------------------------------------


class TestPlanGatePerAttempt:
    """REQ-3: `_plan_with_retry` fires one `plan` trajectory event per attempt
    (success and each failure). MAX_HARNESS=2 default → failing-then-succeeding
    yields exactly 2 rows; mid-row has error_* set (plan_failure), success row
    has `ext_info.children` + `has_gap`, `error_*` None."""

    def test_failing_then_succeeding_emits_per_attempt(self):
        svc = TaskGraphService()
        graph = _make_graph(svc)
        # First attempt: bot returns non-JSON prose → plan_parse_fail mid-row.
        # Second attempt: bot returns a good JSON child → success row.
        bad_run = {
            "status": "COMPLETED",
            "result": {"content": "this is plain prose, not JSON — forces plan_parse_fail"},
        }
        good_run = {
            "status": "COMPLETED",
            "result": {"content": json.dumps({
                "tasks": [
                    {"metadata": {"task_id": "gap_child_1", "title": "t", "instruction": "i"}}
                ],
                "has_gap": True,
                "gap_detail": "",
            })},
        }
        bot = _ScriptedBot([bad_run, good_run])
        planner = TaskPlanner(svc, pool=[GapBasedPlanningStrategy(bot=bot)])
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(svc, planner=planner, trajectory_repo=repo)

        pr = _run(eng._plan_with_retry("t1", graph))

        # Sanity: the engine plan loop converged on the second attempt
        assert pr is not None and pr.children
        assert pr.children[0].node_id == "gap_child_1"

        plan_records = _plan_records(repo)
        assert len(plan_records) == 2, [
            (r.action_type, r.action_result, r.attempt) for r in repo.records
        ]
        # attempt strictly increasing
        attempts = [r.attempt for r in plan_records]
        assert attempts == sorted(attempts) and len(set(attempts)) == len(attempts), attempts
        assert attempts == [0, 1]

        # mid-row (first, attempt=0): failure → error_* non-null, error_type=plan_failure
        mid = plan_records[0]
        assert mid.attempt == 0
        assert mid.action_type == "plan"
        assert mid.action_result == "parse_fail"
        assert mid.error_type == "plan_failure"
        assert mid.error_msg is not None
        assert "plan_parse_fail" in mid.error_msg
        # mid-row still carries the gap-based digests of the prompt + bad response
        assert mid.action_input is not None
        assert len(mid.action_input) == 64
        int(mid.action_input, 16)
        assert mid.ext_info is not None
        mid_payload = json.loads(mid.ext_info)
        assert mid_payload["schema_v"] == 1
        assert mid_payload["strategy_name"] == "gap_based"
        assert mid_payload["raw_response_digest"] is not None
        assert len(mid_payload["raw_response_digest"]) == 64

        # success row (final, attempt=1): error_* None, ext_info.children + has_gap
        final = plan_records[1]
        assert final.attempt == 1
        assert final.action_result == "success"
        assert final.error_type is None
        assert final.error_msg is None
        assert final.action_input is not None
        assert len(final.action_input) == 64
        int(final.action_input, 16)
        assert final.ext_info is not None
        payload = json.loads(final.ext_info)
        assert payload["schema_v"] == 1
        assert payload["strategy_name"] == "gap_based"
        assert "gap_child_1" in payload["children"]
        assert payload["has_gap"] is True
        assert len(payload["raw_response_digest"]) == 64

    def test_plan_call_fail_attempt_carries_error_msg(self):
        """A planner that raises a transport exception → plan_call_fail mid-row;
        the engine captures the exception text as error_msg (truncated)."""
        svc = TaskGraphService()
        graph = _make_graph(svc)

        class _RaisingPlanner:
            def __init__(self):
                self.calls = 0

            async def plan(self, g, target_node_id=None):
                self.calls += 1
                raise RuntimeError("transport boom")

        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(svc, planner=_RaisingPlanner(), trajectory_repo=repo)
        _run(eng._plan_with_retry("t1", graph))

        plan_records = _plan_records(repo)
        assert len(plan_records) == 2  # MAX_HARNESS=2; both attempts raised
        first, second = plan_records
        assert first.attempt == 0 and second.attempt == 1
        # both mid-rows are call_fail with the exception text in error_msg
        for rec in (first, second):
            assert rec.action_result == "call_fail"
            assert rec.error_type == "plan_failure"
            assert rec.error_msg is not None
            assert "transport boom" in rec.error_msg


# ---------------------------------------------------------------------------
# 2. workflow strategy → prompt_digest=None
# ---------------------------------------------------------------------------


class TestPlanGateWorkflowStrategy:
    """REQ-3: workflow strategy → `prompt_digest=None` (no LLM prompt)."""

    def test_workflow_strategy_emits_null_prompt_digest(self):
        svc = TaskGraphService()
        graph = _make_graph(svc, task_info=_wf_task_info("t1"))
        # Real TaskPlanner with only the WorkflowPlanningStrategy in the pool
        # (since `cfg["workflow"]` is set, it matches first by priority).
        planner = TaskPlanner(svc, pool=[WorkflowPlanningStrategy()])
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(svc, planner=planner, trajectory_repo=repo)

        pr = _run(eng._plan_with_retry("t1", graph))

        # Workflow strategy produced the two configured children
        assert pr is not None and pr.children
        child_ids = {c.node_id for c in pr.children}
        assert child_ids == {"wf_child_1", "wf_child_2"}

        plan_records = _plan_records(repo)
        assert len(plan_records) == 1, [r.attempt for r in plan_records]
        rec = plan_records[0]
        assert rec.action_type == "plan"
        assert rec.action_result == "success"
        # action_input (prompt_digest) is None for workflow (no LLM prompt)
        assert rec.action_input is None
        assert rec.error_type is None
        assert rec.error_msg is None
        assert rec.ext_info is not None
        payload = json.loads(rec.ext_info)
        assert payload["schema_v"] == 1
        assert payload["strategy_name"] == "workflow"
        assert payload["raw_response_digest"] is None  # workflow has no LLM response
        # children in the success row carry the planned workflow node_ids
        for nid in ("wf_child_1", "wf_child_2"):
            assert nid in payload["children"]


# ---------------------------------------------------------------------------
# 3. gap-based digests
# ---------------------------------------------------------------------------


class TestPlanGateGapBasedDigests:
    """REQ-3: gap-based success row carries `prompt_digest`
    (SHA-256(prompt + response[:500])) as `action_input` and `raw_response_digest`
    (SHA-256(response[:500])) in `ext_info`; `strategy_name == "gap_based"`. The
    `ext_info` envelope wraps in `{"schema_v": 1, ...}` (emitter behavior)."""

    def test_gap_based_success_emits_real_digests(self):
        svc = TaskGraphService()
        graph = _make_graph(svc)
        good_run = {
            "status": "COMPLETED",
            "result": {"content": json.dumps({
                "tasks": [
                    {"metadata": {"task_id": "gap_child_1", "title": "t", "instruction": "i"}}
                ],
                "has_gap": True,
                "gap_detail": "",
            })},
        }
        bot = _ScriptedBot([good_run])
        planner = TaskPlanner(svc, pool=[GapBasedPlanningStrategy(bot=bot)])
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(svc, planner=planner, trajectory_repo=repo)

        _run(eng._plan_with_retry("t1", graph))

        plan_records = _plan_records(repo)
        assert len(plan_records) == 1, [r.attempt for r in plan_records]
        rec = plan_records[0]
        assert rec.action_type == "plan"
        assert rec.action_result == "success"
        # action_input = prompt_digest, 64-hex
        assert rec.action_input is not None
        assert len(rec.action_input) == 64
        int(rec.action_input, 16)  # raises if not valid hex
        # ext_info JSON carries strategy_name=gap_based, raw_response_digest 64-hex,
        # has_gap, gap_detail, children
        assert rec.ext_info is not None
        payload = json.loads(rec.ext_info)
        assert payload["schema_v"] == 1
        assert payload["strategy_name"] == "gap_based"
        assert payload["raw_response_digest"] is not None
        assert len(payload["raw_response_digest"]) == 64
        int(payload["raw_response_digest"], 16)
        assert "gap_child_1" in payload["children"]
        assert payload["has_gap"] is True
        # gap_detail is the gap-based strategy's gap_detail string
        assert "gap_detail" in payload


# ---------------------------------------------------------------------------
# 3b. gap_detail → action_result mapping (REQ-3 failure mid-row classification)
# ---------------------------------------------------------------------------


class TestPlanGateActionResultMapping:
    """REQ-3 (review I-3): pin each ``gap_detail`` → ``action_result`` mapping.
    Split criterion:
      * ``call_fail``  = bot call did NOT reach a usable COMPLETED response
        (transport / abandoned: ``plan_call_fail``, ``plan_not_completed``)
      * ``parse_fail`` = COMPLETED response but unusable shape/empty
        (``plan_parse_fail``, ``plan_shape_unexpected``, ``plan_empty_content``)
    Driven via direct ``_emit_plan_trajectory`` calls to isolate the mapping
    logic from the retry loop. The existing per-attempt tests above already
    pin ``plan_parse_fail`` → ``parse_fail`` and ``plan_call_fail`` →
    ``call_fail`` end-to-end; these pin the remaining cases + the success row."""

    @pytest.mark.parametrize("gap_detail,expected_action_result", [
        ("plan_call_fail", "call_fail"),          # engine exception (transport)
        ("plan_not_completed", "call_fail"),       # bot returned non-COMPLETED
        ("plan_parse_fail", "parse_fail"),         # COMPLETED but unparseable JSON
        ("plan_shape_unexpected", "parse_fail"),   # COMPLETED but unexpected shape
        ("plan_empty_content", "parse_fail"),      # COMPLETED but empty content
    ])
    def test_failure_gap_detail_to_action_result(self, gap_detail,
                                                 expected_action_result):
        svc = TaskGraphService()
        _make_graph(svc)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(svc, trajectory_repo=repo)
        pr = PlanResult(
            children=[], has_gap=True, gap_detail=gap_detail,
            strategy_name="gap_based",
        )
        # Drive the per-attempt emitter directly (bypass the retry loop).
        eng._emit_plan_trajectory("t1", "t1", pr, 0, failure_msg=None)
        plan_records = _plan_records(repo)
        assert len(plan_records) == 1
        rec = plan_records[0]
        assert rec.action_type == "plan"
        assert rec.action_result == expected_action_result, (
            f"gap_detail={gap_detail} should map to {expected_action_result}, "
            f"got {rec.action_result}"
        )
        assert rec.error_type == "plan_failure"
        assert rec.error_msg is not None
        # the failure mid-row envelope carries strategy_name / gap_detail /
        # raw_response_digest (no has_gap / children — those are success-only)
        assert rec.ext_info is not None
        payload = json.loads(rec.ext_info)
        assert payload["schema_v"] == 1
        assert payload["gap_detail"] == gap_detail
        assert "has_gap" not in payload
        assert "children" not in payload

    def test_non_plan_gap_detail_is_success(self):
        """``gap_detail`` not starting with ``plan_`` → success row (no error)."""
        svc = TaskGraphService()
        _make_graph(svc)
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(svc, trajectory_repo=repo)
        pr = PlanResult(children=[], has_gap=False, gap_detail="done",
                        strategy_name="gap_based")
        eng._emit_plan_trajectory("t1", "t1", pr, 0, failure_msg=None)
        plan_records = _plan_records(repo)
        assert len(plan_records) == 1
        rec = plan_records[0]
        assert rec.action_result == "success"
        assert rec.error_type is None
        assert rec.error_msg is None


# ---------------------------------------------------------------------------
# 3c. _root(task_id) resolution regression (review I-1)
# ---------------------------------------------------------------------------


class TestPlanGateRootResolution:
    """Review I-1: ``_root(task_id)`` is engine's own graph lookup — NOT under
    决策 #14's swallow scope (which is for trajectory EMISSION failures only).
    Pre-change ``652beb210`` had it unguarded; the try/except that wrapped it
    during REQ-3 was removed because it would silently swallow BOTH the
    trajectory emission AND the ``_log_action(PLAN)`` write on a graph
    failure (AGENTS.md anti-pattern).

    Pinned:
      * Common case (root present): ``target_id`` resolves via ``_root``; BOTH
        the per-attempt ``_log_trajectory`` row and the post-loop
        ``_log_action(NodeAction.PLAN, ...)`` fire, anchored on the root's
        node_id.
      * Failure case (``_root`` raises): the exception propagates out of
        ``_plan_with_retry`` (engine behavior preserved — not silently swallowed
        at the trajectory gate)."""

    def test_legit_root_resolves_emits_trajectory_and_logs_action(self):
        """Common case: target_node_id=None → _plan_with_retry resolves
        target_id via _root(task_id); the single root ("t1") becomes the anchor
        for both the trajectory event and the action-log write."""
        svc = TaskGraphService()
        graph = _make_graph(svc)
        repo = _TrajRepo()
        pr = PlanResult(children=[], has_gap=False, gap_detail="done")
        eng = _TrajectoryCaseEngine(
            svc, planner=_ScriptedPlanner([pr]), trajectory_repo=repo
        )
        # target_node_id=None forces the engine to resolve via _root(task_id).
        result = _run(eng._plan_with_retry("t1", graph, target_node_id=None))
        # plan completes normally
        assert result is not None and result.has_gap is False
        # per-attempt PLAN trajectory fired — anchored on the root "t1"
        plan_records = _plan_records(repo)
        assert len(plan_records) == 1, [r.action_result for r in repo.records]
        assert plan_records[0].node_id == "t1"
        assert plan_records[0].action_result == "success"
        # post-loop _log_action(NodeAction.PLAN, ...) ALSO fired on "t1"
        # (NodeAction is a StrEnum: PLAN.value == "plan")
        root = svc._get_node(graph, "t1")
        action_log = root.run_info.action_log
        plan_actions = [
            ev for ev in action_log
            if ev.action.value == "plan" and ev.payload.get("__node_id") == "t1"
        ]
        assert plan_actions, [(ev.action.value, ev.payload) for ev in action_log]

    def test_root_raising_propagates_not_swallowed(self):
        """If ``_root`` raises (graph query failure), the exception propagates
        out of ``_plan_with_retry`` — NOT swallowed at the trajectory gate
        (I-1: ``_root`` is outside 决策 #14's scope)."""
        svc = TaskGraphService()
        graph = _make_graph(svc)
        repo = _TrajRepo()
        pr = PlanResult(children=[], has_gap=False, gap_detail="done")
        eng = _TrajectoryCaseEngine(
            svc, planner=_ScriptedPlanner([pr]), trajectory_repo=repo
        )

        def _boom_root(task_id):  # noqa: ANN001  test monkey-patch
            raise RuntimeError("graph lookup boom")

        eng._root = _boom_root
        with pytest.raises(RuntimeError, match="graph lookup boom"):
            _run(eng._plan_with_retry("t1", graph, target_node_id=None))
        # No trajectory row fired — the loop was never entered.
        assert _plan_records(repo) == []


# ---------------------------------------------------------------------------
# 4. PlanResult backward-compat
# ---------------------------------------------------------------------------


class TestPlanResultBackwardCompat:
    """Existing `PlanResult` consumers (planner/strategy tests) use the old
    kwargs; all new provenance fields default `None` so they don't break."""

    def test_old_kwargs_build_default_provenance_none(self):
        # Old-style construction with only existing kwargs — must not raise.
        pr = PlanResult(children=[], has_gap=False, gap_detail="done")
        assert pr.children == []
        assert pr.has_gap is False
        assert pr.gap_detail == "done"
        assert pr.acceptance_result is None
        # New provenance fields are additive and default None
        assert pr.strategy_name is None
        assert pr.prompt_digest is None
        assert pr.raw_response_digest is None
        assert pr.planned_children is None

    def test_no_kwargs_build_default_everything(self):
        pr = PlanResult()
        assert pr.children == []
        assert pr.has_gap is False
        assert pr.gap_detail == ""
        # New fields
        assert pr.strategy_name is None
        assert pr.prompt_digest is None
        assert pr.raw_response_digest is None
        assert pr.planned_children is None

    def test_new_kwargs_build_with_provenance(self):
        pr = PlanResult(
            children=[], has_gap=True, gap_detail="plan_parse_fail",
            strategy_name="gap_based", prompt_digest="a" * 64,
            raw_response_digest="b" * 64, planned_children=["c1"],
        )
        assert pr.strategy_name == "gap_based"
        assert pr.prompt_digest == "a" * 64
        assert pr.raw_response_digest == "b" * 64
        assert pr.planned_children == ["c1"]


# ---------------------------------------------------------------------------
# 5. Defensive — missing provenance + trajectory repo raises
# ---------------------------------------------------------------------------


class TestPlanGateDefensive:
    """Defensive: a PlanResult with missing provenance (e.g., a scripted planner
    building the old shape) still completes planning, and the PLAN trajectory
    event still fires with `ext_info` partial (`None` for the missing sub-fields).
    Also verifies 决策 #14: a broken trajectory repo doesn't break the plan loop."""

    def test_partial_provenance_still_emits(self):
        """A scripted planner returns a PlanResult with only old fields
        (strategy_name=None, etc.) — the engine should still fire one PLAN
        trajectory row per attempt with `action_result=success`,
        `action_input=None` and `ext_info` carrying None sub-fields."""
        svc = TaskGraphService()
        graph = _make_graph(svc)
        pr = PlanResult(children=[], has_gap=False, gap_detail="done")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc, planner=_ScriptedPlanner([pr]), trajectory_repo=repo
        )
        _run(eng._plan_with_retry("t1", graph))

        plan_records = _plan_records(repo)
        assert len(plan_records) == 1
        rec = plan_records[0]
        assert rec.action_result == "success"
        # action_input=None when prompt_digest is None
        assert rec.action_input is None
        assert rec.error_type is None
        assert rec.error_msg is None
        # ext_info envelope still emitted with None values for missing provenance
        assert rec.ext_info is not None
        payload = json.loads(rec.ext_info)
        assert payload["schema_v"] == 1
        assert payload["strategy_name"] is None
        assert payload["raw_response_digest"] is None
        assert payload["children"] == []  # planned_children None → []
        assert "has_gap" in payload
        assert payload["has_gap"] is False

    def test_plan_gate_completes_when_trajectory_repo_raises(self, caplog):
        """Global swallow guarantee (决策 #14): even if the trajectory repo
        raises `insert_event`, `_plan_with_retry` completes (the engine does
        not crash) and the emitter logs a WARNING (observable)."""

        class _BoomRepo:
            def insert_event(self, record):
                raise RuntimeError("trajectory repo boom")

        svc = TaskGraphService()
        graph = _make_graph(svc)
        pr = PlanResult(children=[], has_gap=False, gap_detail="done")
        eng = _TrajectoryCaseEngine(
            svc, planner=_ScriptedPlanner([pr]), trajectory_repo=_BoomRepo()
        )
        with caplog.at_level(logging.WARNING, logger="task.trajectory"):
            result = _run(eng._plan_with_retry("t1", graph))
        # plan completes and returns the (success) result (no exception bubbled)
        assert result is not None and result.has_gap is False
        # emitter swallowed the exception and logged WARNING (observable)
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "task.trajectory" in r.name
        ]
        assert any("boom" in r.getMessage() for r in warnings), \
            [r.getMessage() for r in caplog.records]
