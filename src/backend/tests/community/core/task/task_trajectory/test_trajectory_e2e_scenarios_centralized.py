"""E2E scenario suite — CENTRALIZED orchestration mode (task_trajectory 轨迹服务).

User-facing scenario matrix this file pins (中心化调度模式; see also
``test_trajectory_e2e_scenarios_relay.py`` for the relay 接力 mode and
``test_trajectory_branch_pins.py`` for defensive branch pins):

异常原因 (error reasons — every row is persisted through the REAL
``TaskContextService → TaskTrajectoryService`` facade into a REAL in-memory
SQLite ``TaskTrajectoryRepository``, then read back via the REAL assembler +
REAL rule analyzer):
* 模态执行时底层调用接口报错 (single_bot)  — the EXECUTE row carries
  ``error_type=underlying_interface_error`` + the CONCRETE interface name and
  error description; ``ext_info.interface_error_code`` carries the code; the
  rule analyzer derives ``failure_reason`` starting with
  ``underlying_interface_error:`` including the description.
* 执行 hung 住一直不上报结果 (协作群 run_mode=multi_bot) — the SLA-timeout
  RESET row carries elapsed vs the 协作群 900s threshold (vs 单 bot 600s), the
  harness-max escalation lands the terminal HUNG transition with the
  ``exec_stuck`` reason, and the analyzer derives ``execution_timeout:``.
* 验收不通过 (single_bot + 协作群) — the VERIFY row carries
  ``action_result=accept_fail`` with ``status_to=HUNG`` (the engine's
  acceptance_failed armor), the node's ``hung_reason`` is
  ``acceptance_failed``, and the rule analyzer (terminal-status FALLBACK path —
  no dedicated transition row on this path) derives ``acceptance_failed:``.

推进原因 (progression reasons, centralized):
* 为什么派发给这个 bot 或协作群 — hit_multi (协作群) rows carry the dispatch
  rationale; the 搜推找不到-bot MISS row carries the empty-candidates
  rationale (the miss shape), and the analyzer's boost derivation handles
  candidate_count=0.
* BBS 自主接单的原因 — the REAL ``bbs_modal_executor.notify`` flow (bid →
  select → claim → dispatch) driven with fake transport ports into the real
  repo: the ``bbs_execution_started`` row's ``boost_reason`` carries
  竞价胜出 + winner bot + the winner's relay_reason (胜出原因) — the
  free-text claim reason nothing anywhere else asserts. The
  ``bbs_execution_failed`` row carries the send-failure cause + the claim
  release (bbs_owner=Null); the manager_worker group-executor form covers the
  BBS→协作群 combination.

Execution modality coverage (user req #2): single_bot
(interface_error/accept_fail), 协作群 (multi_bot hung-900s / accept_fail /
hit_multi dispatch), BBS (auction pickup + BBS→协作群 combination), plus the
relay-side mixed chain in the relay suite.

Near-e2e wiring: REAL engine gates (``CentralizedExecutionAdapter`` /
``on_report`` / ``on_harness`` / ``_prepare_into``+``_drain`` / real
``bbs_modal_executor.notify``), REAL graph service, REAL repo + REAL facade —
only the outbound transports (poller callback data, dispatcher roster, bot
bid/task calls, group executor) are stubbed.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.task.task_trajectory_repository import (
    TaskTrajectoryRepository,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    PlanResult,
    RuntimeInfo,
    Status,
    TaskCallbackData,
    TaskGraphPatch,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.task_service_support import (
    build_submit_trajectory_event_kwargs,
)
from agentclaw.community.core.task.task_context.task_context_service import (
    TaskContextService,
)
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)
from agentclaw.community.core.task.task_context.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
)
from agentclaw.community.core.task.task_context.task_trajectory.assembler import (
    TaskTrajectoryAssembler,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
)
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
    _build_ext_info_lookup,
)
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
)
from agentclaw.community.core.task.task_runner.execution_adapters import (
    CentralizedExecutionAdapter,
)
from agentclaw.community.core.task.task_runner.modal_executor import (
    bbs_modal_executor,
)
from agentclaw.community.di.task_trajectory_config import TrajectoryAnalysisConfig

# Side-effect import: registers the task ORM models on Base.metadata.
import agentclaw.community.core.task.repository.models  # noqa: F401


# ---------------------------------------------------------------------------
# Harness — in-memory SQLite + REAL repo + REAL facade (copied-in-place from
# test_trajectory_e2e_acceptance.py, with _tcs() replaced by the REAL chain)
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


def _real_facade(repo) -> TaskContextService:
    """TaskContextService → TaskTrajectoryService(payloads) — production chain."""
    ts = TaskTrajectoryService(
        TaskTrajectoryAssembler(repo), repo, TaskTrajectoryAnalyzer(),
        TrajectoryAnalysisConfig(analysis_bot_id="bot-analyst"),
    )
    return TaskContextService(ts)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _task_info(task_id: str, max_depth: int = 3, extra_cfg: dict | None = None) -> TaskInfo:
    cfg = {"MAX_DEPTH": max_depth, "BBS_MAX_DEPTH": 3, "task_type": "dynamic"}
    if extra_cfg:
        cfg.update(extra_cfg)
    return TaskInfo(
        task_id=task_id,
        task_spec=TaskSpec(
            context=Context(background="bg", title="T"),
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
    return AcceptanceResult(verdict=verdict, done_items=[], gap_items=gaps or [])


def _data(loop_task_id: str = "t1::c1", *,
          success: object = True,
          exec_error: str | None = None,
          ext_info: dict | None = None) -> TaskCallbackData:
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
    def __init__(self, children: list[TaskNode] | None = None) -> None:
        self._children = children or []

    async def plan(self, graph, target_node_id=None):
        return PlanResult(children=self._children, has_gap=False, gap_detail="done")


class _RationaleStubDispatcher:
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
            elif self._outcome == "miss":
                # mirrors the real dispatcher's miss carrier (搜推找不到bot)
                n.run_info.extend_props["miss_events"] = [
                    "no_bot_match: 搜推没有可用候选",
                ]
        return nodes


class _GroupStubRunner:
    """Runner stub whose form_coop_group proves the 协作群 path fires."""

    def __init__(self) -> None:
        self.run_calls: list[list[TaskNode]] = []
        self.groups: list[str] = []

    async def start_run(self, toDoTaskList):
        self.run_calls.append(list(toDoTaskList))
        return [True] * len(toDoTaskList)

    async def form_coop_group(self, gf):
        self.groups.append(str(gf))
        return f"grp_{len(self.groups)}"


class _CaseEngine(CentralizedExecutionAdapter):
    """CentralizedExecutionAdapter with case stubs + REAL trajectory facade."""

    def __init__(self, graph, *, tcs=None, planner=None, dispatcher=None,
                 runner=None) -> None:
        self._case_planner = planner
        self._case_dispatcher = dispatcher
        self._case_runner = runner
        super().__init__(graph, task_context_service=tcs)

    def _build_planner(self):
        return self._case_planner if self._case_planner is not None else super()._build_planner()

    def _build_dispatcher(self):
        return (
            self._case_dispatcher
            if self._case_dispatcher is not None
            else super()._build_dispatcher()
        )

    def _build_runner(self):
        return self._case_runner if self._case_runner is not None else super()._build_runner()


def _set_running_node(svc, task_id, node_id, *, run_mode: str = "single_bot",
                     assignee: str = "bot1", start_time=None,
                     harness_retries: int = 0,
                     request_input: str | None = None) -> None:
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


def _drive_submit(facade, graph_svc, *, task_id: str) -> None:
    """SUBMIT: the real SUBMIT emission path (via the REAL facade chain)."""
    task_info = _task_info(task_id)
    graph_svc.initialize_graph(task_info)
    facade.emit_trajectory_event(
        task_id, task_id, "submit",
        **build_submit_trajectory_event_kwargs(task_info, int(time.time() * 1000)),
    )


def _assemble_and_analyze(repo, *, task_id: str):
    """REAL read+analysis: assembler → service ext_info_lookup → rule analyzer."""
    trajectory = TaskTrajectoryAssembler(repo).assemble(task_id)
    ext_info_lookup = _build_ext_info_lookup(repo, task_id)
    analysis = _run(TaskTrajectoryAnalyzer().analyze(
        trajectory, ext_info_lookup,
        analysis_type="rule", analysis_executor="rule_engine",
    ))
    return trajectory, analysis


def _execute_rows(repo, task_id, action_result: str):
    return [
        r for r in repo.list_events_by_task(task_id)
        if r.action_type == "execute" and r.action_result == action_result
    ]


# A dispatch rationale with real bot candidates (single) and with ZERO
# candidates (the 搜推找不到bot miss shape).
_SAMPLE_RATIONALE: dict = {
    "strategy_name": "search",
    "decision_mode": "skill",
    "candidates": [
        {"bot_id": "bot1", "recommend_score": 0.9, "short_profile": "owns skill"},
        {"bot_id": "bot2", "recommend_score": 0.7, "short_profile": "backup"},
    ],
    "prefetch_tokens": ["存储", "分析"],
    "join_dropped": [{"bot_id": "bot9", "reason": "claim_mode_off"}],
}

_MISS_RATIONALE: dict = {
    "strategy_name": "search",
    "decision_mode": "skill",
    "candidates": [],  # 搜推找不到 bot — the empty-candidates miss shape
    "join_filter_applied": True,
    "join_dropped": [],
}


# ---------------------------------------------------------------------------
# S6 — 模态执行时底层调用接口报错 (含具体报错的接口和错误描述) — single_bot
# ---------------------------------------------------------------------------


class TestInterfaceErrorReason:
    IFACE_MSG = "调用接口 POST /api/v1/notify 失败: HTTP 500 Internal Server Error"

    def _drive(self, repo, graph_svc, *, task_id, child):
        graph_svc.add_task_nodes([_child(child, task_id)], parent_node_id=task_id)
        _set_running_node(graph_svc, task_id, child, run_mode="single_bot",
                          assignee="bot1", harness_retries=99,
                          request_input="bot-request-payload")
        eng = _CaseEngine(graph_svc, tcs=_real_facade(repo),
                          planner=_StubPlanner(),
                          dispatcher=_RationaleStubDispatcher(outcome="hit_single"),
                          runner=_GroupStubRunner())
        patch = CallbackAdapter().adapt(
            _data(loop_task_id=f"{task_id}::{child}", success=True,
                  exec_error=self.IFACE_MSG,
                  ext_info={"interface_error_code": 50401})
        )
        _run(eng.on_report(patch))
        # drains HUNG-escalation bg fallout (retries>=MAX path)
        _run(_drain_bg(eng))
        return eng

    def test_interface_error_row_carries_interface_and_description(self):
        """The EXECUTE(failed) row must carry the error_type + the CONCRETE
        interface name + error description + the surfaced interface_error_code."""
        repo = TaskTrajectoryRepository(_make_db())
        graph_svc = TaskGraphService()
        task_id, child = "cee-iface", "c1"
        graph_svc.initialize_graph(_task_info(task_id))
        self._drive(repo, graph_svc, task_id=task_id, child=child)

        failed = _execute_rows(repo, task_id, "failed")
        failed_msg = [(r.action_type, r.action_result) for r in repo.list_events_by_task(task_id)]
        assert failed, f"no execute(failed) row; got {failed_msg}"
        rec = failed[0]
        assert rec.error_type == ReasonCatalog.UNDERLYING_INTERFACE_ERROR.value
        # 具体报错的接口 + 错误描述 persisted verbatim (truncated ≤500)
        assert "POST /api/v1/notify" in rec.error_msg
        assert "HTTP 500 Internal Server Error" in rec.error_msg
        assert json.loads(rec.ext_info)["interface_error_code"] == "50401"

    def test_analyzer_derives_underlying_interface_error_with_description(self):
        """The REAL rule analyzer (via the REAL assembler + ext_info re-query)
        derives failure_reason='underlying_interface_error: <desc>'."""
        repo = TaskTrajectoryRepository(_make_db())
        graph_svc = TaskGraphService()
        task_id, child = "cee-iface2", "c1"
        graph_svc.initialize_graph(_task_info(task_id))
        self._drive(repo, graph_svc, task_id=task_id, child=child)

        _trajectory, analysis = _assemble_and_analyze(repo, task_id=task_id)
        assert analysis.failure_reason is not None
        assert analysis.failure_reason.startswith("underlying_interface_error:")
        assert "POST /api/v1/notify" in analysis.failure_reason
        assert "HTTP 500" in analysis.failure_reason


# ---------------------------------------------------------------------------
# S7 — 执行 hung 住一直不上报结果 (协作群 multi_bot 900s SLA vs 单 bot 600s)
# ---------------------------------------------------------------------------


class TestHungNoReportReason:
    def test_group_hung_sla_breach_and_hung_reason_recorded(self):
        """A 协作群 node that never reports back: the SLA-timeout RESET row
        carries the 协作群 900s threshold (not the single-bot 600s) + elapsed
        since start; the harness-max escalation lands the HUNG transition with
        the exec_stuck reason; the analyzer derives execution_timeout:."""
        repo = TaskTrajectoryRepository(_make_db())
        graph_svc = TaskGraphService()
        task_id, child = "cee-hung", "c1"
        graph_svc.initialize_graph(_task_info(task_id))

        # the node started 10s ago in multi_bot (协作群) mode and no report lands
        t0 = int(time.time() * 1000)
        start_time = t0 - 10_000
        _set_running_node(graph_svc, task_id, child, run_mode="coop_group",
                          assignee="grp-driver", start_time=start_time,
                          harness_retries=0)
        facade = _real_facade(repo)
        eng = _CaseEngine(graph_svc, tcs=facade, planner=_StubPlanner(),
                          dispatcher=_RationaleStubDispatcher(outcome="hit_single"),
                          runner=_GroupStubRunner())
        # SLA breach (no report) → RESET(sla_timeout): elapsed vs GROUP 900s
        _run(eng.on_harness(_patch(task_id, child, status=Status.PENDING,
                                   extend_props_patch={"harness_reset": "timeout"})))
        reset_rows = [r for r in repo.list_events_by_task(task_id)
                      if r.action_type == "reset"]
        assert reset_rows, "no reset row for the never-reporting node"
        rec = reset_rows[0]
        assert rec.action_result == "sla_timeout"
        ext = json.loads(rec.ext_info)
        # 协作群 SLA = 900s (900_000ms) — the group threshold, not single 600s
        assert ext["sla_threshold_ms"] == 900_000
        assert 9_000 <= ext["elapsed_ms"] <= 60_000  # started 10s ago
        # NO execute row ever landed — the node never reported a result
        assert not _execute_rows(repo, task_id, "success")
        assert not _execute_rows(repo, task_id, "failed")

        # harness retries exhausted → terminal HUNG with the exec_stuck reason
        _set_running_node(graph_svc, task_id, child, run_mode="coop_group",
                          assignee="grp-driver", start_time=start_time,
                          harness_retries=3)
        async def _hung():
            await eng.on_harness(_patch(task_id, child, exec_error="exec_failed_retry"))
            for bg in list(eng._bg_tasks):
                try:
                    await (asyncio.wrap_future(bg) if isinstance(bg, asyncio.Future) else bg)
                except Exception:  # noqa: BLE001  swallow bg fallout in test
                    pass
        _run(_hung())
        hung = [r for r in repo.list_events_by_task(task_id)
                if r.action_type == "transition" and r.status_to == Status.HUNG]
        assert hung, "no HUNG transition after harness exhaustion"
        assert hung[0].error_type == ReasonCatalog.HUNG.value
        assert hung[0].error_msg == "exec_stuck"  # the hung-with-no-report reason

        # REAL rule analyzer: bullet 1 (sla_timeout RESET) wins over bullet 4
        _trajectory, analysis = _assemble_and_analyze(repo, task_id=task_id)
        assert analysis.failure_reason is not None
        assert analysis.failure_reason.startswith("execution_timeout:")
        assert "900000" in analysis.failure_reason  # the 协作群 threshold
        assert "阈值" in analysis.failure_reason


# ---------------------------------------------------------------------------
# S8 — 验收不通过 (single_bot + 协作群): accept_fail → HUNG → acceptance_failed
# ---------------------------------------------------------------------------


class TestAcceptanceFailReason:
    def _drive(self, repo, graph_svc, *, task_id, child, run_mode="single_bot"):
        graph_svc.add_task_nodes([_child(child, task_id)], parent_node_id=task_id)
        _set_running_node(graph_svc, task_id, child, run_mode=run_mode,
                          assignee="bot1" if run_mode == "single_bot" else "grp-driver",
                          request_input="bot-request-payload")
        eng = _CaseEngine(graph_svc, tcs=_real_facade(repo),
                          planner=_StubPlanner(),
                          dispatcher=_RationaleStubDispatcher(outcome="hit_single"),
                          runner=_GroupStubRunner())
        patch = _patch(
            task_id, child,
            acceptance_result=_accept(AcceptanceVerdict.FAILED, gaps=["证据链不完整"]),
        )
        _run(eng.on_report(patch))
        _run(_drain_bg(eng))
        return eng

    def _assert_chain(self, repo, graph_svc, *, task_id, child):
        # the VERIFY row carries the accept_fail verdict
        verify_rows = [r for r in repo.list_events_by_task(task_id)
                       if r.action_type == "verify"]
        rows_msg = [(r.action_type, r.action_result) for r in repo.list_events_by_task(task_id)]
        assert verify_rows, f"no verify row; got {rows_msg}"
        vf = verify_rows[-1]
        assert vf.action_result == "accept_fail"
        assert vf.status_to == Status.HUNG  # the engine's acceptance_failed armor
        # the node itself is HUNG with the acceptance_failed reason
        node = graph_svc._get_node(graph_svc._graphs[task_id], child)
        assert node.status == Status.HUNG
        assert node.run_info.extend_props.get("hung_reason") == "acceptance_failed"
        # REAL rule analyzer — the terminal-fallback path (no dedicated
        # transition row) yields the 接单 acceptance_failed reason
        _trajectory, analysis = _assemble_and_analyze(repo, task_id=task_id)
        assert analysis.failure_reason is not None
        assert analysis.failure_reason.startswith("acceptance_failed:"), (
            f"got {analysis.failure_reason!r}"
        )
        # the acceptance gaps are visible on the trajectory's VERIFY error side
        verify_failed = [r for r in _trajectory.timeline
                         if getattr(r.action_type, "value", r.action_type) == "verify"]
        assert verify_failed[-1].action_result == "accept_fail"

    def test_acceptance_fail_single_bot_full_chain(self):
        repo = TaskTrajectoryRepository(_make_db())
        graph_svc = TaskGraphService()
        task_id, child = "cee-acc-single", "c1"
        graph_svc.initialize_graph(_task_info(task_id))
        self._drive(repo, graph_svc, task_id=task_id, child=child)
        self._assert_chain(repo, graph_svc, task_id=task_id, child=child)

    def test_acceptance_fail_collab_group_full_chain(self):
        """协作群 verification FAIL — same chain, group run_mode."""
        repo = TaskTrajectoryRepository(_make_db())
        graph_svc = TaskGraphService()
        task_id, child = "cee-acc-group", "c1"
        graph_svc.initialize_graph(_task_info(task_id))
        self._drive(repo, graph_svc, task_id=task_id, child=child, run_mode="coop_group")
        self._assert_chain(repo, graph_svc, task_id=task_id, child=child)


# ---------------------------------------------------------------------------
# S5+S2 (centralized) — 搜推找不到 bot: the MISS dispatch rationale shape
# ---------------------------------------------------------------------------


class TestCentralizedSearchEmptyDispatch:
    def test_miss_dispatch_row_carries_empty_candidates_rationale(self):
        """Centralized mode, 搜推找不到 bot: the real engine dispatch gate
        persists a ``dispatch(miss)`` row whose ext_info rationale carries the
        empty candidates (the reason the leg could not be dispatched)."""
        repo = TaskTrajectoryRepository(_make_db())
        graph_svc = TaskGraphService()
        task_id, child = "cee-miss", "c1"
        graph_svc.initialize_graph(_task_info(task_id))
        graph_svc.add_task_nodes([_child(child, task_id)], parent_node_id=task_id)
        eng = _CaseEngine(
            graph_svc, tcs=_real_facade(repo),
            planner=_StubPlanner(),
            dispatcher=_RationaleStubDispatcher(
                outcome="miss", rationale=_MISS_RATIONALE,
            ),
            runner=_GroupStubRunner(),
        )
        side: list[tuple] = []
        _run(eng._prepare_into(task_id, side))
        miss_patches = [p for kind, *rest in side if kind == "miss" for p in rest]
        for m in miss_patches:
            _run(eng.on_miss(m))

        rows = [r for r in repo.list_events_by_task(task_id) if r.action_type == "dispatch"]
        assert rows, "no dispatch row for the miss case"
        rec = rows[0]
        assert rec.action_result == "miss"
        assert rec.status_from == Status.PENDING and rec.status_to == Status.PENDING
        ext = json.loads(rec.ext_info)
        rationale = ext["_dispatch_rationale"]
        assert rationale["candidates"] == []  # the 搜推找不到 shape itself
        assert rationale["strategy_name"] == "search"

        # REAL analyzer reads the persisted ext_info: boost derives with 候选0
        # (no bot found), no failure verdict (the task is not terminal — the
        # harness will retry / BBS).
        _trajectory, analysis = _assemble_and_analyze(repo, task_id=task_id)
        assert analysis.boost_reason is not None
        assert "候选0" in analysis.boost_reason
        assert analysis.failure_reason is None  # not terminal — no false verdict


# ---------------------------------------------------------------------------
# S3 (centralized) — BBS 自主接单的原因: real bbs_modal_executor.notify flow
# ---------------------------------------------------------------------------


class _FakeBcn:
    """BcnService roster port: two claim-enabled bots on the BBS 广场."""

    def list_bots_by_task_modes(self, *, claim=None, dream=None, match=None,
                                visibility=None):
        return [{"bot_id": "bidder-a"}, {"bot_id": "bidder-b"}]


class _FakeBidBot:
    """OpenApiBotPort fake. 1st call per bot = the BID evaluation (returns
    completion_rate + relay_reason); the winner's 2nd call = the actual task
    (optionally raising to pin the dispatch-failure path)."""

    def __init__(self, *, rates: dict[str, int], task_send_raises: bool = False) -> None:
        self._rates = rates
        self._raises = task_send_raises
        self._calls: dict[str, int] = {}

    async def send_and_wait_async(self, *, bot_id, message, metadata=None,
                                  timeout=180.0, poll_interval=2.0) -> dict:
        n = self._calls.get(bot_id, 0) + 1
        self._calls[bot_id] = n
        if n == 1:
            return {
                "status": "COMPLETED",
                "result": {"content": json.dumps({
                    "completion_rate": self._rates[bot_id],
                    "relay_reason": f"{bot_id} 具备存储行业分析技能且当前负载最低",
                    "title": "存储架构分析",
                    "goal": "完成存储架构分析",
                })},
            }
        # the actual task send to the winner
        if self._raises:
            raise RuntimeError("下游消息通道投递失败: connection refused")
        return {
            "status": "COMPLETED",
            "result": {"content": "BBS 棒产出: 存储架构分析完成"},
            "session_id": "sess-bbs-1",
        }


async def _drain_bg_tasks(engine) -> None:
    for bg in list(engine._bg_tasks):
        try:
            if isinstance(bg, asyncio.Future):
                await bg
            else:
                await asyncio.wrap_future(bg)
        except Exception:  # noqa: BLE001  swallow bg fallout in test
            pass


def _drain_bg(engine):
    return _drain_bg_tasks(engine)


class TestBbsPickupReason:
    """The REAL BBS auction flow (bid → select → claim → dispatch) driven into
    the real repo through the REAL facade. Nothing anywhere else asserts the
    竞价胜出 reason lands on a persisted row — this pins scenario S3."""

    def test_bid_winner_reason_persisted_on_execution_started_row(self):
        repo = TaskTrajectoryRepository(_make_db())
        graph_svc = TaskGraphService()
        task_id = "cee-bbs"
        graph_svc.initialize_graph(_task_info(task_id))
        graph_svc.update_task_graph_info(
            task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True})
        )
        facade = _real_facade(repo)
        engine = _CaseEngine(graph_svc, tcs=facade)
        bot = _FakeBidBot(rates={"bidder-a": 90, "bidder-b": 40})

        async def _go():
            await bbs_modal_executor.notify(
                graph_svc.query_task_dashboard(task_id),
                bcn=_FakeBcn(),
                bot=bot,
                graph=graph_svc,
                backend_url="http://test-backend",
                on_bbs_report=engine.on_bbs_report,
                task_context_service=facade,
            )

        _run(_go())

        records = repo.list_events_by_task(task_id)
        # milestone rows in order
        entered = [r for r in records if r.action_result == "bbs_entered"]
        broadcast = [r for r in records if r.action_result == "bbs_bid_broadcast"]
        started = [r for r in records if r.action_result == "bbs_execution_started"]
        assert entered and entered[0].boost_reason == "进入BBS模态"
        assert broadcast and json.loads(broadcast[0].ext_info)["candidate_count"] == 2
        assert started, f"no bbs_execution_started row; got {[r.action_result for r in records]}"
        rec = started[0]
        # S3 — the 自主接单 reason: who won the auction and WHY
        assert "竞价胜出" in rec.boost_reason
        assert "bidder-a" in rec.boost_reason  # the higher completion_rate wins
        assert "存储行业分析技能且当前负载最低" in rec.boost_reason  # the relay_reason
        ext = json.loads(rec.ext_info)
        assert ext["winner_bot_id"] == "bidder-a"
        assert ext["execution_mode"] == "bbs"  # group_executor None → straight bbs
        # the scoped bbs node was created + executed + reported back
        scoped = [n for n in graph_svc.query_task_dashboard(task_id).tasks
                  if n.node_id.startswith("bbs-")]
        assert scoped, "the BBS scoped node was never added"
        assert scoped[0].run_info.assignee == "bidder-a"

    def test_bbs_to_group_execution_combination(self):
        """BBS → 协作群 combination: with a group_executor wired the auction
        winner runs in the manager_worker 协作群 form and the reason row
        records execution_mode=coop_group."""
        repo = TaskTrajectoryRepository(_make_db())
        graph_svc = TaskGraphService()
        task_id = "cee-bbs-group"
        graph_svc.initialize_graph(_task_info(task_id))
        graph_svc.update_task_graph_info(
            task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True})
        )
        facade = _real_facade(repo)
        engine = _CaseEngine(graph_svc, tcs=facade)
        bot = _FakeBidBot(rates={"bidder-a": 90, "bidder-b": 40})

        async def _group_executor(**kwargs):
            assert kwargs["winner_bot_id"] == "bidder-a"
            return {
                "status": "COMPLETED",
                "result": {"content": "协作群 manager_worker 产出"},
            }

        async def _go():
            await bbs_modal_executor.notify(
                graph_svc.query_task_dashboard(task_id),
                bcn=_FakeBcn(),
                bot=bot,
                graph=graph_svc,
                backend_url="http://test-backend",
                on_bbs_report=engine.on_bbs_report,
                group_executor=_group_executor,
                task_context_service=facade,
            )

        _run(_go())
        started = [r for r in repo.list_events_by_task(task_id)
                   if r.action_result == "bbs_execution_started"]
        assert started
        ext = json.loads(started[0].ext_info)
        assert ext["execution_mode"] == "coop_group"  # the BBS→协作群 combination
        assert ext["winner_bot_id"] == "bidder-a"

    def test_bbs_task_send_failure_records_cause_and_releases_claim(self):
        """The winner's actual task send blows up → the bbs_execution_failed
        row carries the transport cause + exception_type, and the claim is
        RELEASED (bbs_owner=None) so the next BBS round is not blocked."""
        repo = TaskTrajectoryRepository(_make_db())
        graph_svc = TaskGraphService()
        task_id = "cee-bbs-fail"
        graph_svc.initialize_graph(_task_info(task_id))
        graph_svc.update_task_graph_info(
            task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True})
        )
        facade = _real_facade(repo)
        engine = _CaseEngine(graph_svc, tcs=facade)
        bot = _FakeBidBot(rates={"bidder-a": 90, "bidder-b": 40},
                          task_send_raises=True)

        async def _go():
            await bbs_modal_executor.notify(
                graph_svc.query_task_dashboard(task_id),
                bcn=_FakeBcn(),
                bot=bot,
                graph=graph_svc,
                backend_url="http://test-backend",
                on_bbs_report=engine.on_bbs_report,
                task_context_service=facade,
            )

        _run(_go())

        records = repo.list_events_by_task(task_id)
        started = [r for r in records if r.action_result == "bbs_execution_started"]
        failed = [r for r in records if r.action_result == "bbs_execution_failed"]
        assert started, "the winner reason row fired before the send attempt"
        assert failed, f"no bbs_execution_failed row; got {[r.action_result for r in records]}"
        rec = failed[0]
        # boost_reason degrades to the action_result token on the error path
        assert rec.boost_reason == "bbs_execution_failed"
        assert rec.error_msg and "connection refused" in rec.error_msg
        ext = json.loads(rec.ext_info)
        # the CONCRETE cause: exception type + winner identity
        assert ext["exception_type"] == "RuntimeError"
        assert ext["winner_bot_id"] == "bidder-a"
        # the claim was released — a stale bbs_owner must not block the retry
        snapshot = graph_svc.query_task_dashboard(task_id)
        assert (snapshot.extend_props or {}).get("bbs_owner") is None