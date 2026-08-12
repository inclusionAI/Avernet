"""M2 ExecutionEngine on_* 单测(对齐 tasks.md T2.x)。

in-test stub 注入 planner/dispatcher/runner/verify/market;真实 TaskGraphService(M1)。
覆盖:on_execute 首帧、on_report PASS 传播/根终验、on_report FAIL 补救/升 BBS、
on_miss 拆细/升 BBS、on_harness 复位重投、loop_round 仅升 BBS++、零 case grep。
"""
from __future__ import annotations

from typing import Callable

import pytest

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
    TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


# ===== domain helpers =====
def _task_info(task_id: str = "t1", max_depth: int = 3) -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_channel_type="bot",
        source_channel_id="b1",
        execution_config={"MAX_DEPTH": max_depth, "BBS_MAX_DEPTH": 3},
    )


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec, run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


# ===== stubs =====
class StubPlanner:
    def __init__(self, factory: Callable[[object], list[TaskNode]] | None = None):
        self._factory = factory or (lambda g: [])
        self.plan_calls = 0

    def plan(self, graph) -> list[TaskNode]:
        self.plan_calls += 1
        return self._factory(graph)


class StubDispatcher:
    def __init__(self, run_mode="single_bot", assignee="bot1", miss=False):
        self.run_mode = run_mode
        self.assignee = assignee
        self.miss = miss

    def dispatch(self, toDoTaskList: list[TaskNode]) -> list[TaskNode]:
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

    def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        self.run_calls.append(list(toDoTaskList))
        return [True] * len(toDoTaskList)


class StubVerifyPort:
    def __init__(self):
        self.verify_calls: list[tuple[str, str]] = []

    def request_verify(self, task_id: str, node_id: str) -> None:
        self.verify_calls.append((task_id, node_id))


class StubBbsMarket:
    def __init__(self):
        self.publish_calls: list[str] = []

    def publish_task(self, task_id: str) -> None:
        self.publish_calls.append(task_id)


