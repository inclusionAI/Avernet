"""Branch pins — the defensive/edge branches of the task_trajectory package
that the scenario suites' happy-and-error paths never reach (each maps to a
specific uncovered line reported by --cov; see the姊妹 suites
``test_trajectory_e2e_scenarios_relay.py`` / ``..._centralized.py`` for the 8
user-facing scenarios).

Sections:
* trajectory_service.py — the borrowed-by-adapter gate helpers (decision #14
  swallow semantics, defensive reads, the static-plan lazy loader).
* analyzer.py — tc_bot contract edges (error passthrough, non-dict / non-str
  coercions, missing result) + boost assignee fallbacks via the real read path.
* payloads.py / assembler.py / time_utils.py — the non-dict ext_info / None
  timestamp defensive branches.

These are deliberately the ONLY non-e2e tests in this delivery: each pins a
branch the full-link drives cannot reach without a hostile/broken neighbor.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.task.task_trajectory_repository import (
    TaskTrajectoryRepository,
)
from agentclaw.community.core.task.domain.errors import (
    TrajectoryAnalysisError,
)
from agentclaw.community.core.task.domain.models import (
    NodeOpResult,
    PlanResult,
    Status,
    TaskNodePatch,
)
from agentclaw.community.core.task.task_runner.centralized_support import (
    NodeAction,
)
from agentclaw.community.core.task.task_context.task_context_service import (
    TaskContextService,
)
from agentclaw.community.core.task.task_context.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
)
from agentclaw.community.core.task.task_context.task_trajectory.assembler import (
    TaskTrajectoryAssembler,
    _holder_id_from_ext_info,
)
from agentclaw.community.core.task.task_context.task_trajectory.payloads import (
    emit_trajectory_event,
)
from agentclaw.community.core.task.task_context.task_trajectory.time_utils import (
    storage_datetime_to_epoch_ms,
)
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
    _build_ext_info_lookup,
)
from agentclaw.community.core.task.task_runner.execution_adapters import (
    CentralizedExecutionAdapter,
)
from agentclaw.community.di.task_trajectory_config import TrajectoryAnalysisConfig

import agentclaw.community.core.task.repository.models  # noqa: F401


# ---------------------------------------------------------------------------
# Shared mini-harness
# ---------------------------------------------------------------------------


class _InMemorySqliteDB:
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
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return _InMemorySqliteDB(eng)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Snapshot:
    """Minimal query_task_dashboard returnee."""

    def __init__(self, tasks) -> None:
        self.tasks = tasks
        self.status = Status.RUNNING
        self.extend_props: dict = {}


class _StubGraph:
    """Graph stub for the borrowed gate helpers' DEFENSIVE read branches
    (engine methods borrowed from TaskTrajectoryService run with the adapter
    as self; the graph is the adapter's service — a stub isolates the branch)."""

    def __init__(self, *, tasks=None, query_raises=False, append_raises=False,
                 execution_config=None) -> None:
        self._tasks = tasks if tasks is not None else []
        self._query_raises = query_raises
        self._append_raises = append_raises
        self._cfg = execution_config or {}

    def query_task_dashboard(self, task_id):
        if self._query_raises:
            raise RuntimeError("graph unavailable")
        return _Snapshot(list(self._tasks))

    def append_action_event(self, task_id, node_id, action, payload, **kw):
        if self._append_raises:
            raise RuntimeError("action-log unavailable")
        return None

    def _execution_config(self, task_id):
        return dict(self._cfg)


class _BadFacade:
    """A facade whose emit raises — pins the borrowed emitters' swallow."""

    def emit_trajectory_event(self, *a, **kw):
        raise RuntimeError("facade boom")


class _Engine(CentralizedExecutionAdapter):
    """Bare adapter over a stub graph — only the borrowed trajectory helpers
    are exercised here (no drive loops)."""

    def __init__(self, graph, tcs=None) -> None:
        super().__init__(graph, task_context_service=tcs)


# ---------------------------------------------------------------------------
# trajectory_service.py — borrowed gate helpers (decision #14 semantics)
# ---------------------------------------------------------------------------


class TestPlanTrajectoryGuardBranches:
    def test_plan_trajectory_without_target_node_is_noop(self):
        """PLAN gate target_id=None (no anchor node) → returns without emission."""
        repo = TaskTrajectoryRepository(_make_db())
        eng = _Engine(_StubGraph(), tcs=None)
        pr = PlanResult(children=[], has_gap=True, gap_detail="done")
        eng._emit_plan_trajectory("t-x", None, pr, 0, None)  # no raise, no row
        assert repo.list_events_by_task("t-x") == []

    def test_plan_trajectory_assembly_failure_is_swallowed(self):
        """A PlanResult whose shape raises mid-assembly → swallow + WARNING
        (decision #14); the forward-driving path is unaffected."""

        class _BoomResult:
            strategy_name = "s"

            @property
            def gap_detail(self):
                raise RuntimeError("gap_detail boom")

        eng = _Engine(_StubGraph())
        eng._emit_plan_trajectory("t-x", "n1", _BoomResult(), 0, None)  # no raise


class TestLogActionGuardBranches:
    def test_log_action_append_failure_is_swallowed(self):
        """append_action_event raising → swallow + WARNING (history is a
        pure observational旁路, it must not fail the gate)."""
        eng = _Engine(_StubGraph(append_raises=True))
        eng._log_action("t-x", "n1", NodeAction.PLAN, {}, attempt=0)  # no raise


class TestExecErrorDefensiveReads:
    def _patch(self, **kw):
        return TaskNodePatch(task_id="t-x", node_id="n1", **kw)

    def test_interface_error_code_read_with_hostile_patch_returns_none(self):
        """A hostile extend_props_patch (raises on .get) → None, never a gate
        failure (trajectory-assembly read is decision-#14 scope)."""

        class _Hostile(dict):
            def get(self, key, default=None):
                raise RuntimeError("hostile get")

        eng = _Engine(_StubGraph())
        code = eng._read_interface_error_code(self._patch(extend_props_patch=_Hostile()))
        assert code is None

    def test_exec_request_input_read_missing_node_returns_none_zero(self):
        """EXECUTE gate read for a node that is NOT in the graph → (None, 0)
        (the node legitimately missing — defensive, not an error)."""
        eng = _Engine(_StubGraph(tasks=[]))
        assert eng._read_exec_request_input_and_attempt("t-x", "ghost") == (None, 0)

    def test_exec_request_input_read_graph_failure_returns_none_zero(self):
        """A graph query blow-up → (None, 0) (decision #14: never break the
        gate for a trajectory-assembly read)."""
        eng = _Engine(_StubGraph(query_raises=True))
        assert eng._read_exec_request_input_and_attempt("t-x", "n1") == (None, 0)

    def test_emit_execute_trajectory_swallows_facade_failure(self):
        """The facade emitting the EXECUTE/VERIFY row blows up → swallow +
        WARNING (fire-and-forget语义); the gate result is unaffected."""
        eng = _Engine(_StubGraph(), tcs=_BadFacade())
        patch = self._patch(exec_error="boom", extend_props_patch={})
        op = NodeOpResult(task_id="t-x", node_id="n1", success=True,
                          prev_status=Status.RUNNING, new_status=Status.HUNG)
        eng._emit_execute_trajectory(
            patch, op, action_type="execute", action_result="failed",
            is_exec_error=True,
        )  # no raise — decision #14


class TestStaticRuntimeLoaderBranches:
    def test_lazy_load_from_plans_dir_builds_runtime(self):
        """static_plan_id WITHOUT inline yaml → lazy-load from the real
        task_plan/plans/ directory → a real StaticPlanRuntime."""
        eng = _Engine(_StubGraph(execution_config={"static_plan_id": "okr-implementation"}))
        runtime = eng._static_runtime("t-x")
        assert runtime is not None

    def test_missing_template_id_lazily_degrades_to_none(self):
        """A static_plan_id whose template file does NOT exist in the plans
        repository → None (a degrade, not a failure — the engine proceeds
        without a preset runtime)."""
        eng = _Engine(_StubGraph(execution_config={
            "static_plan_id": "no-such-template",
        }))
        assert eng._static_runtime("t-x") is None

    def test_broken_static_yaml_raises_after_logging(self):
        """A broken static_plan_yaml → logger.exception + re-raise (the static
        runtime init failure is VISIBLE, not swallowed — unlike the emission
        bypass, this is a driving-path failure)."""
        eng = _Engine(_StubGraph(execution_config={
            "static_plan_yaml": "::: definitely not YAML",
        }))
        with pytest.raises(Exception):
            eng._static_runtime("t-x")


# ---------------------------------------------------------------------------
# analyzer.py — tc_bot contract edges + boost assignee fallbacks
# ---------------------------------------------------------------------------


class _FakeBot:
    def __init__(self, *, content=None, run=None, raise_exc=None) -> None:
        self._content = content
        self._run = run
        self._raise = raise_exc

    async def send_and_wait_async(self, *, bot_id, message, metadata=None,
                                 timeout=180.0, poll_interval=2.0) -> dict:
        if self._raise is not None:
            raise self._raise
        if self._run is not None:
            return self._run
        return {"result": {"content": self._content}}


class TestTcBotContractEdges:
    def _empty_trajectory(self):
        from agentclaw.community.core.task.task_context.task_trajectory.models import (
            TaskTrajectory,
        )
        return TaskTrajectory(task_id="t-x", timeline=[], analysis=None,
                              gmt_create=0, gmt_modified=0)

    def test_bot_raising_analysis_error_propagates_unwrapped(self):
        """A bot call that already raised TrajectoryAnalysisError re-raises
        AS-IS (no double-wrap with the exception-type prefix)."""
        bot = _FakeBot(raise_exc=TrajectoryAnalysisError("bot contract violated"))
        analyzer = TaskTrajectoryAnalyzer(bot=bot)
        with pytest.raises(TrajectoryAnalysisError, match="bot contract violated"):
            _run(analyzer.analyze(
                self._empty_trajectory(), lambda _ev: None,
                analysis_type="tc_bot", analysis_executor="bot-x",
            ))

    def test_non_dict_bot_response_raises_analysis_error(self):
        """A valid-JSON-but-not-an-object bot response → TrajectoryAnalysisError."""
        bot = _FakeBot(content="```json\n[1, 2]\n```")
        analyzer = TaskTrajectoryAnalyzer(bot=bot)
        with pytest.raises(TrajectoryAnalysisError, match="not a JSON object"):
            _run(analyzer.analyze(
                self._empty_trajectory(), lambda _ev: None,
                analysis_type="tc_bot", analysis_executor="bot-x",
            ))

    def test_missing_result_degrades_to_unparseable_error(self):
        """run["result"] missing/None → empty content → unparseable → 504."""
        bot = _FakeBot(run={"result": None})
        analyzer = TaskTrajectoryAnalyzer(bot=bot)
        with pytest.raises(TrajectoryAnalysisError, match="unparseable"):
            _run(analyzer.analyze(
                self._empty_trajectory(), lambda _ev: None,
                analysis_type="tc_bot", analysis_executor="bot-x",
            ))

    def test_non_str_boost_and_failure_coerced(self):
        """Structured boost/failure values from the bot are coerced to strings
        (no 504 — the content survives)."""
        bot = _FakeBot(content=json.dumps({
            "analysis_output": "ok",
            "boost_reason": 123,
            "failure_reason": ["e1", "e2"],
        }))
        analyzer = TaskTrajectoryAnalyzer(bot=bot)
        analysis = _run(analyzer.analyze(
            self._empty_trajectory(), lambda _ev: None,
            analysis_type="tc_bot", analysis_executor="bot-x",
        ))
        assert analysis.boost_reason == "123"
        assert "e1" in analysis.failure_reason


class TestBoostAssigneeFallbacks:
    """The boost derivation's assignee fallback branches, driven through the
    REAL emit → repo → assemble → rule-analyze read path."""

    def _analyze(self, repo):
        trajectory = TaskTrajectoryAssembler(repo).assemble("t-x")
        ext = _build_ext_info_lookup(repo, "t-x")
        return _run(TaskTrajectoryAnalyzer().analyze(
            trajectory, ext, analysis_type="rule", analysis_executor="rule_engine",
        ))

    def test_assignee_falls_back_to_first_candidate_bot(self):
        """Dispatch event WITHOUT action_input (no target spec recorded) → the
        boost's 选中 falls back to the rationale's first candidate bot_id."""
        repo = TaskTrajectoryRepository(_make_db())
        emit_trajectory_event(
            repo, "t-x", "n1", "dispatch",
            action_result="hit_single", action_input=None,
            ext_info={"_dispatch_rationale": {
                "strategy_name": "search", "decision_mode": "skill",
                "candidates": [{"bot_id": "fallback-bot", "recommend_score": 0.5}],
                "join_dropped": [],
            }},
        )
        analysis = self._analyze(repo)
        assert analysis.boost_reason is not None
        assert "选中=fallback-bot" in analysis.boost_reason

    def test_assignee_unknown_when_no_action_input_and_no_candidates(self):
        """Neither action_input nor candidates → 选中=unknown."""
        repo = TaskTrajectoryRepository(_make_db())
        emit_trajectory_event(
            repo, "t-x", "n1", "dispatch",
            action_result="hit_multi", action_input=None,
            ext_info={"_dispatch_rationale": {
                "strategy_name": "manual", "decision_mode": "manual",
                "candidates": [],
                "join_dropped": [],
            }},
        )
        analysis = self._analyze(repo)
        assert analysis.boost_reason is not None
        assert "选中=unknown" in analysis.boost_reason


# ---------------------------------------------------------------------------
# payloads.py / assembler.py / time_utils.py defensive branches
# ---------------------------------------------------------------------------


class TestEmitterAndAssemblerDefensiveBranches:
    def test_non_dict_ext_info_degrades_to_payload_string(self):
        """A gate passing a non-dict ext_info (usage error) → the emitter
        degrades by stringifying the whole payload under ``_payload`` instead
        of raising (decision #14 — the row still lands with schema_v)."""
        repo = TaskTrajectoryRepository(_make_db())
        emit_trajectory_event(
            repo, "t-x", "n1", "execute",
            action_result="success",
            ext_info=["not", "a", "dict"],  # type: ignore[arg-type]
        )
        rows = repo.list_events_by_task("t-x")
        assert rows, "the degraded row must still land"
        ext = json.loads(rows[0].ext_info)
        assert ext["schema_v"] == 1
        assert "_payload" in ext
        assert "not" in ext["_payload"]

    def test_real_service_facade_relays_non_dict_ext_info_too(self):
        """The same defensive branch via the REAL service facade chain
        (TaskContextService → TaskTrajectoryService.emit_trajectory_event)."""
        repo = TaskTrajectoryRepository(_make_db())
        ts = TaskTrajectoryService(
            TaskTrajectoryAssembler(repo), repo, TaskTrajectoryAnalyzer(),
            TrajectoryAnalysisConfig(analysis_bot_id="bot-analyst"),
        )
        facade = TaskContextService(ts)
        facade.emit_trajectory_event(
            "t-x", "n1", "execute", action_result="success",
            ext_info=("tuple-payload",),  # type: ignore[arg-type]
        )
        rows = repo.list_events_by_task("t-x")
        assert rows and "tuple-payload" in json.loads(rows[0].ext_info)["_payload"]

    def test_holder_projection_skips_non_dict_ext_info(self):
        """Relay's holder_id projection over a NON-dict ext_info JSON → None
        (a corrupt row never breaks the assembled trajectory)."""
        assert _holder_id_from_ext_info("[1, 2]") is None

    def test_null_storage_timestamp_degrades_to_epoch_zero(self):
        """A NULL persisted gmt_* value → 0 (the assembler's defensive default;
        never raises on a NULL row)."""
        assert storage_datetime_to_epoch_ms(None) == 0
