"""M2 ExecutionEngine on_* 单测(对齐 tasks.md T2.x)。

in-test CaseEngine(ExecutionEngine)子类覆写 _build_* 注入 stub planner/dispatcher/runner(T1=A corp 最简形态);
真实 TaskGraphService。验收 100% 回投(无 verify port);BBS 投递归 runner(无 bbs market port)。
覆盖:on_execute 首帧、on_report PASS 传播/根等回投、on_report FAIL 补救/升 BBS、on_miss 拆细/升 BBS、
on_harness 复位重投、loop_round 仅升 BBS++、零 case grep。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import time
from typing import Callable

import pytest

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
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService, TaskGraphPatch


# ===== domain helpers =====
def _task_info(
    task_id: str = "t1", max_depth: int = 3, task_type: str = "dynamic"
) -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
        execution_config={
            "MAX_DEPTH": max_depth,
            "BBS_MAX_DEPTH": 3,
            "task_type": task_type,
        },
    )


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec, run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


def _run(coro):
    """同步驱动 async 编排方法(on_* 协程化后,单测经此 helper 跑)。"""
    return asyncio.new_event_loop().run_until_complete(coro)


async def _wait_bg_tasks(engine):
    """Wait for engine tasks regardless of the task's owning event loop."""
    for task in list(engine._bg_tasks):
        if isinstance(task, concurrent.futures.Future):
            await asyncio.to_thread(task.result, 5)
        else:
            await task


# ===== stubs =====
class StubPlanner:
    def __init__(self, factory: Callable[[object], list[TaskNode]] | None = None,
                 has_gap_when_empty: bool = True):
        self._factory = factory or (lambda g: [])
        self._has_gap_when_empty = has_gap_when_empty
        self.plan_calls = 0

    async def plan(self, graph, target_node_id: str | None = None) -> PlanResult:
        self.plan_calls += 1
        kids = self._factory(graph)
        return PlanResult(children=kids, has_gap=bool(kids) or self._has_gap_when_empty)


class StubDispatcher:
    def __init__(self, run_mode="single_bot", assignee="bot1", miss=False):
        self.run_mode = run_mode
        self.assignee = assignee
        self.miss = miss

    async def dispatch(self, toDoTaskList: list[TaskNode]) -> list[TaskNode]:
        out = []
        for n in toDoTaskList:
            if self.miss:
                n.run_info.extend_props["miss_events"] = ["no_bot"]
            else:
                n.run_info.run_mode = self.run_mode
                n.run_info.assignee = self.assignee
            out.append(n)
        return out


class StubRunner:
    def __init__(self):
        self.run_calls: list[list[TaskNode]] = []
        self.bbs_calls: list = []   # engine 升 BBS 可恢复态时经 start_run 派发的 task_id
        self._groups = []

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

    def query_status(self, task_id): return Status.PENDING
    def query_detail(self, node): return node
    def query_result(self, node): return node
    def query_bot_tasks(self, bot_id): return []


class _CaseEngine(ExecutionEngine):
    """测试子类覆写 _build_* 注入 stub(T1=A:corp 最简形态)。"""

    def __init__(self, graph, planner=None, dispatcher=None, runner=None):
        self._case_planner = planner
        self._case_dispatcher = dispatcher
        self._case_runner = runner
        super().__init__(graph)

    def _build_planner(self):
        return self._case_planner if self._case_planner is not None else super()._build_planner()

    def _build_dispatcher(self):
        return self._case_dispatcher if self._case_dispatcher is not None else super()._build_dispatcher()

    def _build_runner(self):
        return self._case_runner if self._case_runner is not None else super()._build_runner()


def _engine(svc, planner=None, dispatcher=None, runner=None):
    return _CaseEngine(
        svc,
        planner=planner or StubPlanner(),
        dispatcher=dispatcher or StubDispatcher(),
        runner=runner or StubRunner(),
    )


@pytest.fixture
def svc() -> TaskGraphService:
    return TaskGraphService()


@pytest.fixture
def graph(svc):
    return svc.initialize_graph(_task_info())


# ===== task_dispatch lifecycle timestamps =====
class TestPrepareDispatchStartTime:
    def test_miss_node_records_start_time_before_dispatch_result(self, svc, graph):
        dispatcher = StubDispatcher(miss=True)
        eng = _engine(svc, dispatcher=dispatcher)
        side: list[tuple] = []

        _run(eng._prepare_into("t1", side))

        root = svc._get_node(graph, "t1")
        assert root.run_info.start_time is not None
        assert side and side[0][0] == "miss"


# ===== on_execute =====
class TestDispatchStartTimeSemantics:
    def test_retry_does_not_overwrite_first_dispatch_start_time(self, svc, graph):
        planner = StubPlanner(lambda g: [_child("c1")])
        eng = _engine(svc, planner=planner)
        _run(eng.on_execute("t1"))
        node = svc._get_node(graph, "c1")
        first_start = node.run_info.start_time
        assert first_start is not None

        _run(eng.on_harness(_patch("t1", "c1", exec_error="external_harness")))
        node = svc._get_node(graph, "c1")
        assert node.run_info.start_time == first_start