def _engine(svc, planner=None, dispatcher=None, runner=None, verify=None, bbs=None):
    return ExecutionEngine(
        svc,
        planner or StubPlanner(),
        dispatcher or StubDispatcher(),
        runner or StubRunner(),
        verify or StubVerifyPort(),
        bbs or StubBbsMarket(),
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
        eng.on_execute("t1")
        # 根进 PLANNING,子 RUNNING,runner 收到 2 个
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        assert svc._get_node(graph, "c1").status == Status.RUNNING
        assert svc._get_node(graph, "c2").status == Status.RUNNING
        assert len(runner.run_calls) == 1
        assert {n.node_id for n in runner.run_calls[0]} == {"c1", "c2"}

    def test_no_plan_no_op(self, svc, graph):
        eng = _engine(svc, planner=StubPlanner(lambda g: []))
        eng.on_execute("t1")
        assert svc._get_node(graph, "t1").status == Status.PENDING  # 未变

    def test_not_pending_root_no_op(self, svc, graph):
        # 根 dispatch RUNNING 后调 on_execute → 非条件 a
        svc.update_task_node_info(_patch("t1", "t1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        eng = _engine(svc, planner=StubPlanner(lambda g: [_child("c1")]))
        eng.on_execute("t1")
        assert planner_plan_count(eng) == 0


def planner_plan_count(eng) -> int:
    return eng._planner.plan_calls


# ===== on_report PASS =====
class TestOnReportPass:
    def _setup_running_children(self, svc, graph, n=2):
        svc.add_task_nodes([_child(f"c{i}") for i in range(n)], parent_node_id="t1")
        for i in range(n):
            svc.update_task_node_info(_patch("t1", f"c{i}", status=Status.RUNNING, run_mode="single_bot", assignee="b"))

    def test_pass_partial_siblings_wait(self, svc, graph):
        self._setup_running_children(svc, graph, 2)
        eng = _engine(svc, planner=StubPlanner(lambda g: [_child("c_proceed")]))
        eng.on_report(_patch("t1", "c0", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS)))
        assert svc._get_node(graph, "c0").status == Status.DONE
        # c1 未 DONE → 兄弟未齐 → 不 plan/add
        assert planner_plan_count(eng) == 0

    def test_pass_all_siblings_plan_new(self, svc, graph):
        self._setup_running_children(svc, graph, 2)
        runner = StubRunner()
        eng = _engine(svc, planner=StubPlanner(lambda g: [_child("c_proceed")]), runner=runner)
        eng.on_report(_patch("t1", "c0", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS)))
        eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS)))
        # 全 DONE → plan 返新子 → add+dispatch
        assert svc._get_node(graph, "c_proceed").status == Status.RUNNING
        assert len(runner.run_calls) == 1

    def test_pass_all_siblings_gap_closed_root_verify(self, svc, graph):
        self._setup_running_children(svc, graph, 2)
        verify = StubVerifyPort()
        eng = _engine(svc, planner=StubPlanner(lambda g: []), verify=verify)
        eng.on_report(_patch("t1", "c0", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS)))
        eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS)))
        # gap 闭 + 根 → 终验
        assert verify.verify_calls == [("t1", "t1")]

    def test_root_terminal_pass_finish_graph(self, svc, graph):
        # 全图 DONE 后终验 PASS → graph DONE
        self._setup_running_children(svc, graph, 1)
        eng = _engine(svc, planner=StubPlanner(lambda g: []), verify=StubVerifyPort())
        eng.on_report(_patch("t1", "c0", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS)))
        # 全 DONE → plan[] → 根终验 → 模拟 owner bot 回投 root PASS
        eng.on_report(_patch("t1", "t1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS)))
        assert graph.status == Status.DONE


# ===== on_report FAIL =====
class TestOnReportFail:
    def test_fail_remedy_below_max(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        runner = StubRunner()
        eng = _engine(svc, planner=StubPlanner(lambda g: [_child("c1_remedy")]), runner=runner)
        eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAIL, gaps=["缺x"])))
        # depth=1 < MAX=3 → 补救子挂 c1 下,c1 进 PLANNING
        assert svc._get_node(graph, "c1").status == Status.PLANNING
        assert svc._get_node(graph, "c1_remedy").status == Status.RUNNING
        assert len(runner.run_calls) == 1

    def test_fail_escalate_bbs_at_max(self, svc):
        # MAX_DEPTH=1 → c1 depth=1 ≥MAX → 升 BBS
        g = svc.initialize_graph(_task_info("t2", max_depth=1))
        svc.add_task_nodes([_child("c1", "t2")], parent_node_id="t2")
        svc.update_task_node_info(_patch("t2", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        bbs = StubBbsMarket()
        eng = _engine(svc, planner=StubPlanner(lambda g: []), bbs=bbs)
        eng.on_report(_patch("t2", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAIL, gaps=["缺x"])))
        # 升 BBS:remove_subtree(c1) + loop_round++ + 挂广场
        assert all(n.node_id != "c1" for n in g.tasks)
        assert g.loop_round == 1
        assert bbs.publish_calls == ["t2"]

    def test_fail_escalate_bbs_stuck(self, svc):
        # MAX_DEPTH=1, BBS_MAX_DEPTH=1 → 升 BBS 即 STUCK → graph HUNG
        g = svc.initialize_graph(_task_info("t3", max_depth=1))
        # 覆盖 BBS_MAX_DEPTH=1
        g.extend_props["execution_config"]["BBS_MAX_DEPTH"] = 1
        svc.add_task_nodes([_child("c1", "t3")], parent_node_id="t3")
        svc.update_task_node_info(_patch("t3", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        eng = _engine(svc, planner=StubPlanner(lambda g: []), bbs=StubBbsMarket())
        eng.on_report(_patch("t3", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAIL, gaps=["缺x"])))
        assert g.status == Status.HUNG
        assert g.extend_props.get("hung_reason") == "stuck"


# ===== on_miss =====
class TestOnMiss:
    def test_miss_below_max_split(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        runner = StubRunner()
        eng = _engine(svc, dispatcher=StubDispatcher(), planner=StubPlanner(lambda g: [_child("c1_split")]), runner=runner)
        # 直接 on_miss(c1) → plan 拆细 add c1_split → dispatch HIT → RUNNING
        eng.on_miss(_patch("t1", "c1", extend_props_patch={"miss_events": ["no_bot"]}))
        assert svc._get_node(graph, "c1_split").status == Status.RUNNING
        assert len(runner.run_calls) == 1

    def test_miss_escalate_bbs_at_max(self, svc):
        g = svc.initialize_graph(_task_info("t4", max_depth=1))
        svc.add_task_nodes([_child("c1", "t4")], parent_node_id="t4")
        bbs = StubBbsMarket()
        eng = _engine(svc, planner=StubPlanner(lambda g: []), bbs=bbs)
        # 直接 on_miss(c1) → depth=1 ≥ MAX=1 → 升 BBS
        eng.on_miss(_patch("t4", "c1", extend_props_patch={"miss_events": ["no_bot"]}))
        assert g.loop_round == 1
        assert bbs.publish_calls == ["t4"]


# ===== on_harness =====
class TestOnHarness:
    def test_reset_and_redispatch(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        runner = StubRunner()
        eng = _engine(svc, runner=runner)
        eng.on_harness(_patch("t1", "c1", status=Status.PENDING, extend_props_patch={"crash": "timeout"}))
        # 复位 PENDING → 重投 dispatch → RUNNING
        assert svc._get_node(graph, "c1").status == Status.RUNNING
        assert len(runner.run_calls) == 1


# ===== loop_round 仅升 BBS++ =====
class TestLoopRound:
    def test_normal_remedy_no_increment(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        before = graph.loop_round
        eng = _engine(svc, planner=StubPlanner(lambda g: [_child("c1_remedy")]))
        eng.on_report(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAIL, gaps=["x"])))
        assert graph.loop_round == before  # 正常补救不 ++


# ===== 零 case 知识 =====
class TestZeroCaseKnowledge:
    def test_no_node_name_literals_in_engine(self):
        import agentclaw.community.core.task.task_center.engine as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"engine 出现写死节点名: {hits}"
