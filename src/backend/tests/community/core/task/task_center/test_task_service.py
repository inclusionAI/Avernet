"""M5 TaskService facade 单测(对齐 tasks.md T5.2/T5.3)。

零参 facade + CaseTaskService 子类覆写 _build_engine(CaseEngine 注入 stub 策略池/投递)。
验收 100% 回投(无 verify seam);BBS 投递归 runner(无 bbs market seam);engine 对调用方不可见。
覆盖:2 API 契约 + callback 回投 + harness 接线 + verify/bbs 已删回归(V1/V2)+ 零 case。
"""
from __future__ import annotations

import asyncio
from typing import Callable

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskCallbackData,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_center.task_service import TaskService


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


# ===== adapter:包旧 stub 成 PlanningStrategy / DispatchStrategy =====
class _StubPlanningStrategy:
    rule_id = "stub"
    priority = 5

    def __init__(self, factory: Callable[[object], list[TaskNode]] | None = None):
        self._factory = factory or (lambda g: [])

    def matches(self, graph) -> bool:
        return True

    def apply(self, graph) -> list[TaskNode]:
        return self._factory(graph)


class _StubDispatchStrategy:
    from agentclaw.community.core.task.task_dispatch.strategies import SearchResult, SearchOutcome

    rule_id = "stub"
    priority = 5

    def __init__(self, bot_id="bot1"):
        self.bot_id = bot_id

    def matches(self, node, graph) -> bool:
        return True

    def apply(self, node, graph):
        from agentclaw.community.core.task.task_dispatch.strategies import SearchResult, SearchOutcome
        return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id=self.bot_id)


class StubRunner:
    def __init__(self):
        self.run_calls: list[list[TaskNode]] = []

    async def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        self.run_calls.append(list(toDoTaskList))
        return [True] * len(toDoTaskList)

    async def form_coop_group(self, gf):
        return "grp_stub"

    def query_status(self, task_id): return Status.PENDING
    def query_detail(self, node): return node
    def query_result(self, node): return node
    def query_bot_tasks(self, bot_id): return []


# ===== CaseEngine:覆写 _build_* 注入 stub(T1=A corp 最简形态)=====
class _CaseEngine(ExecutionEngine):
    """测试用编排核:继承 ExecutionEngine 覆写 _build_* 注入 stub 策略/投递(T1=A corp 最简形态)。
    不手动委托 on_*/_dispatch_and_run——直接继承 async 编排逻辑(collect/drain 模式)。"""
    def __init__(self, graph, planner_factory, discover_bot="bot1", runner=None):
        self._case_planner_factory = planner_factory
        self._case_discover_bot = discover_bot
        self._case_runner = runner
        super().__init__(graph)

    def _build_planner(self):
        from agentclaw.community.core.task.task_plan.planner import TaskPlanner
        p = TaskPlanner(self._graph)
        p.set_strategies([_StubPlanningStrategy(self._case_planner_factory)])
        return p

    def _build_dispatcher(self):
        from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
        d = TaskDispatcher(self._graph)
        d.set_strategies([_StubDispatchStrategy(self._case_discover_bot)])
        return d

    def _build_runner(self):
        return self._case_runner or StubRunner()


class _CaseTaskService(TaskService):
    """测试用 facade:覆写 _build_engine 返回 _CaseEngine(注入 stub 策略/投递;模拟 corp)。"""
    def __init__(self, graph, planner_factory=None, discover_bot="bot1", runner=None, harness=None):
        self._case_planner_factory = planner_factory or (lambda g: [])
        self._case_discover_bot = discover_bot
        self._case_runner = runner
        super().__init__(graph, harness=harness)

    def _build_engine(self):
        return _CaseEngine(
            self._graph,
            self._case_planner_factory,
            self._case_discover_bot,
            self._case_runner,
        )


def _build_facade(svc=None, *, decomposer=None, discover=None, runner=None,
                  harness=None, verify=None, bbs=None) -> tuple:
    """兼容旧调用签名(verify/bbs 参数已废弃,忽略);返回 (facade, svc, planner, dispatcher, discover, runner)。"""
    from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
    svc = svc or TaskGraphService()
    factory = None
    if decomposer is not None:
        # decomposer 可能是 _StubPlanningStrategy(有 _factory)或旧 lambda
        f = getattr(decomposer, "_factory", None) or getattr(decomposer, "apply", None)
        if callable(f) and not isinstance(f, type):
            # _StubPlanningStrategy:直接复用其 apply
            def factory(g, _d=decomposer):
                return _d.apply(g)
        elif callable(decomposer):
            factory = decomposer
    facade = _CaseTaskService(
        svc, planner_factory=factory,
        discover_bot=getattr(discover, "bot_id", "bot1") if discover else "bot1",
        runner=runner, harness=harness,
    )
    return facade, svc, None, None, discover, facade._engine._runner


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ===== composition root / protocol =====
class TestProtocolConformance:
    def test_implements_protocol(self):
        facade, *_ = _build_facade()
        assert isinstance(facade, TaskServiceProtocol)


