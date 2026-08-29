"""M2 ExecutionEngine on_* 单测(对齐 tasks.md T2.x)。

in-test CaseEngine(ExecutionEngine)子类覆写 _build_* 注入 stub planner/dispatcher/runner(T1=A corp 最简形态);
真实 TaskGraphService。验收 100% 回投(无 verify port);BBS 投递归 runner(无 bbs market port)。
覆盖:on_execute 首帧、on_report PASS 传播/根等回投、on_report FAIL 补救/升 BBS、on_miss 拆细/升 BBS、
on_harness 复位重投、loop_round 仅升 BBS++、零 case grep。
"""
from __future__ import annotations

import asyncio
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
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


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
        self.bbs_calls: list = []   # engine 升 BBS 可恢复态时 run_bbs 调用的 task_id
        self._groups = []

    async def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        self.run_calls.append(list(toDoTaskList))
        return [True] * len(toDoTaskList)

    async def run_bbs(self, execution_graph) -> None:
        """记录 BBS 升级调度(根 HUNG 升 BBS 可恢复态时 fire-and-forget 调用;对齐真实 TaskExecutor.run_bbs)。"""
        self.bbs_calls.append(getattr(execution_graph, "task_id", None))

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


# ===== on_execute =====
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
        assert svc._get_node(graph, "t1").status == Status.DONE
        assert graph.status == Status.DONE

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

        assert graph.tasks[0].status == Status.DONE
        assert graph.status == Status.DONE
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
        assert graph.tasks[0].status == Status.FAILED
        assert runner.run_calls == []

        _run(eng.on_harness(_patch("t1", "t1", exec_error="timeout")))
        assert graph.tasks[0].status == Status.FAILED
        assert runner.run_calls == []

    @pytest.mark.parametrize("task_type", ["workflow", "yaml"])
    def test_redrive_does_not_dispatch_external_task(self, svc, task_type):
        graph = svc.initialize_graph(_task_info(task_type=task_type))
        runner = StubRunner()
        eng = _engine(svc, runner=runner)

        _run(eng.redrive("t1"))

        assert runner.run_calls == []
        assert [n.node_id for n in graph.tasks] == ["t1"]


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
        assert svc._get_node(graph, "c0").status == Status.DONE
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
        assert svc._get_node(graph, "t1").status == Status.DONE  # gap 闭=终验通过→翻根 DONE
        assert graph.status == Status.DONE

    def test_root_gap_closed_finish_graph(self, svc, graph):
        # 语义A:c0 PASS → plan[]→ gap 闭=终验通过 → 翻根 DONE + graph DONE(一步到位,不再等回投)
        self._setup_running_children(svc, graph, 1)
        eng = _engine(svc, planner=StubPlanner(lambda g: [], has_gap_when_empty=False))
        _run(eng.on_report(_patch("t1", "c0", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        assert svc._get_node(graph, "t1").status == Status.DONE  # 不再等回投
        assert graph.status == Status.DONE




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
        assert m.status == Status.DONE
        assert m.run_info.run_mode == "single_bot"
        assert m.run_info.assignee == "b1"
        assert m.run_info.output == {"output": "# 架构师名册\n章文嵩/毕玄/唐洪"}
        assert m.run_info.acceptance_result is not None
        assert m.run_info.acceptance_result.verdict == AcceptanceVerdict.DONE
        assert m.run_info.acceptance_result.acceptances_metric == [{"ac1": "名册3位齐全"}]
        # root: 一跳 done_children 看到非空 m1.output -> plan(t1) gap 闭 -> 图 DONE
        root = svc._get_node(graph, "t1")
        assert root.status == Status.DONE
        assert graph.status == Status.DONE
        assert root.run_info.run_mode == "single_bot"
        assert root.run_info.assignee == "b1"
        assert root.run_info.output  # 滚子交付物非空
        assert planner.plan_calls == 2

# ===== on_report FAIL =====
class TestOnReportFail:
    def test_fail_to_failed_no_immediate_remedy(self, svc, graph):
        """v4:验收 FAIL→FAILED,不立即补救拆子(补救改由 harness 重新派发执行重试)。"""
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        planner = StubPlanner(lambda g: [_child("c1_remedy")])
        eng = _engine(svc, planner=planner)
        _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["缺x"]))))
        assert svc._get_node(graph, "c1").status == Status.FAILED  # 落 FAILED,不补救
        assert planner.plan_calls == 0  # 不调用 plan 补救

    def test_fail_harness_retry_redispatch(self, svc, graph):
        """v4:FAILED 经 harness on_harness 重新派发执行(不拆):复位 FAILED→PENDING→dispatch→RUNNING。"""
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        runner = StubRunner()
        eng = _engine(svc, dispatcher=StubDispatcher(), runner=runner)
        _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["缺x"]))))
        assert svc._get_node(graph, "c1").status == Status.FAILED
        _run(eng.on_harness(_patch("t1", "c1", exec_error="acceptance_fail_retry")))  # harness 重新派发
        assert svc._get_node(graph, "c1").status == Status.RUNNING  # 重新派发执行
        assert len(runner.run_calls) == 1

    def test_fail_harness_max_hung_escalate(self, svc):
        """v4:harness 重试达 MAX_HARNESS→节点 HUNG + 升 BBS(loop_round++,bbs_mode;节点保留不 remove)。"""
        g = svc.initialize_graph(_task_info("t2", max_depth=1))
        svc.add_task_nodes([_child("c1", "t2")], parent_node_id="t2")
        svc.update_task_node_info(_patch("t2", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t2", "c1", extend_props_patch={"harness_retries": 2}))
        eng = _engine(svc, planner=StubPlanner(lambda g: []))
        _run(eng.on_harness(_patch("t2", "c1", exec_error="acceptance_fail_retry")))
        assert svc._get_node(g, "c1").status == Status.HUNG
        assert any(n.node_id == "c1" for n in g.tasks)  # v4 保留(不 remove)
        assert g.loop_round == 1
        assert g.extend_props.get("bbs_mode") is True

    def test_loop_exhausted_graph_hung(self, svc):
        """v4:loop_round 达 MAX_LOOP→图 HUNG(hung_reason=loop_exhausted)。"""
        g = svc.initialize_graph(_task_info("t3", max_depth=1))
        g.extend_props["execution_config"]["MAX_LOOP"] = 1
        svc.add_task_nodes([_child("c1", "t3")], parent_node_id="t3")
        svc.update_task_node_info(_patch("t3", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t3", "c1", extend_props_patch={"harness_retries": 2}))
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
        """调度优化:任一根 HUNG(此处 exec_stuck 冒泡到根)即调度 run_bbs(MAX_LOOP 未达即升 BBS)。"""
        g = svc.initialize_graph(_task_info("t_bbs", max_depth=1))
        g.extend_props["execution_config"]["MAX_LOOP"] = 10  # 确保不撞反失控兜底
        svc.add_task_nodes([_child("c1", "t_bbs")], parent_node_id="t_bbs")
        svc.update_task_node_info(_patch("t_bbs", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t_bbs", "c1", extend_props_patch={"harness_retries": 2}))
        runner = StubRunner()
        eng = _engine(svc, planner=StubPlanner(lambda g: []), runner=runner)

        async def _go():
            await eng.on_harness(_patch("t_bbs", "c1", exec_error="acceptance_fail_retry"))
            if eng._bg_tasks:  # 排空 fire-and-forget bbs 任务,断言已调度
                await asyncio.gather(*eng._bg_tasks, return_exceptions=True)

        _run(_go())
        assert svc._get_node(g, "t_bbs").status == Status.HUNG  # exec_stuck 冒泡到根→根 HUNG
        assert g.extend_props.get("bbs_mode") is True
        assert len(runner.bbs_calls) == 1  # 新行为:根 HUNG 即调度 bbs(恰好一次)

    def test_loop_exhausted_does_not_schedule_bbs(self, svc):
        """反失控兜底:loop_round 达 MAX_LOOP→图 HUNG(loop_exhausted),不再调度 run_bbs。"""
        g = svc.initialize_graph(_task_info("t_loop", max_depth=1))
        g.extend_props["execution_config"]["MAX_LOOP"] = 1
        svc.add_task_nodes([_child("c1", "t_loop")], parent_node_id="t_loop")
        svc.update_task_node_info(_patch("t_loop", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t_loop", "c1", extend_props_patch={"harness_retries": 2}))
        runner = StubRunner()
        eng = _engine(svc, planner=StubPlanner(lambda g: []), runner=runner)

        async def _go():
            await eng.on_harness(_patch("t_loop", "c1", exec_error="x"))
            if eng._bg_tasks:
                await asyncio.gather(*eng._bg_tasks, return_exceptions=True)

        _run(_go())
        assert g.status == Status.HUNG
        assert g.extend_props.get("hung_reason") == "loop_exhausted"
        assert runner.bbs_calls == []  # MAX_LOOP 硬停:不调度 bbs


# ===== loop_round 仅升 BBS++ =====
class TestLoopRound:
    def test_normal_remedy_no_increment(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        before = graph.loop_round
        eng = _engine(svc, planner=StubPlanner(lambda g: [_child("c1_remedy")]))
        _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["x"]))))
        assert graph.loop_round == before





# ===== MAX_PLAN_ROUND 节点级重规划闸 =====
class TestMaxPlanRound:
    def test_root_replan_to_cap_hungs_root(self, svc, graph):
        """v5:根子全 DONE→gap 未闭→重 plan 产子,MAX_PLAN_ROUND 次后→根 HUNG(plan_round_exhausted),不再产子。"""
        graph.extend_props["execution_config"]["MAX_PLAN_ROUND"] = 2
        # 首帧 plan 产 c1(初始规划不计 plan_round)
        planner = StubPlanner(lambda g: [_child("c1")])
        eng = _engine(svc, planner=planner, runner=StubRunner())
        _run(eng.on_execute("t1"))  # t1 PENDING→PLANNING,产 c1 RUNNING
        # 回投 c1 PASS → c1 DONE → 兄弟全 DONE → 根重 plan(产 c_r1) → plan_round=1 < 2 → 不 HUNG
        planner._factory = lambda g: [_child("c_r1")]
        _run(eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        assert svc._get_node(graph, "t1").status != Status.HUNG
        assert svc._get_node(graph, "c_r1").status == Status.RUNNING
        assert svc._get_node(graph, "t1").run_info.extend_props.get("plan_round") == 1
        # 回投 c_r1 PASS → 根重 plan → plan_round=2 >= MAX → 根 HUNG,不产子
        planner._factory = lambda g: [_child("c_r2")]
        _run(eng.on_report(_patch("t1", "c_r1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE))))
        root = svc._get_node(graph, "t1")
        assert root.status == Status.HUNG
        assert root.run_info.extend_props.get("hung_reason") == "plan_round_exhausted"
        assert root.run_info.extend_props.get("plan_round") == 2
        assert svc._get_node(graph, "c_r2") is None  # 达上限不再产子

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

# ===== 零 case 知识 =====
class TestZeroCaseKnowledge:
    def test_no_node_name_literals_in_engine(self):
        import agentclaw.community.core.task.task_center.engine as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"engine 出现写死节点名: {hits}"