class TestOnExecute:
    def test_first_frame(self, svc, graph):
        planner = StubPlanner(lambda g: [_child("c1"), _child("c2")])
        runner = StubRunner()
        eng = _engine(svc, planner=planner, runner=runner)
        _run(eng.on_execute("t1"))
        assert svc._get_node(graph, "t1").status == Status.PLANNING  # v4:父委托态
        assert svc._get_node(graph, "c1").status == Status.RUNNING
        assert svc._get_node(graph, "c2").status == Status.RUNNING
        assert len(runner.run_calls) == 1
        assert {n.node_id for n in runner.run_calls[0]} == {"c1", "c2"}

    def test_mark_planning_no_run_mode_only_status(self, svc, graph):
        # v5:planning 是 Status.PLANNING,不是 run_mode;run_mode 仅供 single_bot/coop_group/bbs
        planner = StubPlanner(lambda g: [_child("c1")])
        eng = _engine(svc, planner=planner)
        _run(eng.on_execute("t1"))
        root = svc._get_node(graph, "t1")
        assert root.status == Status.PLANNING
        assert root.run_info.run_mode is None  # 不再写 "planning"
        assert root.run_info.assignee is None
        # 叶子派发执行:run_mode=执行态 + start_time 写入
        leaf = svc._get_node(graph, "c1")
        assert leaf.run_info.run_mode == "single_bot"
        assert leaf.run_info.start_time is not None

    def test_action_log_records_plan_dispatch(self, svc, graph):
        # 动作历史:根 PLAN + 叶 DISPATCH(execute 后 EXECUTE/VERIFY 在 test_task_service 回投流覆盖)
        planner = StubPlanner(lambda g: [_child("c1")])
        eng = _engine(svc, planner=planner)
        _run(eng.on_execute("t1"))
        root = svc._get_node(graph, "t1")
        assert [e.action.value for e in root.run_info.action_log] == ["plan"]
        assert root.run_info.action_log[0].payload["children"] == ["c1"]
        leaf = svc._get_node(graph, "c1")
        assert [e.action.value for e in leaf.run_info.action_log] == ["dispatch"]
        assert leaf.run_info.action_log[0].payload["outcome"] == "HIT_SINGLE"

    def test_action_log_append_only_seq_monotonic(self, svc, graph):
        # append-only:seq 单调递增;多次 plan 不覆盖
        planner = StubPlanner(lambda g: [_child("c1")])
        eng = _engine(svc, planner=planner)
        _run(eng.on_execute("t1"))
        root = svc._get_node(graph, "t1")
        seqs = [e.seq for e in root.run_info.action_log]
        assert seqs == list(range(1, len(seqs) + 1))

    def test_no_plan_gap_closed_finishes(self, svc, graph):
        # Step2:plan 返 []+has_gap=F = 根 gap 闭(终验通过)→ 翻根 DONE + 图 DONE
        eng = _engine(svc, planner=StubPlanner(lambda g: [], has_gap_when_empty=False))
        _run(eng.on_execute("t1"))
        assert svc._get_node(graph, "t1").status == Status.SUCCESS
        assert graph.status == Status.SUCCESS

    def test_not_pending_root_no_op(self, svc, graph):
        svc.update_task_node_info(_patch("t1", "t1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        planner = StubPlanner(lambda g: [_child("c1")])
        eng = _engine(svc, planner=planner)
        _run(eng.on_execute("t1"))
        assert planner.plan_calls == 0


# ===== external-managed workflow/yaml isolation =====
class TestExternalManagedIsolation:
    @pytest.mark.parametrize("task_type", ["workflow", "yaml"])
    def test_on_execute_does_not_enter_dynamic_planner(self, svc, task_type):
        graph = svc.initialize_graph(_task_info(task_type=task_type))
        planner = StubPlanner(lambda g: [_child("should-not-exist")])
        dispatcher = StubDispatcher()
        eng = _engine(svc, planner=planner, dispatcher=dispatcher)

        _run(eng.on_execute("t1"))

        assert planner.plan_calls == 0
        assert dispatcher is not None
        assert [n.node_id for n in graph.tasks] == ["t1"]
        assert graph.tasks[0].status == Status.PENDING

    @pytest.mark.parametrize("task_type", ["workflow", "yaml"])
    def test_report_only_updates_graph_without_driving_next_step(self, svc, task_type):
        graph = svc.initialize_graph(_task_info(task_type=task_type))
        svc.update_task_node_info(
            _patch("t1", "t1", status=Status.RUNNING, run_mode="single_bot", assignee="b")
        )
        planner = StubPlanner(lambda g: [_child("should-not-exist")])
        dispatcher = StubDispatcher()
        runner = StubRunner()
        eng = _engine(svc, planner=planner, dispatcher=dispatcher, runner=runner)

        _run(
            eng.on_report(
                _patch(
                    "t1",
                    "t1",
                    output_patch={"content": "third-party result"},
                    acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE),
                )
            )
        )

        assert graph.tasks[0].status == Status.SUCCESS
        assert graph.status == Status.SUCCESS
        assert planner.plan_calls == 0
        assert runner.run_calls == []
        assert [n.node_id for n in graph.tasks] == ["t1"]

    @pytest.mark.parametrize("task_type", ["workflow", "yaml"])
    def test_failure_report_does_not_reset_or_redispatch(self, svc, task_type):
        graph = svc.initialize_graph(_task_info(task_type=task_type))
        svc.update_task_node_info(
            _patch("t1", "t1", status=Status.RUNNING, run_mode="coop_group", assignee="group-1")
        )
        runner = StubRunner()
        eng = _engine(svc, runner=runner)

        _run(
            eng.on_report(
                _patch(
                    "t1",
                    "t1",
                    acceptance_result=AcceptanceResult(
                        verdict=AcceptanceVerdict.FAILED, gaps=["third-party failure"]
                    ),
                )
            )
        )
        assert graph.tasks[0].status == Status.DONE
        assert runner.run_calls == []

        _run(eng.on_harness(_patch("t1", "t1", exec_error="timeout")))
        assert graph.tasks[0].status == Status.DONE
        assert runner.run_calls == []

    @pytest.mark.parametrize("task_type", ["workflow", "yaml"])
    def test_redrive_does_not_dispatch_external_task(self, svc, task_type):
        graph = svc.initialize_graph(_task_info(task_type=task_type))
        runner = StubRunner()
        eng = _engine(svc, runner=runner)

        _run(eng.redrive("t1"))

        assert runner.run_calls == []
        assert [n.node_id for n in graph.tasks] == ["t1"]


# ===== redrive:解除崩溃遗留的陈旧飞行态 =====
class TestRedriveUnstickDispatching:
    def test_redrive_unsticks_stale_dispatching(self, svc, graph):
        """崩溃在 prepare→drain 中途遗留 dispatching=True + 陈旧 dispatching_at(超阈值)→ redrive 清之
        → _prepare_into 重派 → start_run → RUNNING(否则节点永久卡死 PENDING)。"""
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        # 模拟崩溃遗留:PENDING + dispatching=True + dispatching_at = 120s 前(超默认阈值 60s)
        svc.update_task_node_info(
            _patch("t1", "c1", run_mode="single_bot", assignee="b",
                   extend_props_patch={"dispatching": True, "dispatching_at": int(time.time() * 1000) - 120_000})
        )
        runner = StubRunner()
        eng = _engine(svc, dispatcher=StubDispatcher(), runner=runner)
        _run(eng.redrive("t1"))
        n = svc._get_node(graph, "c1")
        assert n.status == Status.RUNNING  # 陈旧飞行态被清 → 重派成功
        assert n.run_info.extend_props.get("dispatching") in (None, False)
        assert len(runner.run_calls) == 1

    def test_redrive_keeps_fresh_dispatching(self, svc, graph):
        """在途派发(dispatching=True + 新鲜 dispatching_at < 阈值)不清——redrive 可在本实例正在驱动时
        被周期 recovery 触发,盲清会与在途 start_run 双派发;新鲜在途由 _prepare_into 飞行态闸挡住。"""
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(
            _patch("t1", "c1", status=Status.PENDING, run_mode="single_bot", assignee="b",
                   extend_props_patch={"dispatching": True, "dispatching_at": int(time.time() * 1000)})
        )
        runner = StubRunner()
        eng = _engine(svc, dispatcher=StubDispatcher(), runner=runner)
        _run(eng.redrive("t1"))
        n = svc._get_node(graph, "c1")
        assert n.status == Status.PENDING  # 新鲜在途未清 → prepare 跳过 → 不重派
        assert n.run_info.extend_props.get("dispatching") is True
        assert runner.run_calls == []


# ===== on_report PASS =====
class TestOnReportPass:
    def _setup_running_children(self, svc, graph, n=2):
        svc.add_task_nodes([_child(f"c{i}") for i in range(n)], parent_node_id="t1")
        for i in range(n):
            svc.update_task_node_info(_patch("t1", f"c{i}", status=Status.RUNNING, run_mode="single_bot", assignee="b"))

    def test_pass_partial_siblings_wait(self, svc, graph):
        self._setup_running_children(svc, graph, 2)
        eng = _engine(svc, planner=StubPlanner(lambda g: [_child("c_proceed")]))
        _run(eng.on_report(_patch("t1", "c0", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        assert svc._get_node(graph, "c0").status == Status.SUCCESS
        assert eng._planner.plan_calls == 0

    def test_pass_all_siblings_plan_new(self, svc, graph):
        self._setup_running_children(svc, graph, 2)
        runner = StubRunner()
        eng = _engine(svc, planner=StubPlanner(lambda g: [_child("c_proceed")]), runner=runner)
        _run(eng.on_report(_patch("t1", "c0", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        assert svc._get_node(graph, "c_proceed").status == Status.RUNNING
        assert len(runner.run_calls) == 1

    def test_pass_all_siblings_gap_closed_root_done(self, svc, graph):
        # 语义A:plan 返 []=gap 闭=终验通过 → 翻根 DONE + 图 DONE(无需 owner bot 单独回投)
        self._setup_running_children(svc, graph, 2)
        eng = _engine(svc, planner=StubPlanner(lambda g: [], has_gap_when_empty=False))
        _run(eng.on_report(_patch("t1", "c0", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        assert svc._get_node(graph, "t1").status == Status.SUCCESS  # gap 闭=终验通过→翻根 SUCCESS
        assert graph.status == Status.SUCCESS

    def test_root_gap_closed_finish_graph(self, svc, graph):
        # 语义A:c0 PASS → plan[]→ gap 闭=终验通过 → 翻根 DONE + graph DONE(一步到位,不再等回投)
        self._setup_running_children(svc, graph, 1)
        eng = _engine(svc, planner=StubPlanner(lambda g: [], has_gap_when_empty=False))
        _run(eng.on_report(_patch("t1", "c0", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        assert svc._get_node(graph, "t1").status == Status.SUCCESS  # 不再等回投
        assert graph.status == Status.SUCCESS




# 3 级图 root->结构中父 m->叶执行 lm:owner bot plan 逐条验收 + 结构父 gap 闭翻 DONE 补全 run_info
class _VerdictPlanner:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.plan_calls = 0
    async def plan(self, graph, target_node_id=None) -> PlanResult:
        self.plan_calls += 1
        return PlanResult(children=[], has_gap=False, acceptance_verdicts=list(self.verdicts))


class TestStructuralParentGapClosedRollup:
    # lm DONE -> 父 m1 gap 闭翻 DONE:补全 run_info(验收执行者=owner 落 run_mode/assignee +
    # 父自身 acceptance_result 逐条结论 + output 滚子交付物); root 一跳 done_children 看到非空
    # m1.output -> plan(t1) gap 闭 -> 图 DONE(不再 gap_no_progress 死循环)
    def test_mid_rollup_and_root_finish(self, svc, graph):
        # 结构父 m1(子 t1, run_mode 留 None); 叶执行 lm(子 m1), single_bot 跑
        svc.add_task_nodes([_child("m1")], parent_node_id="t1")
        svc.add_task_nodes([_child("lm")], parent_node_id="m1")
        svc.update_task_node_info(_patch("t1", "lm", status=Status.RUNNING, run_mode="single_bot", assignee="worker_bot"))
        planner = _VerdictPlanner([{"ac_id": "ac1", "passed": True, "reason": "名册3位齐全"}])
        eng = _engine(svc, planner=planner)
        _run(eng.on_report(_patch("t1", "lm",
            acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE),
            output_patch={"output": "# 架构师名册\n章文嵩/毕玄/唐洪"})))
        # 结构父 m1: gap 闭翻 DONE, 补全 run_info(验收执行者=owner=b1)
        m = svc._get_node(graph, "m1")
        assert m.status == Status.SUCCESS
        assert m.run_info.run_mode == "single_bot"
        assert m.run_info.assignee == "b1"
        assert m.run_info.output == {"output": "# 架构师名册\n章文嵩/毕玄/唐洪"}
        assert m.run_info.acceptance_result is not None
        assert m.run_info.acceptance_result.verdict == AcceptanceVerdict.DONE
        assert m.run_info.acceptance_result.acceptances_metric == [{"ac1": "名册3位齐全"}]
        # root: 一跳 SUCCESS children 看到非空 m1.output -> plan(t1) gap 闭 -> 图 SUCCESS
        root = svc._get_node(graph, "t1")
        assert root.status == Status.SUCCESS
        assert graph.status == Status.SUCCESS
        assert root.run_info.run_mode == "single_bot"
        assert root.run_info.assignee == "b1"
        assert root.run_info.output  # 滚子交付物非空
        assert planner.plan_calls == 2

# ===== on_report FAIL =====
class TestOnReportFail:
    def test_acceptance_fail_to_hung(self, svc, graph):
        """验收 FAIL→直接 HUNG(不再"落 FAILED 交 harness 重派同一执行体"):避免重派后 RUNNING 却顶 FAILED
        验收的不一致态。置 HUNG 后是否升 BBS 走既有 _hung_and_escalate 逻辑,本例只校验状态不再停在 FAILED/RUNNING。"""
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        planner = StubPlanner(lambda g: [_child("c1_remedy")])
        eng = _engine(svc, planner=planner)
        _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["缺x"]))))
        assert svc._get_node(graph, "c1").status == Status.HUNG
        assert planner.plan_calls == 0  # 不 plan 补救(HUNG 冒泡/升 BBS 交既有逻辑)

    def test_acceptance_fail_folds_running_to_hung(self, svc, graph):
        """乙' a+R1:动态验收 FAIL 叶折叠 RUNNING→HUNG(纯语义归并,跳过 FAILED 瞬态)——
        on_report 一次写直驱(r 可见 new_status==HUNG,非 FAILED),hung_reason=acceptance_fail 落库,
        根节点进入 BBS 时 loop_round++，节点级 HUNG 本身不计次。"""
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        planner = StubPlanner(lambda g: [_child("c1_remedy")])
        eng = _engine(svc, planner=planner)
        r = _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["缺x"]))))
        assert r.new_status == Status.HUNG
        n = svc._get_node(graph, "c1")
        assert n.status == Status.HUNG
        assert n.run_info.acceptance_result is not None
        assert n.run_info.acceptance_result.gaps == ["缺x"]
        assert graph.loop_round == 1
        assert planner.plan_calls == 0

    def test_pull_fail_status_folded_to_hung(self, svc, graph):
        """pull/poller 路径:_adapt_poller case③(success=false+gaps)产 status=FAILED+verdict FAILED+gaps。
        on_report 折叠门须把 status=FAILED 也归并回 HUNG(与 push status=HUNG 对齐),否则节点落 FAILED
        而 _on_fail_collect/_escalate_hung 假定已 HUNG → 链路断。本例固化 pull FAIL→HUNG 闭环。"""
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        planner = StubPlanner(lambda g: [_child("c1_remedy")])
        eng = _engine(svc, planner=planner)
        # 模拟 pull 路径:adapter 已置 status=FAILED(非 None)
        r = _run(eng.on_report(_patch(
            "t1", "c1",
            status=Status.FAILED,
            acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["缺x"]),
        )))
        assert r.new_status == Status.HUNG
        n = svc._get_node(graph, "c1")
        assert n.status == Status.HUNG
        assert n.run_info.acceptance_result is not None
        assert n.run_info.acceptance_result.gaps == ["缺x"]
        assert graph.loop_round == 1
        assert planner.plan_calls == 0  # HUNG 冒泡/升 BBS 交既有逻辑,不 plan 补救

    def test_non_root_hung_does_not_consume_loop_round(self, svc, graph):
        """子节点 HUNG 只做向上阻塞传播;兄弟仍活跃时不进入根 BBS,不增加 loop_round。"""
        svc.add_task_nodes([_child("c1"), _child("c2")], parent_node_id="t1")
        for node_id in ("c1", "c2"):
            svc.update_task_node_info(
                _patch("t1", node_id, status=Status.RUNNING, run_mode="single_bot", assignee="b")
            )
        svc.update_task_node_info(_patch("t1", "c1", extend_props_patch={"harness_retries": 3}))
        eng = _engine(svc, planner=StubPlanner(lambda g: []), runner=StubRunner())

        _run(eng.on_harness(_patch("t1", "c1", exec_error="exec_failed_retry")))

        assert svc._get_node(graph, "c1").status == Status.HUNG
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        assert graph.loop_round == 0
        assert graph.extend_props.get("bbs_mode") is None

    def test_late_terminal_sibling_reconciles_root_hung_and_bbs(self, svc, graph):
        """A HUNG child seen while a sibling is active is rechecked when that sibling finishes."""
        svc.add_task_nodes([_child("c1"), _child("c2")], parent_node_id="t1")
        for node_id in ("c1", "c2"):
            svc.update_task_node_info(
                _patch("t1", node_id, status=Status.RUNNING, run_mode="single_bot", assignee="b")
            )
        runner = StubRunner()
        eng = _engine(svc, planner=StubPlanner(lambda g: []), runner=runner)

        async def _go():
            # First HUNG propagation observes c2 still active and must wait.
            eng._hung_and_escalate("t1", "c1", "exec_stuck")
            assert svc._get_node(graph, "t1").status == Status.PLANNING
            assert graph.loop_round == 0

            # c2 completes through a status-only callback, which historically skipped
            # _on_pass_collect. The reconciliation hook must close the root.
            await eng.on_report(_patch("t1", "c2", status=Status.DONE))
            if eng._bg_tasks:
                await _wait_bg_tasks(eng)

        _run(_go())
        assert svc._get_node(graph, "t1").status == Status.HUNG
        assert graph.extend_props.get("bbs_mode") is True
        assert graph.loop_round == 1


    def test_exec_error_harness_retry_redispatch(self, svc, graph):
        """exec_error(执行报错/传输失败)→harness 重新派发执行(不拆):RUNNING→PENDING→dispatch→RUNNING。
        与验收 FAIL 不同:执行报错为临时性失败,重派有意义(验收不过属内容 gap,已直接 HUNG)。"""
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        runner = StubRunner()
        eng = _engine(svc, dispatcher=StubDispatcher(), runner=runner)
        _run(eng.on_report(_patch("t1", "c1", exec_error="transport_fail")))  # 执行报错→harness 重新派发
        assert svc._get_node(graph, "c1").status == Status.RUNNING  # 重新派发执行
        assert len(runner.run_calls) == 1

    def test_fail_harness_max_hung_escalate(self, svc):
        """v4:harness 重试达 MAX_HARNESS→节点 HUNG;传播到根后升 BBS(loop_round++,bbs_mode)。"""
        g = svc.initialize_graph(_task_info("t2", max_depth=1))
        svc.add_task_nodes([_child("c1", "t2")], parent_node_id="t2")
        svc.update_task_node_info(_patch("t2", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t2", "c1", extend_props_patch={"harness_retries": 3}))
        eng = _engine(svc, planner=StubPlanner(lambda g: []))
        _run(eng.on_harness(_patch("t2", "c1", exec_error="exec_failed_retry")))
        assert svc._get_node(g, "c1").status == Status.HUNG
        assert any(n.node_id == "c1" for n in g.tasks)  # v4 保留(不 remove)
        assert g.loop_round == 1
        assert g.extend_props.get("bbs_mode") is True

    def test_loop_exhausted_graph_hung(self, svc):
        """v4:loop_round 达 MAX_LOOP→图 HUNG(hung_reason=loop_exhausted)。"""
        g = svc.initialize_graph(_task_info("t3", max_depth=1))
        g.extend_props["execution_config"]["MAX_LOOP"] = 1
        g.loop_round = 1  # 先判后+1:预设 loop 已达 MAX_LOOP,首次 _bump_loop_round 先判即撞图 HUNG
        svc.add_task_nodes([_child("c1", "t3")], parent_node_id="t3")
        svc.update_task_node_info(_patch("t3", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t3", "c1", extend_props_patch={"harness_retries": 3}))
        eng = _engine(svc, planner=StubPlanner(lambda g: []))
        _run(eng.on_harness(_patch("t3", "c1", exec_error="x")))
        assert g.status == Status.HUNG
        assert g.extend_props.get("hung_reason") == "loop_exhausted"


# ===== on_miss =====
class TestOnMiss:
    def test_miss_below_max_split(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        runner = StubRunner()
        eng = _engine(svc, dispatcher=StubDispatcher(), planner=StubPlanner(lambda g: [_child("c1_split")]), runner=runner)
        _run(eng.on_miss(_patch("t1", "c1", extend_props_patch={"miss_events": ["no_bot"]})))
        assert svc._get_node(graph, "c1_split").status == Status.RUNNING
        assert len(runner.run_calls) == 1

    def test_miss_escalate_bbs_at_max(self, svc):
        g = svc.initialize_graph(_task_info("t4", max_depth=1))
        svc.add_task_nodes([_child("c1", "t4")], parent_node_id="t4")
        eng = _engine(svc, planner=StubPlanner(lambda g: []))
        _run(eng.on_miss(_patch("t4", "c1", extend_props_patch={"miss_events": ["no_bot"]})))
        assert g.loop_round == 1
        assert g.extend_props.get("bbs_mode") is True


# ===== on_harness =====
class TestOnHarness:
    def test_reset_and_redispatch(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        runner = StubRunner()
        eng = _engine(svc, runner=runner)
        _run(eng.on_harness(_patch("t1", "c1", status=Status.PENDING, extend_props_patch={"crash": "timeout"})))
        assert svc._get_node(graph, "c1").status == Status.RUNNING
        assert len(runner.run_calls) == 1

    def test_root_hung_schedules_bbs(self, svc):
        """调度优化:任一根 HUNG(此处 exec_stuck 冒泡到根)即经 start_run 调度 BBS。"""
        g = svc.initialize_graph(_task_info("t_bbs", max_depth=1))
        g.extend_props["execution_config"]["MAX_LOOP"] = 10  # 确保不撞反失控兜底
        svc.add_task_nodes([_child("c1", "t_bbs")], parent_node_id="t_bbs")
        svc.update_task_node_info(_patch("t_bbs", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t_bbs", "c1", extend_props_patch={"harness_retries": 3}))
        runner = StubRunner()
        eng = _engine(svc, planner=StubPlanner(lambda g: []), runner=runner)

        async def _go():
            await eng.on_harness(_patch("t_bbs", "c1", exec_error="exec_failed_retry"))
            if eng._bg_tasks:  # 排空 fire-and-forget bbs 任务,断言已调度
                await _wait_bg_tasks(eng)

        _run(_go())
        assert svc._get_node(g, "t_bbs").status == Status.HUNG  # exec_stuck 冒泡到根→根 HUNG
        assert g.extend_props.get("bbs_mode") is True
        assert len(runner.bbs_calls) == 1  # 新行为:根 HUNG 即调度 bbs(恰好一次)

    def test_loop_exhausted_does_not_schedule_bbs(self, svc):
        """反失控兜底:loop_round 达 MAX_LOOP→图 HUNG(loop_exhausted),不再调度 BBS。"""
        g = svc.initialize_graph(_task_info("t_loop", max_depth=1))
        g.extend_props["execution_config"]["MAX_LOOP"] = 1
        g.loop_round = 1  # 先判后+1:预设 loop 已达 MAX_LOOP,首次 _bump_loop_round 先判即撞图 HUNG
        svc.add_task_nodes([_child("c1", "t_loop")], parent_node_id="t_loop")
        svc.update_task_node_info(_patch("t_loop", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t_loop", "c1", extend_props_patch={"harness_retries": 3}))
        runner = StubRunner()
        eng = _engine(svc, planner=StubPlanner(lambda g: []), runner=runner)

        async def _go():
            await eng.on_harness(_patch("t_loop", "c1", exec_error="x"))
            if eng._bg_tasks:
                await _wait_bg_tasks(eng)

        _run(_go())
        assert g.status == Status.HUNG
        assert g.extend_props.get("hung_reason") == "loop_exhausted"
        assert runner.bbs_calls == []  # MAX_LOOP 硬停:不调度 bbs


# ===== loop_round 仅升 BBS++ =====
class TestLoopRound:
    def test_acceptance_fail_escalates_bumps_loop_round(self, svc, graph):
        # 验收 FAIL→直接 HUNG;根确认进入 BBS 时才 _bump_loop_round(loop_round++)。
        # 旧"落 FAILED 交 harness 重派不 bump"前提已废弃(验收不过属内容 gap,直 HUNG 升级,非 normal remedy)。
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        before = graph.loop_round
        eng = _engine(svc, planner=StubPlanner(lambda g: [_child("c1_remedy")]))
        _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["x"]))))
        assert graph.loop_round == before + 1





# ===== MAX_PLAN_ROUND 节点级重规划闸 =====
class TestMaxPlanRound:
    def test_root_replan_to_cap_hungs_root(self, svc, graph):
        """v5:根子全 DONE→gap 未闭→重 plan 产子,MAX_PLAN_ROUND 次产子后→根 HUNG(plan_round_exhausted),不再产子。
        X2 计数(先判后+1):plan_round 现值<MAX 产子并+1,达 MAX 撞顶。MAX=2→产 c_r1(0→1)、c_r2(1→2),第3次 plan_round=2>=2 撞顶。"""
        graph.extend_props["execution_config"]["MAX_PLAN_ROUND"] = 2
        # 首帧 plan 产 c1(初始规划不计 plan_round)
        planner = StubPlanner(lambda g: [_child("c1")])
        eng = _engine(svc, planner=planner, runner=StubRunner())
        _run(eng.on_execute("t1"))  # t1 PENDING→PLANNING,产 c1 RUNNING
        # 回投 c1 PASS → 根重 plan → plan_round=0 < 2 产 c_r1 → plan_round=1
        planner._factory = lambda g: [_child("c_r1")]
        _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        assert svc._get_node(graph, "t1").status != Status.HUNG
        assert svc._get_node(graph, "c_r1").status == Status.RUNNING
        assert svc._get_node(graph, "t1").run_info.extend_props.get("plan_round") == 1
        # 回投 c_r1 PASS → 根重 plan → plan_round=1 < 2 产 c_r2 → plan_round=2
        planner._factory = lambda g: [_child("c_r2")]
        _run(eng.on_report(_patch("t1", "c_r1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        assert svc._get_node(graph, "t1").status != Status.HUNG
        assert svc._get_node(graph, "c_r2").status == Status.RUNNING
        assert svc._get_node(graph, "t1").run_info.extend_props.get("plan_round") == 2
        # 回投 c_r2 PASS → 根重 plan → plan_round=2 >= MAX → 根 HUNG,不产子;升 BBS 重置 plan_round=loop_round=1
        planner._factory = lambda g: [_child("c_r3")]
        _run(eng.on_report(_patch("t1", "c_r2", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        root = svc._get_node(graph, "t1")
        assert root.status == Status.HUNG
        assert root.run_info.extend_props.get("hung_reason") == "plan_round_exhausted"
        assert root.run_info.extend_props.get("plan_round") == 1  # 升 BBS 收口重置 plan_round=loop_round=1(X2 计数下每轮产子=MAX-loop)
        assert svc._get_node(graph, "c_r3") is None  # 达上限不再产子

    def test_below_cap_no_hung(self, svc, graph):
        """未达 MAX_PLAN_ROUND → 继续产子不 HUNG。"""
        graph.extend_props["execution_config"]["MAX_PLAN_ROUND"] = 5
        planner = StubPlanner(lambda g: [_child("c1")])
        eng = _engine(svc, planner=planner, runner=StubRunner())
        _run(eng.on_execute("t1"))
        planner._factory = lambda g: [_child("c_r1")]
        _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        assert svc._get_node(graph, "c_r1").status == Status.RUNNING

    def test_default3_cap_bbs_escalate_loop_exhausted(self, svc, graph):
        """默认 MAX_PLAN_ROUND=MAX_LOOP=3:root 重规划 3 次产子撞顶→升 BBS(plan_round 重置=loop_round)→
        BBS 接力逐轮递减产子(3/2/1/0)→loop 撞 MAX_LOOP 图 HUNG(loop_exhausted)硬停不调度。先判后+1 全程。

        重规划产子预算 = MAX_PLAN_ROUND - loop_round,逐轮 3/2/1/0;升 BBS 接力 3 次(loop 1/2/3),第 4 次撞图 HUNG。
        """
        runner = StubRunner()
        planner = StubPlanner(lambda g: [_child("c1")])
        eng = _engine(svc, planner=planner, runner=runner)
        _run(eng.on_execute("t1"))  # 首帧 c1(plan_round 不计)

        def _pass(node):
            async def _go():
                await eng.on_report(_patch("t1", node, acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE)))
                if eng._bg_tasks:  # 排空 fire-and-forget start_run,使 bbs_calls 落定
                    await _wait_bg_tasks(eng)
            _run(_go())

        def _plan(nid):
            planner._factory = lambda g, n=nid: [_child(n)]

        def _redispatch(node):  # 模拟 BBS scoped re-dispatch:已 DONE 叶复位 RUNNING,再 on_report PASS 触发 on_pass(root)
            svc.update_task_node_info(_patch("t1", node, status=Status.RUNNING))

        # ---- 首轮:3 次重规划产子(plan_round 0→1→2→3),c4 PASS 撞顶→升 BBS1 ----
        for src, nxt in [("c1", "c2"), ("c2", "c3"), ("c3", "c4")]:
            _plan(nxt)
            _pass(src)
        _plan("c5")
        _pass("c4")  # plan_round=3>=3 撞顶,c5 不产
        root = svc._get_node(graph, "t1")
        assert root.status == Status.HUNG
        assert root.run_info.extend_props.get("hung_reason") == "plan_round_exhausted"
        assert root.run_info.extend_props.get("plan_round") == 1  # 升 BBS 重置=loop_round=1
        assert graph.loop_round == 1
        assert len(runner.bbs_calls) == 1
        assert svc._get_node(graph, "c5") is None  # 撞顶不产子

        # ---- BBS1(plan reset=1):re-dispatch c4 触发;产 c5(1→2)、c6(2→3);c6 PASS 撞→升 BBS2 ----
        _redispatch("c4")
        _plan("c5")
        _pass("c4")
        assert svc._get_node(graph, "c5").status == Status.RUNNING
        assert svc._get_node(graph, "t1").run_info.extend_props.get("plan_round") == 2
        _plan("c6")
        _pass("c5")
        assert svc._get_node(graph, "c6").status == Status.RUNNING
        assert svc._get_node(graph, "t1").run_info.extend_props.get("plan_round") == 3
        _plan("c7")
        _pass("c6")  # 撞顶→升 BBS2
        assert svc._get_node(graph, "t1").run_info.extend_props.get("plan_round") == 2  # 重置=loop=2
        assert graph.loop_round == 2
        assert len(runner.bbs_calls) == 2
        assert svc._get_node(graph, "c7") is None

        # ---- BBS2(plan reset=2):re-dispatch c6 触发;产 c7(2→3);c7 PASS 撞→升 BBS3 ----
        _redispatch("c6")
        _plan("c7")
        _pass("c6")
        assert svc._get_node(graph, "c7").status == Status.RUNNING
        assert svc._get_node(graph, "t1").run_info.extend_props.get("plan_round") == 3
        _plan("c8")
        _pass("c7")  # 撞顶→升 BBS3
        assert svc._get_node(graph, "t1").run_info.extend_props.get("plan_round") == 3  # 重置=loop=3
        assert graph.loop_round == 3
        assert len(runner.bbs_calls) == 3
        assert svc._get_node(graph, "c8") is None
        assert graph.status != Status.HUNG  # loop=3 但先判 2<3 +1,图仍 RUNNING,schedule 了 BBS3

        # ---- BBS3(plan reset=3,loop=3):re-dispatch c7 触发;plan=3>=3 撞→_bump_loop 先判 loop=3>=3→图 HUNG 硬停 ----
        _redispatch("c7")
        _plan("c8")
        _pass("c7")
        assert graph.status == Status.HUNG
        assert graph.extend_props.get("hung_reason") == "loop_exhausted"
        assert len(runner.bbs_calls) == 3  # loop 收尾硬停,不再调度 BBS

# ===== 零 case 知识 =====
class TestZeroCaseKnowledge:
    def test_no_node_name_literals_in_engine(self):
        import agentclaw.community.core.task.task_center.engine as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"engine 出现写死节点名: {hits}"


# ===== plan() 异常韧性(plan_call_fail 重试,不 abort on_execute) =====
class _RaisingThenOkPlanner:
    """plan() 先抛指定次数异常,之后返回 then_kids(模拟 sofa_tracer httpx send hook 等传输异常)。"""

    def __init__(self, raise_times: int, then_kids: list[TaskNode] | None = None):
        self.calls = 0
        self.raise_times = raise_times
        self.then_kids = then_kids or []

    async def plan(self, graph, target_node_id: str | None = None) -> PlanResult:
        self.calls += 1
        if self.calls <= self.raise_times:
            raise RuntimeError("simulated httpx send hook fail")
        return PlanResult(children=list(self.then_kids), has_gap=bool(self.then_kids))


class TestPlanRetryOnException:
    def test_planner_transport_exception_retried_then_ok(self, svc, graph):
        # plan() 第一次抛异常→plan_call_fail 重试→第二次产子→正常 dispatch,不再 abort on_execute
        planner = _RaisingThenOkPlanner(raise_times=1, then_kids=[_child("c1")])
        eng = _engine(svc, planner=planner)
        _run(eng.on_execute("t1"))
        assert planner.calls == 2  # 1 抛异常(plan_call_fail 重试) + 1 产子
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        assert svc._get_node(graph, "c1").status == Status.RUNNING

    def test_planner_always_raises_exhausts_to_hung_escalate(self, svc, graph):
        # plan() 恒抛→耗尽 MAX_HARNESS(默认 2)→pr=plan_call_fail(has_gap=T,无子)→HUNG 升 BBS,不 abort
        planner = _RaisingThenOkPlanner(raise_times=99)
        eng = _engine(svc, planner=planner)
        _run(eng.on_execute("t1"))
        assert planner.calls == 2  # MAX_HARNESS 次,全部 plan_call_fail
        assert svc._get_node(graph, "t1").status == Status.HUNG
        assert graph.extend_props.get("bbs_mode") is True
        assert graph.loop_round == 1


class TestOnPassBbsRecoverableGuard:
    """Path 2 回归:图 bbs_mode 未 claim(可恢复态)下,结构叶最后 DONE 必须放行 owner 复核根 gap 收口。

    修复前 _on_pass_collect 守卫的 ``else: return``("停手等 BBS 接力")会死锁:走到守卫前 step①②已
    保证兄弟全 DONE,且 bbs_owner=None 表示无在途接力;而 root 非 HUNG 不会再重升 BBS,于是无人收口,
    根卡 HUNG、图卡未终态。修复后普通叶最后 DONE 亦放行 → gap 闭 → _maybe_finish_graph(根 mode①
    HUNG→DONE)→ 图 DONE。
    """

    def test_struct_leaf_last_done_converges_graph_done(self, svc):
        g = svc.initialize_graph(_task_info("t_p2", max_depth=3))
        # 图级升 BBS 可恢复态:bbs_mode=True、根 HUNG、bbs_owner 未 claim(None)
        svc.update_task_graph_info("t_p2", TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
        svc.update_task_node_info(_patch("t_p2", "t_p2", status=Status.HUNG))  # PENDING→HUNG 合法
        # bbs scoped 子:已 DONE(run_mode=bbs,claim 已释放)
        svc.add_task_nodes([_child("bbs_n", "t_p2")], parent_node_id="t_p2")
        svc.update_task_node_info(_patch("t_p2", "bbs_n", status=Status.RUNNING, run_mode="bbs", assignee="botB"))
        svc.update_task_node_info(_patch("t_p2", "bbs_n", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE)))
        # 结构叶:running,待 on_report 驱动(本轮 PASS 触发者)
        svc.add_task_nodes([_child("c_struct", "t_p2")], parent_node_id="t_p2")
        svc.update_task_node_info(_patch("t_p2", "c_struct", status=Status.RUNNING, run_mode="single_bot", assignee="bot"))
        # planner:gap 闭(无新子)→ _maybe_finish_graph
        eng = _engine(svc, planner=StubPlanner(lambda _g: [], has_gap_when_empty=False))

        # 结构叶最后 DONE → _on_pass_collect(c_struct) 触发守卫
        _run(eng.on_report(_patch("t_p2", "c_struct", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))

        # 修复后:放行 → gap 闭 → 图 DONE、根 DONE(不再卡 HUNG 死锁)
        assert svc._get_node(g, "t_p2").status == Status.SUCCESS
        assert g.status == Status.SUCCESS

    def test_struct_leaf_last_done_gap_open_replans_or_hungs(self, svc):
        """同可恢复态,但 gap 未闭(planner 产新子或 has_gap)→ 不死锁:要么重 plan 产子,要么 HUNG 重升 BBS,
        根态推进而非卡 HUNG 不变。本例 planner 产新子 → 根回到 PLANNING 并发新子。"""
        g = svc.initialize_graph(_task_info("t_p3", max_depth=3))
        svc.update_task_graph_info("t_p3", TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
        svc.update_task_node_info(_patch("t_p3", "t_p3", status=Status.HUNG))
        svc.add_task_nodes([_child("bbs_n", "t_p3")], parent_node_id="t_p3")
        svc.update_task_node_info(_patch("t_p3", "bbs_n", status=Status.RUNNING, run_mode="bbs", assignee="botB"))
        svc.update_task_node_info(_patch("t_p3", "bbs_n", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE)))
        svc.add_task_nodes([_child("c_struct", "t_p3")], parent_node_id="t_p3")
        svc.update_task_node_info(_patch("t_p3", "c_struct", status=Status.RUNNING, run_mode="single_bot", assignee="bot"))
        runner = StubRunner()
        eng = _engine(svc, planner=StubPlanner(lambda _g: [_child("c_new", "t_p3")]), runner=runner)

        _run(eng.on_report(_patch("t_p3", "c_struct", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))

        # gap 未闭 → 重 plan 产新子 + dispatch(不死锁):根不再卡 HUNG,新子进入 RUNNING
        assert svc._get_node(g, "c_new") is not None
        assert svc._get_node(g, "c_new").status == Status.RUNNING
        assert len(runner.run_calls) >= 1
