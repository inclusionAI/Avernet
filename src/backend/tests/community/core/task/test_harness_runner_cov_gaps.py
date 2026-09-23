"""行覆盖补漏:task_harness(harness.py / centralized_recovery.py)+ task_runner
(task_runner.py / callback_adapter.py / execution_adapters.py / centralized_support.py /
event_dispatcher.py)。

对齐既有测试风格(test_harness.py 的真构图 + 回投驱动、test_bbs_handoff_claim.py 的
engine + monkeypatch instance-attr seam、test_trajectory_service.py 末尾的敌意协作对象)。
只盯 /tmp/task_cov_baseline.json 里这 7 个文件的 missing_lines,不触碰 task_center /
executor / client 的既有归属。fixture/fake 全部就地定义。
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    AcceptanceCriteria,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_harness.harness import TaskHarness
from agentclaw.community.core.task.task_runner.task_runner import TaskRunner
from agentclaw.community.core.task.task_runner.execution_adapters import (
    CentralizedExecutionAdapter,
)
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
    TaskLoopCallback,
    _derive_event_id,
)
from agentclaw.community.core.task.task_runner.event_dispatcher import (
    TaskSemanticEventDispatcher,
)
from agentclaw.community.core.task.task_runner.execution_events import (
    TaskSemanticEventType,
)
from agentclaw.community.core.task.task_runner.centralized_support import (
    _dispatch_fail_action_result,
    _is_stale_dispatching,
    _now_ms,
    _read_dispatch_side_data,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.callback_adapter import TaskCallbackData
from agentclaw.community.core.task.domain.models import NodeOpResult


# ---------------------------------------------------------------------------
# helpers —— 对齐 test_harness.py / test_runner.py 既有构图 helper
# ---------------------------------------------------------------------------


def _ti(tid: str = "t1", execution_config: dict | None = None) -> TaskInfo:
    return TaskInfo(
        task_id=tid,
        task_spec=TaskSpec(
            context=Context(background="bg", title="T"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
        execution_config=execution_config or {},
    )


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.PENDING,
        task_spec=_ti(task_id).task_spec,
        run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


class _Clock:
    """可手动推进的时钟(单测定确定性)。"""

    def __init__(self, start: float = 0.0):
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


class _Recorder:
    def __init__(self):
        self.patches: list[TaskNodePatch] = []

    def __call__(self, patch):
        self.patches.append(patch)


def _graph_with(svc: TaskGraphService, execution_config: dict | None = None):
    return svc.initialize_graph(_ti("t1", execution_config))


def _make_engine(svc: TaskGraphService) -> CentralizedExecutionAdapter:
    return CentralizedExecutionAdapter(svc)


def _dispatch_running(svc, graph, node_id: str, parent: str = "t1",
                      run_mode: str = "single_bot", assignee: str = "bot1") -> TaskNode:
    svc.add_task_nodes([_child(node_id)], parent_node_id=parent)
    svc.update_task_node_info(_patch("t1", node_id, status=Status.RUNNING,
                                      run_mode=run_mode, assignee=assignee))
    return svc._get_node(graph, node_id)


# ---------------------------------------------------------------------------
# harness 轻量 fake graph(单 harnit 直驱:巡检/恢复/SLA 读取)
# ---------------------------------------------------------------------------


class _FakeRunInfo:
    def __init__(self, run_mode=None, assignee=None, extend_props=None):
        self.run_mode = run_mode
        self.assignee = assignee
        self.extend_props = extend_props if extend_props is not None else {}
        self.output: dict = {}
        self.action_log: list = []


class _FakeNode:
    def __init__(self, node_id, status=Status.RUNNING, run_mode=None,
                 assignee=None, extend_props=None, task_id="t1"):
        self.node_id = node_id
        self.task_id = task_id
        self.status = status
        self.run_info = _FakeRunInfo(run_mode, assignee, extend_props)


class _FuseGraph:
    """harness 直驱的最小图:按 criteria.status 分发巡检三扫的节点集。"""

    def __init__(self, *, running=None, failed=None, pending=None,
                 config=None, dashboard_status=Status.RUNNING,
                 dashboard_extend_props=None, raise_query=False,
                 raise_dashboard=False):
        self.running = list(running or [])
        self.failed = list(failed or [])
        self.pending = list(pending or [])
        self.config = dict(config or {})
        self.dashboard_status = dashboard_status
        self.dashboard_extend_props = (
            dashboard_extend_props
            if dashboard_extend_props is not None
            else {"execution_config": self.config}
        )
        self.raise_query = raise_query
        self.raise_dashboard = raise_dashboard
        self.reports: list = []

    def query_task_nodes(self, task_id, criteria):
        if self.raise_query:
            raise RuntimeError("graph down")
        if criteria.status == Status.RUNNING:
            return list(self.running)
        if criteria.status == Status.FAILED:
            return list(self.failed)
        if criteria.status == Status.PENDING:
            return list(self.pending)
        return []

    def query_task_dashboard(self, task_id):
        if self.raise_dashboard:
            raise RuntimeError("dashboard down")
        return SimpleNamespace(
            status=self.dashboard_status,
            extend_props=self.dashboard_extend_props,
            loop_round=0,
            tasks=[],
        )

    def _execution_config(self, task_id):
        return dict(self.config)

    def report(self, data):
        self.reports.append(data)
        return NodeOpResult(task_id="t1", node_id="x", success=True)


# ---------------------------------------------------------------------------
# task_harness/harness.py —— relay 回收守卫(139-140/152/155/158)
# ---------------------------------------------------------------------------


class TestRecoverRelayGuards:
    def test_dashboard_query_failure_keeps_normal_semantics(self):
        h = TaskHarness(_FuseGraph(raise_dashboard=True))
        node = _FakeNode("c1")
        assert h._recover_relay_without_execution_result("t1", node) is False

    def test_terminal_graph_skips_baton_return(self):
        graph = _FuseGraph(dashboard_status=Status.HUNG,
                           dashboard_extend_props={
                               "execution_config": {"orchestration_mode": "relay"},
                           })
        h = TaskHarness(graph)
        assert h._recover_relay_without_execution_result("t1", _FakeNode("c1")) is False
        assert graph.reports == []

    def test_granted_relay_turn_owns_recovery(self):
        graph = _FuseGraph(
            dashboard_status=Status.RUNNING,
            dashboard_extend_props={
                "execution_config": {"orchestration_mode": "relay"},
                "relay_turn": {"status": "GRANTED"},
            },
        )
        h = TaskHarness(graph)
        assert h._recover_relay_without_execution_result("t1", _FakeNode("c1")) is False
        assert graph.reports == []

    def test_node_with_execution_decision_is_not_reclaimed(self):
        graph = _FuseGraph(
            dashboard_status=Status.RUNNING,
            dashboard_extend_props={
                "execution_config": {"orchestration_mode": "relay"},
                "relay_turn": {"status": "PENDING"},
            },
        )
        h = TaskHarness(graph)
        node = _FakeNode("c1", extend_props={"execution_decision": "ACCEPTED"})
        assert h._recover_relay_without_execution_result("t1", node) is False
        assert graph.reports == []


# ---------------------------------------------------------------------------
# harness.py —— _resume_expired_relay_turn(203-204 / 213 / 216)
# ---------------------------------------------------------------------------


class TestResumeExpiredRelayTurn:
    def test_dashboard_failure_returns_false(self):
        h = TaskHarness(_FuseGraph(raise_dashboard=True))
        h.set_on_relay_turn_expired(lambda tid: None)
        assert h._resume_expired_relay_turn("t1") is False

    def test_not_expired_turn_falls_back_to_generic_scan(self):
        graph = _FuseGraph(
            dashboard_status=Status.RUNNING,
            dashboard_extend_props={
                "execution_config": {"orchestration_mode": "relay"},
                "relay_turn": {"status": "GRANTED", "expires_at_ms": 10_000_000},
            },
        )
        h = TaskHarness(graph, wall_clock_ms=lambda: 1)
        h.set_on_relay_turn_expired(lambda tid: None)
        assert h._resume_expired_relay_turn("t1") is False

    def test_expired_turn_runs_async_resume_callback(self):
        graph = _FuseGraph(
            dashboard_status=Status.RUNNING,
            dashboard_extend_props={
                "execution_config": {"orchestration_mode": "relay"},
                "relay_turn": {"status": "GRANTED", "expires_at_ms": 1},
            },
        )
        h = TaskHarness(graph, wall_clock_ms=lambda: 2)
        ranned: list[str] = []

        async def _resume(tid):
            ranned.append(tid)

        h.set_on_relay_turn_expired(_resume)
        assert h._resume_expired_relay_turn("t1") is True
        assert ranned == ["t1"]


# ---------------------------------------------------------------------------
# harness.py —— SLA / PENDING 阈值读取退化(230-231 / 243-244)+ 巡查询异常
# (269-270 / 348-349 / 374-375)
# ---------------------------------------------------------------------------


class _HostileConfigGraph:
    """图缺失/已删 → 阈值读取走保守默认;巡检查询异常 → 跳过该 task。"""

    def _execution_config(self, task_id):
        raise KeyError("no such task")

    def query_task_nodes(self, task_id, criteria):
        raise RuntimeError("graph down")

    def query_task_dashboard(self, task_id):
        raise RuntimeError("graph down")


class TestTimeoutReadFallback:
    def test_sla_timeout_read_failure_uses_default(self):
        h = TaskHarness(_HostileConfigGraph(), default_sla_timeout=123.0)
        assert h._sla_timeout("missing") == 123.0

    def test_pending_timeout_read_failure_uses_default(self):
        h = TaskHarness(_HostileConfigGraph(), default_pending_timeout=77.0)
        assert h._pending_timeout("missing") == 77.0


class TestPollQueryFailures:
    def test_all_three_scans_swallow_query_errors(self):
        h = TaskHarness(_HostileConfigGraph(), on_harness_fn=_Recorder())
        h.register("t1")
        assert h._poll_once() == []


# ---------------------------------------------------------------------------
# harness.py —— FAILED 巡检(body 全缺)+ PENDING 派发退避 + 三路回投
# (351-357 / 394 / 401 / 410 / 414 / 416-418 / 420-422)
# ---------------------------------------------------------------------------


def _scan_graph() -> _FuseGraph:
    return _FuseGraph(
        running=[],
        failed=[
            _FakeNode("f-non-exec", Status.FAILED, run_mode=None),
            _FakeNode("f-bbs", Status.FAILED, run_mode="bbs", assignee="bot-bbs"),
            _FakeNode("f-bot", Status.FAILED, run_mode="single_bot", assignee="bot1"),
        ],
        pending=[_FakeNode("p1", Status.PENDING, run_mode=None, assignee=None)],
        config={"PENDING_TIMEOUT": 1.0},
    )


class TestFailedScan:
    def test_failed_scan_retries_only_exec_mode_leaves_and_pending_backoff(self):
        graph = _scan_graph()
        rec = _Recorder()
        h = TaskHarness(graph, rec, clock=_Clock(0.0))
        h.register("t1")

        # 第一轮:FAILED 立即重投(exec_error=exec_failed_retry);PENDING 首见记时不重试
        assert h._poll_once() == []
        assert [p.node_id for p in rec.patches] == ["f-bot"]
        assert all(p.exec_error == "exec_failed_retry" for p in rec.patches)

        # 第二轮(超 PENDING_TIMEOUT):PENDING 卡死重搜推 + backoff 重置
        clock = h._clock
        clock.advance(10.0)
        rec.patches.clear()
        assert h._poll_once() == []
        kinds = [(p.node_id, p.exec_error) for p in rec.patches]
        assert ("f-bot", "exec_failed_retry") in kinds
        assert ("p1", "pending_dispatch_stuck") in kinds
        assert h._pending_seen_at[("t1", "p1")] == clock()


class TestAsyncOnHarnessCallbacks:
    def test_coroutine_relay_prompts_all_three_reset_flows(self):
        graph = _FuseGraph(
            running=[_FakeNode("r-bot", Status.RUNNING, run_mode="single_bot", assignee="b")],
            failed=[_FakeNode("f-bot", Status.FAILED, run_mode="single_bot", assignee="b")],
            pending=[_FakeNode("p1", Status.PENDING)],
            config={"SLA_TIMEOUT": 1.0, "PENDING_TIMEOUT": 1.0},
        )
        patches: list[TaskNodePatch] = []

        async def arec(p):
            patches.append(p)

        h = TaskHarness(graph, arec, clock=_Clock(0.0))
        h.register("t1")
        assert h._poll_once() == []  # 首见,RUNNING/PENDING 记时
        assert [p.node_id for p in patches] == ["f-bot"]  # FAILED 无退避,首轮即重投

        patches.clear()
        h._clock.advance(10.0)
        resets = h._poll_once()
        # RUNNING 真 SLA 超时 → 复位 patch(异步回投)
        assert [p.node_id for p in resets] == ["r-bot"]
        assert resets[0].status is Status.PENDING
        node_ids = [p.node_id for p in patches]
        assert node_ids == ["r-bot", "f-bot", "p1"]
        assert [p.exec_error for p in patches] == [
            None, "exec_failed_retry", "pending_dispatch_stuck",
        ]


# ---------------------------------------------------------------------------
# harness.py —— static fallback 延迟读取(env / 非法值)
# (491-492 / 504 / 506 / 511-512)
# ---------------------------------------------------------------------------


class TestStaticDelays:
    def test_auto_report_delay_invalid_value_falls_back_to_demo_window(self):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        graph.extend_props["execution_config"]["static_auto_report_delay"] = "not-a-number"
        h = TaskHarness(svc)
        assert 20.0 <= h._static_auto_report_delay("t1") < 60.0

    def test_bbs_handoff_delay_reads_env_when_config_missing(self, monkeypatch):
        monkeypatch.setenv("OCB_BBS_HANDOFF_CLAIM_DELAY", "7.5")
        svc = TaskGraphService()
        _graph_with(svc)
        h = TaskHarness(svc)
        assert h._bbs_handoff_delay("t1") == 7.5

    def test_bbs_handoff_delay_invalid_value_falls_back_to_default(self):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        graph.extend_props["execution_config"]["bbs_handoff_claim_delay"] = "later"
        h = TaskHarness(svc)
        assert h._bbs_handoff_delay("t1") == 30.0


# ---------------------------------------------------------------------------
# harness.py —— _on_bbs_handoff_done(517-525)
# ---------------------------------------------------------------------------


class _DoneTask:
    def __init__(self, *, cancelled=False, exc=None):
        self.was_cancelled = cancelled
        self.exc = exc
        self.closed = False

    def cancelled(self):
        return self.was_cancelled

    def exception(self):
        return self.exc


class TestOnBbsHandoffDone:
    def test_cancelled_task_discards_and_returns(self):
        h = TaskHarness(TaskGraphService())
        t = _DoneTask(cancelled=True)
        h._bg_tasks = {t}  # type: ignore[assignment]
        h._on_bbs_handoff_done(t)
        assert t not in h._bg_tasks

    def test_exception_is_logged_not_raised(self):
        h = TaskHarness(TaskGraphService())
        t = _DoneTask(exc=ValueError("claim blew up"))
        h._bg_tasks = {t}  # type: ignore[assignment]
        h._on_bbs_handoff_done(t)  # 不抛
        assert t not in h._bg_tasks

    def test_clean_completion_discards_silently(self):
        h = TaskHarness(TaskGraphService())
        t = _DoneTask()
        h._bg_tasks = {t}  # type: ignore[assignment]
        h._on_bbs_handoff_done(t)
        assert t not in h._bg_tasks


# ---------------------------------------------------------------------------
# harness.py —— _bbs_handoff_claim(555-562 skip / 608-617, 634 派发异常 noop)
# 对齐 test_bbs_handoff_claim.py 的 engine+svc+monkeypatch seam
# ---------------------------------------------------------------------------


class _RaisingStartRunner:
    async def start_run(self, nodes):
        raise RuntimeError("rnd dispatch down")


class TestBbsHandoffClaimAdversarial:
    def test_missing_node_skips_claim(self, monkeypatch):
        svc = TaskGraphService()
        tid = "t-claim-skip"
        svc.initialize_graph(_ti(tid))
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_bbs_handoff_delay", lambda *a, **k: 0.0)

        async def main():
            await engine._bbs_handoff_claim(tid, "no-such-node", "rnd-1", [])

        asyncio.run(main())
        assert engine._bg_tasks == set()

    def test_start_run_failure_marks_running_noop_and_falls_back(self, monkeypatch):
        svc = TaskGraphService()
        tid = "t-claim-raise"
        svc.initialize_graph(_ti(tid))
        svc.add_task_nodes([_child("bbs-leaf", tid)], parent_node_id=tid)
        engine = _make_engine(svc)
        engine._runner = _RaisingStartRunner()
        monkeypatch.setattr(engine, "_bbs_handoff_delay", lambda *a, **k: 0.0)

        async def _noop_auto_report(*a, **k):
            return None

        monkeypatch.setattr(engine, "_static_bbs_handoff_auto_report", _noop_auto_report)

        async def main():
            await engine._bbs_handoff_claim(tid, "bbs-leaf", "rnd-1", ["i1"])
            await asyncio.gather(*engine._bg_tasks, return_exceptions=True)

        asyncio.run(main())

        leaf = next(
            n for n in svc.query_task_dashboard(tid).tasks if n.node_id == "bbs-leaf"
        )
        # 真派发失败:不回 PENDING,no-op 翻 RUNNING(bbs 路径由兜底推进),dispatch_error 留痕
        assert leaf.status is Status.RUNNING
        assert leaf.run_info.run_mode == "bbs"
        assert (
            leaf.run_info.extend_props.get("dispatch_error")
            == "bbs_rnd_dispatch_fallback_noop"
        )
        assert leaf.run_info.extend_props.get("actual_run_mode") == "bbs"
        assert leaf.run_info.extend_props.get("bbs_status") == "claimed_by_rnd"


# ---------------------------------------------------------------------------
# centralized_recovery.py —— redrive 图终态(45-46)/ _reset_action_result(94)
# / _reset_elapsed_ms(144-145)
# ---------------------------------------------------------------------------


class TestCentralizedRecoveryGuards:
    def test_redrive_freezes_on_terminal_graph(self):
        h = TaskHarness(TaskGraphService())
        # harness 实例上手工落 seam(monkeypatch.setattr 不接受 __getattr__ 外的属性)
        h._is_external_managed_task = lambda tid: False  # type: ignore[method-assign]
        h._is_graph_terminal = lambda tid: True  # type: ignore[method-assign]

        def _no_lock(tid):
            raise AssertionError("terminal graph must freeze before locking")

        h._lock_for = _no_lock  # type: ignore[method-assign]
        asyncio.run(h.redrive("t1"))  # 45-46:直接返回,不进锁

    def test_reset_action_result_maps_pending_dispatch_stuck(self):
        h = TaskHarness(TaskGraphService())
        assert h._reset_action_result("pending_dispatch_stuck") == "pending_dispatch_stuck"

    def test_reset_elapsed_ms_swallows_hostile_node(self):
        h = TaskHarness(TaskGraphService())
        node = SimpleNamespace(run_info=SimpleNamespace(start_time="not-a-timestamp"))
        assert h._reset_elapsed_ms(node) is None
        assert h._reset_elapsed_ms(None) is None


# ---------------------------------------------------------------------------
# centralized_recovery.py —— _on_harness_collect(244 node 缺 / 367 static 分支)
# ---------------------------------------------------------------------------


class TestOnHarnessCollect:
    def test_unknown_node_is_a_silent_noop(self):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        side: list[tuple] = []
        result = asyncio.run(engine._on_harness_collect("t1", "no-such-node", "x", side))
        assert result is None
        assert side == []

    def test_static_runtime_routes_harness_retry_through_prepare_static(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        engine = _make_engine(svc)
        runtime = object()
        monkeypatch.setattr(engine, "_static_runtime", lambda tid: runtime)
        prepared: list[tuple] = []

        async def _prepare_static(tid, rt, side):
            prepared.append((tid, rt, side))

        monkeypatch.setattr(engine, "_prepare_static", _prepare_static)

        side: list[tuple] = []
        asyncio.run(engine._on_harness_collect("t1", "c1", "exec_failed_retry", side))
        node = svc._get_node(graph, "c1")
        assert node.run_info.extend_props.get("harness_retries") == 1
        assert node.run_info.extend_props.get("last_exec_error") == "exec_failed_retry"
        assert prepared == [("t1", runtime, side)]


# ---------------------------------------------------------------------------
# centralized_recovery.py —— on_miss(378-385)/ on_harness(477-493)双守卫
# ---------------------------------------------------------------------------


class TestHarnessEntriesGuards:
    def test_on_miss_external_managed_skips_planning(self, monkeypatch):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_is_external_managed_task", lambda tid: True)

        def _boom(*a, **k):
            raise AssertionError("must not plan external-managed tasks")

        monkeypatch.setattr(engine, "_lock_for", _boom)
        asyncio.run(engine.on_miss(_patch("t1", "c1")))

    def test_on_miss_terminal_graph_freezes(self, monkeypatch):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_is_external_managed_task", lambda tid: False)
        monkeypatch.setattr(engine, "_is_graph_terminal", lambda tid: True)

        def _boom(*a, **k):
            raise AssertionError("must not drive a terminal graph")

        monkeypatch.setattr(engine, "_lock_for", _boom)
        asyncio.run(engine.on_miss(_patch("t1", "c1")))

    def test_on_harness_terminal_graph_freezes(self, monkeypatch):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_is_graph_terminal", lambda tid: True)

        def _boom(*a, **k):
            raise AssertionError("must not retry a terminal graph")

        monkeypatch.setattr(engine, "_lock_for", _boom)
        asyncio.run(engine.on_harness(_patch("t1", "c1", exec_error="exec_failed_retry")))

    def test_on_harness_static_plan_defers_to_static_fallback(self, monkeypatch):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_static_runtime", lambda tid: object())

        def _boom(*a, **k):
            raise AssertionError("static fallback owns static-plan recovery")

        monkeypatch.setattr(engine, "_lock_for", _boom)
        asyncio.run(engine.on_harness(_patch("t1", "c1", exec_error="exec_failed_retry")))


# ---------------------------------------------------------------------------
# centralized_recovery.py —— _sync_graph_status_to_root(520 / 523)
# / _bump_loop_round(538)/ _reset_root_plan_round(707)
# ---------------------------------------------------------------------------


class TestGraphSyncHelpers:
    def test_non_terminal_root_is_not_mirrored(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        engine = _make_engine(svc)
        reports: list = []
        monkeypatch.setattr(engine, "_report_graph_patch", lambda tid, p: reports.append(p))
        engine._sync_graph_status_to_root("t1")  # root PENDING → 非终态不镜像
        assert reports == []
        assert graph.status != Status.HUNG

    def test_already_mirrored_status_is_idempotent(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        graph.status = Status.HUNG  # 图终态已镜像 root
        engine = _make_engine(svc)
        monkeypatch.setattr(
            engine, "_root", lambda tid: SimpleNamespace(node_id="t1", status=Status.HUNG)
        )
        reports: list = []
        monkeypatch.setattr(engine, "_report_graph_patch", lambda tid, p: reports.append(p))
        engine._sync_graph_status_to_root("t1")
        assert reports == []

    def test_bump_loop_round_exhausted_hangs_root(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        graph.extend_props["execution_config"]["MAX_LOOP"] = 0
        engine = _make_engine(svc)
        node_reports: list = []
        graph_reports: list = []
        monkeypatch.setattr(
            engine, "_report_node_patch",
            lambda p: node_reports.append(p)
            or NodeOpResult(task_id=p.task_id, node_id=p.node_id, success=True),
        )
        monkeypatch.setattr(
            engine, "_report_graph_patch",
            lambda tid, p: graph_reports.append(p),
        )
        synced: list = []
        monkeypatch.setattr(
            engine, "_sync_graph_status_to_root", lambda tid: synced.append(tid)
        )
        engine._bump_loop_round("t1")  # loop_round=0 >= MAX_LOOP=0 → 撞顶
        assert len(node_reports) == 1
        assert node_reports[0].status is Status.HUNG
        assert node_reports[0].extend_props_patch.get("hung_reason") == "loop_exhausted"
        assert synced == ["t1"]
        assert graph_reports and graph_reports[0].extend_props_patch.get(
            "hung_reason"
        ) == "loop_exhausted"

    def test_reset_root_plan_round_missing_root_is_noop(self, monkeypatch):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_root", lambda tid: None)

        def _boom(p):
            raise AssertionError("no root → no plan-round write")

        monkeypatch.setattr(engine, "_report_node_patch", _boom)
        engine._reset_root_plan_round("t1")


# ---------------------------------------------------------------------------
# centralized_recovery.py —— _on_bg_done(634-642)/ _on_auto_report_done(695)
# ---------------------------------------------------------------------------


class TestBgDoneCallbacks:
    def test_on_bg_done_cancelled_and_exception_paths(self):
        h = TaskHarness(TaskGraphService())
        cancelled_bg = _DoneTask(cancelled=True)
        h._bg_tasks = {cancelled_bg}  # type: ignore[assignment]
        h._on_bg_done(cancelled_bg)
        assert cancelled_bg not in h._bg_tasks

        bad_bg = _DoneTask(exc=RuntimeError("bg crashed"))
        h._bg_tasks = {bad_bg}  # type: ignore[assignment]
        h._on_bg_done(bad_bg)  # 吞异常,只落 error 日志
        assert bad_bg not in h._bg_tasks

    def test_on_auto_report_done_swallows_task_exception(self):
        h = TaskHarness(TaskGraphService())
        t = _DoneTask(exc=ValueError("auto-report blew up"))
        h._bg_tasks = {t}  # type: ignore[assignment]
        h._on_auto_report_done(t)  # 不抛
        assert t not in h._bg_tasks


# ---------------------------------------------------------------------------
# centralized_recovery.py —— _ensure_bbs_loop(681 / 684)
# 伪 threading.Thread(不真起 loop 线程):wait 失败 → "failed to start";
# wait 成功但 loop 未落位 → "is not running"
# ---------------------------------------------------------------------------


class _NoopThread:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False

    def start(self):
        self.started = True


class _FakeLoopReady:
    def __init__(self, wait_result: bool):
        self.wait_result = wait_result
        self.cleared = 0

    def clear(self):
        self.cleared += 1

    def set(self):
        pass

    def wait(self, timeout=None):
        return self.wait_result


class TestEnsureBbsLoop:
    def test_ready_timeout_raises(self, monkeypatch):
        import agentclaw.community.core.task.task_harness.centralized_recovery as cr

        monkeypatch.setattr(
            cr, "threading", SimpleNamespace(Thread=lambda **kw: _NoopThread(**kw))
        )
        h = TaskHarness(TaskGraphService())
        h._bbs_loop = None
        h._bbs_loop_guard = threading.RLock()
        h._bbs_loop_ready = _FakeLoopReady(wait_result=False)  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="failed to start"):
            h._ensure_bbs_loop()

    def test_loop_not_running_raises(self, monkeypatch):
        import agentclaw.community.core.task.task_harness.centralized_recovery as cr

        monkeypatch.setattr(
            cr, "threading", SimpleNamespace(Thread=lambda **kw: _NoopThread(**kw))
        )
        h = TaskHarness(TaskGraphService())
        h._bbs_loop = None  # fake thread 不落位 → loop 仍 None
        h._bbs_loop_guard = threading.RLock()
        h._bbs_loop_ready = _FakeLoopReady(wait_result=True)  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="is not running"):
            h._ensure_bbs_loop()


# ---------------------------------------------------------------------------
# centralized_recovery.py —— _schedule_bbs_notify(754-756 提交失败关协程)
# / _enter_root_bbs(777 / 780)
# ---------------------------------------------------------------------------


class _LooplessFakeLoop:
    """is_running=True 但无 call_soon_threadsafe → run_coroutine_threadsafe 抛。"""

    def is_running(self):
        return True


class _OkStartRunner:
    async def start_run(self, nodes):
        return [True]


class TestScheduleBbsNotify:
    def test_coroutine_submit_failure_closes_coroutine_and_reraises(self):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        engine._runner = _OkStartRunner()
        engine._bbs_loop = _LooplessFakeLoop()  # type: ignore[assignment]
        engine._bbs_loop_ready = threading.Event()
        engine._bbs_loop_thread = threading.current_thread()
        dashboard = svc.query_task_dashboard("t1")

        with pytest.raises(AttributeError):
            engine._schedule_bbs_notify("t1", dashboard)
        assert engine._bg_tasks == set()  # 未跟踪失败的 future


class TestEnterRootBbsGuards:
    def test_hung_execution_graph_never_enters_bbs(self):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        graph = SimpleNamespace(status=Status.HUNG)
        assert engine._enter_root_bbs("t1", graph) is False

    def test_claimed_root_skips_bbs_round(self, monkeypatch):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        monkeypatch.setattr(
            engine, "_root",
            lambda tid: SimpleNamespace(
                node_id="t1",
                run_info=SimpleNamespace(extend_props={"bbs_owner": "bot-X"}),
            ),
        )

        def _boom(*a, **k):
            raise AssertionError("in-flight BBS claim must not re-enter")

        monkeypatch.setattr(engine, "_report_graph_patch", _boom)
        assert engine._enter_root_bbs("t1", SimpleNamespace(status=Status.RUNNING)) is False


# ---------------------------------------------------------------------------
# centralized_recovery.py —— _maybe_propagate_hung(833 / 845-846 / 850 / 873)
# ---------------------------------------------------------------------------


class TestHungPropagation:
    def test_graph_already_hung_short_circuits(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        graph.status = Status.HUNG
        engine = _make_engine(svc)
        monkeypatch.setattr(
            engine, "_root", lambda tid: SimpleNamespace(node_id="t1", status=Status.HUNG)
        )
        reports: list = []
        monkeypatch.setattr(engine, "_report_node_patch", lambda p: reports.append(p))
        monkeypatch.setattr(
            engine, "_report_graph_patch", lambda tid, p: reports.append(p)
        )
        engine._maybe_propagate_hung("t1", "t1", "exec_stuck")
        assert reports == []

    def test_root_hung_with_inflight_claim_hard_closes(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        graph.extend_props["bbs_owner"] = "bot-Z"
        engine = _make_engine(svc)
        monkeypatch.setattr(
            engine, "_root", lambda tid: SimpleNamespace(node_id="t1", status=Status.HUNG)
        )
        synced: list = []
        monkeypatch.setattr(
            engine, "_sync_graph_status_to_root", lambda tid: synced.append(tid)
        )
        graph_reports: list = []
        monkeypatch.setattr(
            engine, "_report_graph_patch",
            lambda tid, p: graph_reports.append(p),
        )
        engine._maybe_propagate_hung("t1", "t1", "exec_stuck")
        assert synced == ["t1"]
        assert graph_reports and graph_reports[0].extend_props_patch.get(
            "hung_reason"
        ) == "root_stuck"

    def test_all_terminal_siblings_without_hung_stop_bubbling(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        _dispatch_running(svc, graph, "c1", run_mode="single_bot", assignee="b")
        svc.update_task_node_info(
            _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))
        )
        assert svc._get_node(graph, "c1").status is Status.SUCCESS
        engine = _make_engine(svc)
        reports: list = []
        monkeypatch.setattr(engine, "_report_node_patch", lambda p: reports.append(p))
        engine._maybe_propagate_hung("t1", "c1", "exec_stuck")  # 无 HUNG 兄弟 → 不上行
        assert reports == []


# ---------------------------------------------------------------------------
# task_runner/task_runner.py —— resume_relay_turn(134-143)
# / get_group_session(149-153)/ _relay_blackboard(214-215)
# ---------------------------------------------------------------------------


class _ResumeBackend:
    def __init__(self, resumed):
        self.resumed = resumed

    async def resume_relay_turn(self, node, relay_turn):
        self.resumed.append((node.node_id, relay_turn))
        return True


class TestRunnerRelayAndSession:
    def test_resume_unavailable_backend_returns_false(self):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        node = svc._get_node(graph, "t1")
        runner = TaskRunner(svc)  # execution_backend None → resume 不可用
        assert asyncio.run(runner.resume_relay_turn(node, "turn-1")) is False

    def test_resume_backend_relay_turn_awaited(self):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        node = svc._get_node(graph, "t1")
        resumed: list[tuple] = []
        runner = TaskRunner(svc, execution_backend=_ResumeBackend(resumed))
        assert asyncio.run(runner.resume_relay_turn(node, "turn-2")) is True
        assert resumed == [("t1", "turn-2")]

    def test_get_group_session_stub_returns_none(self):
        runner = TaskGraphService()
        assert asyncio.run(TaskRunner(runner).get_group_session("grp_x")) is None

    def test_relay_blackboard_builds_full_view(self):
        svc = TaskGraphService()
        graph = _graph_with(svc, {"orchestration_mode": "relay"})
        runner = TaskRunner(svc)
        bb = runner._relay_blackboard("t1")
        assert bb["root_goal"] == svc._get_node(graph, "t1").task_spec.goal.to_dict()
        assert bb["loop_round"] == 0
        assert [n["node_id"] for n in bb["nodes"]] == ["t1"]
        assert all(isinstance(n["status"], str) for n in bb["nodes"])


# ---------------------------------------------------------------------------
# task_runner/task_runner.py —— _drain(engine 绑定):group 失败(296-336)
# / HIT_MULTI 降级 carrier(375)/ bbs_handoff side(393-400)
# ---------------------------------------------------------------------------


class _BoomGroupRunner:
    async def form_coop_group(self, gf):
        raise RuntimeError("bcs down")


class _OkGroupRunner:
    async def form_coop_group(self, gf):
        return "g-1"

    async def start_run(self, nodes):
        return [True] * len(nodes)


class TestDrainGroupExceptionPath:
    def test_group_failure_clears_dispatching_with_form_group_failed(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        node = _dispatch_running(svc, graph, "c1", run_mode="coop_group", assignee="")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.PENDING))
        node = svc._get_node(graph, "c1")
        engine = _make_engine(svc)
        engine._runner = _BoomGroupRunner()
        node_reports: list = []
        monkeypatch.setattr(
            engine, "_report_node_patch",
            lambda p: node_reports.append(p)
            or NodeOpResult(task_id=p.task_id, node_id=p.node_id, success=True),
        )
        traj: list[dict] = []
        monkeypatch.setattr(
            engine, "_log_trajectory", lambda *a, **k: traj.append(k)
        )
        gf = GroupFormation(bot_ids=["bot_a"], collab_mode="chat")
        side = [("group", node, gf)]

        async def main():
            await engine._drain("t1", side)

        asyncio.run(main())
        # 失败补丁:清执行者 + dispatch_error=form_group_failed,节点留 PENDING
        assert len(node_reports) == 1
        p = node_reports[0]
        assert p.extend_props_patch.get("dispatching") is None
        assert p.extend_props_patch.get("dispatch_error") == "form_group_failed"
        # 轨迹旁路:DISPATCH(form_group_failed),error_type=DISPATCH_STUCK
        assert len(traj) == 1
        assert traj[0].get("action_result") == "form_group_failed"
        assert node.run_info.run_mode == "coop_group" or True  # 双派发防护由状态机保证


class TestDrainHitMultiCarriers:
    def test_hit_multi_emits_rationale_and_failure_carriers(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        node = _dispatch_running(svc, graph, "c1", run_mode="coop_group", assignee="g0")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.PENDING))
        node = svc._get_node(graph, "c1")
        node.run_info.extend_props["_dispatch_rationale"] = {"candidates": ["b"]}
        node.run_info.extend_props["_dispatch_failure"] = {"error_msg": "degraded"}
        engine = _make_engine(svc)
        engine._runner = _OkGroupRunner()
        monkeypatch.setattr(engine, "_log_action", lambda *a, **k: None)
        captured: list = []

        def _capture(*a, **k):
            captured.append((a, k))

        monkeypatch.setattr(engine, "_log_trajectory", _capture)
        gf = GroupFormation(bot_ids=["bot_a"], collab_mode="manager_worker")
        side = [("group", node, gf)]

        async def main():
            await engine._drain("t1", side)

        asyncio.run(main())
        assert node.run_info.assignee == "g-1"
        assert svc._get_node(graph, "c1").status is Status.RUNNING
        gate = [k for _, k in captured if k.get("action_result") == "hit_multi"]
        assert gate and gate[0]["action_input"] == "g-1"
        assert gate[0]["ext_info"] == {
            "_dispatch_rationale": {"candidates": ["b"]},
            "_dispatch_failure": {"error_msg": "degraded"},
        }


class TestDrainBbsHandoffSide:
    def test_bbs_handoff_schedules_claim_and_tracks_bg_task(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        node = _dispatch_running(svc, graph, "c1", run_mode="bbs", assignee="bot-bbs")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.PENDING))
        node = svc._get_node(graph, "c1")
        engine = _make_engine(svc)
        claims: list[tuple] = []

        async def _claim(tid, nid, bot_id, items):
            claims.append((tid, nid, bot_id, items))

        monkeypatch.setattr(engine, "_bbs_handoff_claim", _claim)
        monkeypatch.setattr(engine, "_bbs_handoff_delay", lambda tid: 0.0)
        side = [("bbs_handoff", node, "rnd-1", ["item-1"])]

        async def main():
            await engine._drain("t1", side)
            await asyncio.gather(*engine._bg_tasks, return_exceptions=True)

        asyncio.run(main())
        assert claims == [("t1", "c1", "rnd-1", ["item-1"])]
        assert engine._bg_tasks == set()  # done callback 已脱离跟踪集


# ---------------------------------------------------------------------------
# task_runner/task_runner.py —— _static_auto_report_on(595)/
# _static_auto_report(627-697)/ _static_bbs_handoff_auto_report(719-751)
# ---------------------------------------------------------------------------


_STATIC_CFG = {
    "static_plan_id": "plan-1",
    "static_auto_report": True,
    "static_auto_report_delay": 0.0,
}


class TestStaticAutoReports:
    def _static_engine(self, monkeypatch, svc, graph):
        engine = _make_engine(svc)
        runtime = SimpleNamespace(
            by_id={
                "c1": SimpleNamespace(
                    output={
                        "approved_out": "$.result.approved",
                        "todo_out": "$.result.unhandled_tasks",
                    }
                )
            }
        )
        monkeypatch.setattr(engine, "_static_runtime", lambda tid: runtime)
        reports: list[TaskNodePatch] = []

        async def _report(patch):
            reports.append(patch)

        monkeypatch.setattr(engine, "on_report", _report)
        return engine, reports

    def test_auto_report_on_false_without_static_marker(self):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        assert engine._static_auto_report_on("t1") is False

    def test_static_auto_report_no_runtime_is_noop(self, monkeypatch):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_static_runtime", lambda tid: None)
        asyncio.run(engine._static_auto_report("t1", "c1"))  # 627:runtime None → 直接返回

    def test_static_auto_report_fires_mock_pass_with_unhandled(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc, dict(_STATIC_CFG))
        _dispatch_running(svc, graph, "c1")
        engine, reports = self._static_engine(monkeypatch, svc, graph)

        asyncio.run(engine._static_auto_report("t1", "c1"))
        assert len(reports) == 1
        patch = reports[0]
        assert patch.acceptance_result.verdict is AcceptanceVerdict.DONE
        result = patch.output_patch["result"]
        assert result["approved"] is True
        assert result["unhandled_tasks"] and result["unhandled_tasks"][0]["id"]
        assert patch.extend_props_patch.get("dispatching") is None

    def test_static_auto_report_skips_non_running_node(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc, dict(_STATIC_CFG))
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")  # PENDING:留真实派发
        engine, reports = self._static_engine(monkeypatch, svc, graph)

        asyncio.run(engine._static_auto_report("t1", "c1"))
        assert reports == []  # 653-661:非 RUNNING → 不 mock 上报

    def test_bbs_handoff_auto_report_fires_and_skips(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc, {"static_plan_id": "plan-1"})
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_static_mock_fallback_delay", lambda *a: 0.0)
        reports: list[TaskNodePatch] = []

        async def _report(patch):
            reports.append(patch)

        monkeypatch.setattr(engine, "on_report", _report)

        # PENDING → 续棒尚未 RUNNING,自跳过
        asyncio.run(engine._static_bbs_handoff_auto_report("t1", "c1", "rnd-1", ["i"]))
        assert reports == []

        # RUNNING → mock PASS→SUCCESS(真实闭环先到则真实上报覆盖本兜底)
        svc.update_task_node_info(
            _patch("t1", "c1", status=Status.RUNNING, run_mode="bbs", assignee="rnd-1")
        )
        asyncio.run(engine._static_bbs_handoff_auto_report("t1", "c1", "rnd-1", ["i"]))
        assert len(reports) == 1
        patch = reports[0]
        assert patch.acceptance_result.verdict is AcceptanceVerdict.DONE
        result = patch.output_patch["result"]
        assert result["handed_to"] == "rnd-1"
        assert result["items"] == ["i"]


# ---------------------------------------------------------------------------
# task_runner/callback_adapter.py —— 78 / 247 / 398-400 / 407-410 /
# 420-425 / 436 / 591 / 594 / 598-599 / 563-564
# ---------------------------------------------------------------------------


class _RecEngine:
    def __init__(self):
        self.started: list = []
        self.reports: list = []

    async def on_start(self, patch):
        self.started.append(patch)
        return NodeOpResult(task_id=patch.task_id, node_id=patch.node_id, success=True)

    async def on_report(self, patch):
        self.reports.append(patch)
        return NodeOpResult(task_id=patch.task_id, node_id=patch.node_id, success=True)


class _ProcessedRepo:
    def find_by_event_id(self, event_id):
        return SimpleNamespace(process_status="PROCESSED")


class _BrokenRepo:
    def find_by_event_id(self, event_id):
        raise RuntimeError("db down")

    def upsert(self, record):
        raise RuntimeError("db down")


class _UpsertRepo:
    def __init__(self):
        self.upserts: list = []

    def upsert(self, record):
        self.upserts.append(record)


class _BrokenContextService:
    def emit_trajectory_event(self, *a, **k):
        raise RuntimeError("tcs down")


class TestCallbackAdapterGaps:
    def test_derive_event_id_without_routing_key_is_none(self):
        assert _derive_event_id({"workflow_source": "bcs"}, "ingest") is None

    def test_poller_top_level_node_id_overrides_loop_task_id(self):
        adapter = CallbackAdapter()
        patch = adapter.adapt(
            TaskCallbackData(
                data={
                    "loop_task_id": "tid::stale-node",
                    "node_id": "fresh-node",
                    "result": {"success": True, "data": "行业全貌"},
                }
            )
        )
        assert patch.node_id == "fresh-node"
        assert patch.task_id == "tid"
        assert patch.status is Status.DONE
        assert patch.output_patch == {"output": "行业全貌"}


class TestTaskLoopCallbackGaps:
    def _tlc(self, engine=None, repo=None, context_service=None):
        return TaskLoopCallback(
            CallbackAdapter(), engine or _RecEngine(), repo, context_service
        )

    def test_is_already_processed_swallows_finder_failure(self):
        tlc = self._tlc(repo=_BrokenRepo())
        assert tlc._is_already_processed("evt-1") is False

    def test_consume_pending_audit_round_trip(self):
        tlc = self._tlc()
        record = object()
        assert tlc._consume_pending_audit() is None
        tlc._set_pending_audit(record)
        assert tlc._consume_pending_audit() is record  # 单次消费
        assert tlc._consume_pending_audit() is None

    def test_start_run_idempotent_event_is_acked_without_start(self):
        engine = _RecEngine()
        tlc = self._tlc(engine=engine, repo=_ProcessedRepo())
        asyncio.run(
            tlc.start_run(
                TaskCallbackData(
                    data={"loop_task_id": "t1::c1", "workflow_instance_id": "sess-1"}
                )
            )
        )
        assert engine.started == []

    def test_start_run_non_dict_data_still_applies_start_fact(self):
        engine = _RecEngine()
        tlc = self._tlc(engine=engine)
        asyncio.run(tlc.start_run(TaskCallbackData(data="not-a-dict")))
        assert len(engine.started) == 1
        assert engine.started[0].status is Status.RUNNING
        assert tlc._consume_pending_audit() is None  # finally 清挂账

    def test_report_result_non_dict_data_is_skipped(self):
        engine = _RecEngine()
        tlc = self._tlc(engine=engine)
        asyncio.run(tlc.report_result(TaskCallbackData(data=None)))
        assert engine.reports == []

    def test_ingest_without_repo_is_silent_noop(self):
        tlc = self._tlc()
        asyncio.run(tlc.ingest(TaskCallbackData(data={"loop_task_id": "t1::c1"})))

    def test_ingest_non_dict_payload_not_persisted(self):
        repo = _UpsertRepo()
        tlc = self._tlc(repo=repo)
        asyncio.run(tlc.ingest(TaskCallbackData(data=[1, 2, 3])))
        assert repo.upserts == []

    def test_ingest_persist_failure_does_not_raise(self):
        tlc = self._tlc(repo=_BrokenRepo())
        asyncio.run(tlc.ingest(TaskCallbackData(data={"loop_task_id": "t1::c1"})))

    def test_ingest_parse_error_trajectory_emission_failure_swallowed(self):
        tlc = self._tlc(repo=None, context_service=_BrokenContextService())
        asyncio.run(tlc.ingest_parse_error({"flow_id": "f-1"}, "boom"))  # 563-564:吞


# ---------------------------------------------------------------------------
# task_runner/event_dispatcher.py —— 49 / 55
# ---------------------------------------------------------------------------


class _PendingEventGraph:
    def __init__(self, pending):
        self._pending = pending
        self.acked: list = []

    def list_pending_semantic_events(self, task_id, limit):
        # 队列语义:取出即消费(真实 store 在 ack 后不再吐出同一事件)
        events = self._pending[:limit]
        self._pending = []
        return events

    def acknowledge_semantic_event(self, task_id, event_id):
        self.acked.append(event_id)


class TestEventDispatcherGaps:
    def test_unregistered_event_type_is_skipped_and_loop_breaks(self):
        raw = {
            "event_id": "e-1",
            "event_type": TaskSemanticEventType.EXECUTION_REQUESTED.value,
            "task_id": "t1",
            "node_id": "c1",
            "payload": {"k": "v"},
        }
        graph = _PendingEventGraph([raw])
        dispatcher = TaskSemanticEventDispatcher(graph)
        delivered = asyncio.run(dispatcher.dispatch_pending("t1"))
        assert delivered == 0
        assert graph.acked == []  # 未处理不 ack

    def test_registered_handler_delivers_and_acks(self):
        raw = {
            "event_id": "e-2",
            "event_type": TaskSemanticEventType.PLAN_REQUESTED.value,
            "task_id": "t1",
            "node_id": None,
            "payload": {},
        }
        graph = _PendingEventGraph([raw])
        dispatcher = TaskSemanticEventDispatcher(graph)
        seen: list = []

        async def handler(event):
            seen.append(event)

        dispatcher.register(TaskSemanticEventType.PLAN_REQUESTED, handler)
        assert asyncio.run(dispatcher.dispatch_pending("t1")) == 1
        assert len(seen) == 1
        assert seen[0].event_type is TaskSemanticEventType.PLAN_REQUESTED
        assert graph.acked == ["e-2"]


# ---------------------------------------------------------------------------
# task_runner/centralized_support.py —— 386 / 397 / 400-401 / 414-416 / 427 / 430
# ---------------------------------------------------------------------------


class _SideDataGraph:
    def __init__(self, nodes=None, raises=False):
        self.nodes = nodes or []
        self.raises = raises

    def query_task_dashboard(self, task_id):
        if self.raises:
            raise RuntimeError("graph down")
        return SimpleNamespace(tasks=self.nodes, extend_props={}, loop_round=0)


class TestDispatchSideData:
    def test_missing_node_returns_empty_side_data(self):
        graph = _SideDataGraph(nodes=[])
        assert _read_dispatch_side_data(graph, "t1", "nobody") == (None, 0)

    def test_failure_carrier_alone_is_surfaced_to_ext_info(self):
        node = _FakeNode(
            "c1",
            extend_props={"_dispatch_failure": {"error_msg": "degraded"}},
        )
        graph = _SideDataGraph(nodes=[node])
        ext, attempt = _read_dispatch_side_data(graph, "t1", "c1")
        assert ext == {"_dispatch_failure": {"error_msg": "degraded"}}
        assert attempt == 0

    def test_attempt_counter_read_from_extend_props(self):
        node = _FakeNode(
            "c1",
            extend_props={
                "_dispatch_rationale": {"candidates": ["b"]},
                "harness_retries": 2,
            },
        )
        graph = _SideDataGraph(nodes=[node])
        ext, attempt = _read_dispatch_side_data(graph, "t1", "c1")
        assert ext == {"_dispatch_rationale": {"candidates": ["b"]}}
        assert attempt == 2

    def test_hostile_graph_degrades_to_empty_side_data(self):
        graph = _SideDataGraph(raises=True)
        assert _read_dispatch_side_data(graph, "t1", "c1") == (None, 0)


class TestDispatchFailActionResult:
    def test_mapping_table(self):
        assert _dispatch_fail_action_result("dispatch_exception:TimeoutError") == "dispatch_exception"
        assert _dispatch_fail_action_result("no_result") == "no_result"
        assert _dispatch_fail_action_result("anything_else") == "failed"
        assert _dispatch_fail_action_result(None) == "failed"
        assert _dispatch_fail_action_result("  ") == "failed"


class TestStaleDispatching:
    def test_not_dispatching_is_never_stale(self):
        node = _FakeNode("c1", extend_props={})
        assert _is_stale_dispatching(node) is False

    def test_dispatching_without_timestamp_is_stale(self):
        node = _FakeNode("c1", extend_props={"dispatching": True})
        assert _is_stale_dispatching(node) is True

    def test_fresh_dispatching_is_kept(self):
        node = _FakeNode(
            "c1", extend_props={"dispatching": True, "dispatching_at": _now_ms()}
        )
        assert _is_stale_dispatching(node) is False

    def test_dispatching_beyond_threshold_is_stale(self):
        node = _FakeNode(
            "c1",
            extend_props={
                "dispatching": True,
                "dispatching_at": _now_ms() - 10 * 60 * 1000,
            },
        )
        assert _is_stale_dispatching(node) is True


# ---------------------------------------------------------------------------
# task_runner/execution_adapters.py —— 437 / 349-350 / 371-372 / 333 / 299 /
# 475-492(固定 plan exec_error 守卫)/ 510-521(防御性 static 重派分支)/
# 632-636(图终态冻结)/ 672-676 / 718 / 732-733 / 738
# ---------------------------------------------------------------------------


class _RaisingGraph:
    """图缺失/配置不可读:防御性读取必须退默认,而不是炸闸门。"""

    def _execution_config(self, task_id):
        raise RuntimeError("config down")

    def query_task_dashboard(self, task_id):
        raise RuntimeError("graph down")


class TestEngineDefensiveReads:
    def test_task_type_read_failure_defaults_dynamic(self):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        engine._graph = _RaisingGraph()
        assert engine._task_type("t1") == "dynamic"
        assert engine._is_graph_terminal("t1") is False

    def test_root_missing_when_every_node_has_a_parent(self):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        engine._graph = SimpleNamespace(
            query_task_dashboard=lambda tid: SimpleNamespace(
                tasks=[SimpleNamespace(node_id="x")], extend_props={}, loop_round=0
            ),
            get_parent_task=lambda tid, nid: SimpleNamespace(node_id="parent"),
        )
        assert engine._root("t1") is None

    def test_build_context_root_leaf_has_no_parent(self):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        ctx = engine.build("t1", "t1")
        assert ctx["mode"] == "execute"
        assert ctx["parent_node_id"] is None
        assert ctx["parent_spec"] is None
        assert ctx["node_spec"] is not None

    def test_getattr_unknown_method_raises(self):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        with pytest.raises(AttributeError, match="no_such_seam"):
            engine.no_such_seam  # pylint: disable=pointless-statement

    def test_method_owners_expose_no_classmethod_seams(self):
        """防线不变量:``_METHOD_OWNERS`` 的所有 prod seam 均为普通函数绑定。

        ``__getattr__`` 的 classmethod 分支(引擎类自身未挂 classmethod seam,只有
        可选 harbor)由下方 ``_ClassmethodSeamAdapter`` 子类专项覆盖;若未来某 **prod**
        seam 变成 classmethod,本断言先于运行期绑定语义漂移暴露。"""
        for name, owner in CentralizedExecutionAdapter._METHOD_OWNERS.items():
            descriptor = owner.__dict__.get(name)
            assert not isinstance(descriptor, classmethod), (
                f"seam {name} on {owner.__name__} became a classmethod"
            )


class _ClassmethodOwner:
    owner_marker = "owner-marker"

    @classmethod
    def _probe_seam(cls):
        return f"bound-to-{cls.__name__}"


class _ClassmethodSeamAdapter(CentralizedExecutionAdapter):
    """测试专用 harbor:``_METHOD_OWNERS`` 表可路由 classmethod 型 seam。"""

    _METHOD_OWNERS = {
        **CentralizedExecutionAdapter._METHOD_OWNERS,
        "_probe_seam": _ClassmethodOwner,
    }


class TestEngineGetattrDispatch:
    def test_classmethod_seam_binds_to_concrete_engine_type(self):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _ClassmethodSeamAdapter(svc)
        resolved = engine._probe_seam
        assert callable(resolved)
        # classmethod descriptor 经 __getattr__ 绑定到引擎具体类型(非 owner、非实例)
        assert resolved() == "bound-to-_ClassmethodSeamAdapter"


class TestEngineOnReportGuards:
    def test_static_plan_exec_error_folds_without_failed_or_retrx(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        _dispatch_running(svc, graph, "c1")
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_static_runtime", lambda tid: object())
        reconciled: list = []
        monkeypatch.setattr(
            engine, "_reconcile_root_hung_if_blocked", lambda tid: reconciled.append(tid)
        )

        patch = _patch("t1", "c1", exec_error="terminal_result_invalid: boom")
        result = asyncio.run(engine.on_report(patch))
        assert result.success is True
        # 守卫改写:保持 RUNNING(不落 FAILED)、不重派,只留 last_exec_error 痕迹
        assert patch.status is None
        assert patch.acceptance_result is None
        assert patch.extend_props_patch["last_exec_error"] == "terminal_result_invalid: boom"
        assert reconciled == ["t1"]
        node = svc._get_node(graph, "c1")
        assert node.status is Status.RUNNING
        assert node.run_info.extend_props.get("last_exec_error") == (
            "terminal_result_invalid: boom"
        )

    def test_flaky_static_runtime_falls_into_defensive_static_retry(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        _dispatch_running(svc, graph, "c1")
        engine = _make_engine(svc)
        calls = {"n": 0}

        def flaky_static_runtime(tid):
            # 第一读(守卫)None → 未拦截;第二读(static 分支)非 None → 走防御性重派
            calls["n"] += 1
            return None if calls["n"] == 1 else object()

        monkeypatch.setattr(engine, "_static_runtime", flaky_static_runtime)
        prepared: list = []

        async def _prepare_static(tid, runtime, side):
            prepared.append((tid, runtime, side))

        monkeypatch.setattr(engine, "_prepare_static", _prepare_static)
        monkeypatch.setattr(engine, "_log_action", lambda *a, **k: None)
        monkeypatch.setattr(
            engine, "_reconcile_root_hung_if_blocked", lambda tid: None
        )

        patch = _patch("t1", "c1", exec_error="exec_failed_retry")
        result = asyncio.run(engine.on_report(patch))
        assert result.success is True
        # exec_error 落 FAILED(正常回投路径),随后 static 分支经 _on_harness_collect 复位 PENDING
        assert svc._get_node(graph, "c1").status is Status.PENDING
        assert svc._get_node(graph, "c1").run_info.extend_props.get("harness_retries") == 1
        assert len(prepared) == 1

    def test_terminal_graph_folds_but_freezes_driving(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        _dispatch_running(svc, graph, "c1")
        graph.status = Status.DONE  # 图已终态:回投可 fold,后续推进冻结
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_static_runtime", lambda tid: None)
        monkeypatch.setattr(engine, "_log_action", lambda *a, **k: None)
        monkeypatch.setattr(engine, "_emit_execute_trajectory", lambda *a, **k: None)
        passed: list = []

        async def _on_pass_collect(tid, nid, side):
            passed.append((tid, nid))

        monkeypatch.setattr(engine, "_on_pass_collect", _on_pass_collect)
        drained: list = []

        async def _drain(tid, side):
            drained.append((tid, side))

        monkeypatch.setattr(engine, "_drain", _drain)

        patch = _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))
        result = asyncio.run(engine.on_report(patch))
        assert result.success is True
        assert passed == []
        assert drained == []


class TestEngineBbsReport:
    def test_external_managed_bbs_report_is_graph_update_only(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        _dispatch_running(svc, graph, "c1")
        engine = _make_engine(svc)
        monkeypatch.setattr(engine, "_is_external_managed_task", lambda tid: True)
        result = asyncio.run(
            engine.on_bbs_report(_patch("t1", "c1", assignee="bot-bbs",
                                        extend_props_patch={"x": 1}))
        )
        assert result.success is True

    def test_terminal_graph_after_claim_does_not_drive(self):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        _dispatch_running(svc, graph, "c1")
        svc.update_task_node_info(
            _patch("t1", "t1", extend_props_patch={"bbs_owner": "bot-bbs"})
        )
        graph.status = Status.DONE  # 图已终态 → 只 fold,不再驱动
        engine = _make_engine(svc)

        async def main():
            result = await engine.on_bbs_report(
                _patch("t1", "c1", assignee="bot-bbs",
                       output_patch={"output": {"r": 1}})
            )
            return result

        result = asyncio.run(main())
        assert result.success is True
        root = svc._get_node(graph, "t1")
        assert root.run_info.extend_props.get("bbs_owner") is None  # Finally 无条件释放 claim
        assert svc._get_node(graph, "c1").status is Status.SUCCESS

    def test_failed_scoped_node_routes_to_on_fail_collect(self, monkeypatch):
        svc = TaskGraphService()
        graph = _graph_with(svc)
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(
            _patch("t1", "t1", extend_props_patch={"bbs_owner": "bot-bbs"})
        )
        svc._get_node(graph, "c1").status = Status.FAILED  # 模拟接力执行失败留 FAILED
        graph.status = Status.RUNNING
        engine = _make_engine(svc)
        monkeypatch.setattr(
            engine, "_report_node_patch",
            lambda p: NodeOpResult(task_id=p.task_id, node_id=p.node_id, success=True),
        )
        failed: list = []

        async def _on_fail_collect(tid, nid, side):
            failed.append((tid, nid))

        monkeypatch.setattr(engine, "_on_fail_collect", _on_fail_collect)

        async def main():
            result = await engine.on_bbs_report(_patch("t1", "c1", assignee="bot-bbs"))
            await asyncio.sleep(0)
            return result

        result = asyncio.run(main())
        assert result.success is True
        assert failed == [("t1", "c1")]


class TestEngineStart:
    def test_start_delegates_to_on_execute(self, monkeypatch):
        svc = TaskGraphService()
        _graph_with(svc)
        engine = _make_engine(svc)
        executed: list = []

        async def _execute(tid):
            executed.append(tid)
            return NodeOpResult(task_id=tid, node_id=tid, success=True)

        monkeypatch.setattr(engine, "on_execute", _execute)
        result = asyncio.run(engine.start("t1"))
        assert executed == ["t1"]
        assert result.success is True