# ===== execute =====
class TestExecute:
    def test_execute_first_frame(self):
        facade, svc, *__, runner = _build_facade(decomposer=lambda g: [_child("c1"), _child("c2")])
        result = _run(facade.execute(_task_info()))
        assert result.task_id == "t1"
        assert result.success is True
        assert result.run_id is not None
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        assert svc._get_node(graph, "c1").status == Status.RUNNING
        assert svc._get_node(graph, "c2").status == Status.RUNNING
        assert len(runner.run_calls) == 1
        assert {n.node_id for n in runner.run_calls[0]} == {"c1", "c2"}

    def test_execute_no_plan_still_returns_result(self):
        facade, svc, *_ = _build_facade(decomposer=lambda g: [])
        result = _run(facade.execute(_task_info()))
        assert result.success is True
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "t1").status == Status.PENDING


# ===== get_task_dashboard =====
class TestGetDashboard:
    def test_returns_full_graph(self):
        facade, svc, *_ = _build_facade()
        _run(facade.execute(_task_info()))
        g = facade.get_task_dashboard("t1")
        assert g.tasks[0].node_id == "t1"

    def test_subtree_projection(self):
        facade, svc, *_ = _build_facade(decomposer=lambda g: [_child("c1")])
        _run(facade.execute(_task_info()))
        sub = facade.get_task_dashboard("t1", "c1")
        assert {n.node_id for n in sub.tasks} == {"c1"}


# ===== callback =====
class TestCallback:
    def test_callback_property_is_tasklopcallback(self):
        facade, *_ = _build_facade()
        from agentclaw.community.core.task.task_runner.callback_adapter import TaskLoopCallback
        assert isinstance(facade.callback, TaskLoopCallback)

    def test_report_result_flips_node_via_callback(self):
        facade, svc, *_ = _build_facade(decomposer=lambda g: [_child("c1")])
        _run(facade.execute(_task_info()))
        _run(facade.callback.report_result(TaskCallbackData(
            loop_task_id="t1::c1", workflow_type="single_bot", workflow_id=1, instance_id=9,
            result={"success": True, "data": "done"},
        )))
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "c1").status == Status.DONE


# ===== harness wiring =====
class TestHarnessWiring:
    def test_execute_registers_with_harness(self):
        from agentclaw.community.core.task.task_harness.harness import TaskHarness
        from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
        svc = TaskGraphService()
        harness = TaskHarness(svc)
        facade = _CaseTaskService(svc, planner_factory=lambda g: [_child("c1")], harness=harness)
        _run(facade.execute(_task_info()))
        assert "t1" in harness._registered
        assert harness._on_harness_fn == facade._engine.on_harness


# ===== V1/V2 回归:验收 100% 回投(无 verify port);BBS 投递归 runner(无 bbs market)=====
class TestAcceptanceViaReport:
    def test_root_terminal_pass_via_report_only(self):
        # V1:根验收不主动触发;全子 DONE + plan[] → 根 PLANNING 等回投 → 回投 root PASS → graph DONE
        from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
        svc = TaskGraphService()
        # decomposer 首批产 c1,c1 DONE 后 plan[]→ 根等回投 → 回投 root PASS → DONE
        facade = _CaseTaskService(svc, planner_factory=lambda g: [_child("c1")])
        _run(facade.execute(_task_info()))
        _run(facade.callback.report_result(TaskCallbackData(
            loop_task_id="t1::c1", workflow_type="single_bot", workflow_id=1, instance_id=1,
            result={"success": True, "data": "x"},
        )))
        # c1 DONE → 根 plan[](无新子)→ 根 PLANNING 等回投
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        # 模拟 owner bot 终验回投 root PASS → graph DONE(无 verify port 注入)
        _run(facade.callback.report_result(TaskCallbackData(
            loop_task_id="t1::t1", workflow_type="single_bot", workflow_id=1, instance_id=2,
            result={"success": True, "data": "root PASS"},
        )))
        graph = svc.query_task_dashboard("t1")
        assert graph.status == Status.DONE


class TestBbsEscalationNoMarket:
    def test_bbs_escalation_marks_bbs_mode_no_market_publish(self):
        # V2:升 BBS 只标 bbs_mode,无 bbs market publish
        from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
        svc = TaskGraphService()
        ti = _task_info("t3")
        ti.execution_config["MAX_DEPTH"] = 1
        facade = _CaseTaskService(svc, planner_factory=lambda g: [_child("c1", "t3")])
        _run(facade.execute(ti))
        _run(facade.callback.report_result(TaskCallbackData(
            loop_task_id="t3::c1", workflow_type="single_bot", workflow_id=1, instance_id=1,
            result={"success": False, "fail_detail": "缺x"},
        )))
        graph = svc.query_task_dashboard("t3")
        assert graph.extend_props.get("bbs_mode") is True
        assert graph.loop_round == 1


class TestZeroCase:
    def test_no_node_name_literals(self):
        import agentclaw.community.core.task.task_center.task_service as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"task_service 出现写死节点名: {hits}"

    def test_no_node_name_literals_harness(self):
        import agentclaw.community.core.task.task_harness.harness as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"harness 出现写死节点名: {hits}"
