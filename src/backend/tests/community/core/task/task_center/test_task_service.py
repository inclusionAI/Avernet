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
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    PlanResult,
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
        source_type="bot",
        owner_bot_id="b1",
        execution_config={"MAX_DEPTH": max_depth, "BBS_MAX_DEPTH": 3},
    )


def _task_info_request(task_id: str = "t1", max_depth: int = 3):
    """TaskInfoRequest for execute (task_id is supplied by the provider, not the request)."""
    from agentclaw.community.core.task.domain.models import TaskSourceType
    from agentclaw.community.core.task.domain.requests import (
        RequestAcceptance, RequestContext, RequestGoal, RequestMetadata,
        RequestTaskSpec, TaskInfoRequest,
    )
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="T", instruction="do"),
            context=RequestContext(background="bg"),
            goal=RequestGoal(objective="o", acceptances=[RequestAcceptance(id="ac1", acceptance="d")]),
        ),
        source_type=TaskSourceType.BOT,
        owner_user_id="u1",
        owner_bot_id="b1",
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

    def __init__(self, factory: Callable[[object], list[TaskNode]] | None = None,
                 has_gap_when_empty: bool = False):
        self._factory = factory or (lambda g: [])
        self._has_gap_when_empty = has_gap_when_empty

    async def matches(self, graph) -> bool:
        return True

    async def apply(self, graph, target) -> PlanResult:
        kids = self._factory(graph)
        return PlanResult(children=kids, has_gap=bool(kids) or self._has_gap_when_empty)


class _StubDispatchStrategy:
    from agentclaw.community.core.task.task_dispatch.strategies import SearchResult, SearchOutcome

    rule_id = "stub"
    priority = 5

    def __init__(self, bot_id="bot1"):
        self.bot_id = bot_id

    async def matches(self, node, graph) -> bool:
        return True

    async def apply(self, node, graph):
        from agentclaw.community.core.task.task_dispatch.strategies import SearchResult, SearchOutcome
        return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id=self.bot_id)


class StubRunner:
    def __init__(self):
        self.run_calls: list[list[TaskNode]] = []
        self.bbs_calls: list = []   # engine 升 BBS 可恢复态时经 start_run 派发的 task_id

    async def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        self.run_calls.append(list(toDoTaskList))
        self.bbs_calls.extend(
            node.task_id
            for node in toDoTaskList
            if node.run_info.run_mode == "bbs"
        )
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
    def __init__(self, graph, planner_factory=None, discover_bot="bot1", runner=None, harness=None,
                 task_id_provider=None):
        self._case_planner_factory = planner_factory or (lambda g: [])
        self._case_discover_bot = discover_bot
        self._case_runner = runner
        super().__init__(graph, harness=harness, task_id_provider=task_id_provider)

    def _build_engine(self, *, bot=None, bcs=None, discover=None) -> ExecutionEngine:
        # case 测试覆写:注入 stub 策略/投递的 _CaseEngine(忽略传入端口)
        return _CaseEngine(
            self._graph,
            self._case_planner_factory,
            self._case_discover_bot,
            self._case_runner,
        )


def _build_facade(svc=None, *, decomposer=None, discover=None, runner=None,
                  harness=None, verify=None, bbs=None, task_id_provider=None) -> tuple:
    """兼容旧调用签名(verify/bbs 参数已废弃,忽略);返回 (facade, svc, planner, dispatcher, discover, runner)。"""
    from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
    svc = svc or TaskGraphService()
    factory = None
    if decomposer is not None:
        # decomposer 为 lambda g: [...] 时直接当 factory(_StubPlanningStrategy 内部包成 PlanResult)
        if callable(decomposer) and not hasattr(decomposer, "apply"):
            factory = decomposer
        elif hasattr(decomposer, "_factory"):
            factory = decomposer._factory
    facade = _CaseTaskService(
        svc, planner_factory=factory,
        discover_bot=getattr(discover, "bot_id", "bot1") if discover else "bot1",
        runner=runner, harness=harness,
        task_id_provider=task_id_provider or (lambda: "t1"),
    )
    return facade, svc, None, None, discover, facade._engine._runner


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)

def _exec(facade, request):
    """execute(fire-and-forget)→drain_background 等首帧落定(测试确定性 seam)。"""
    async def _go():
        r = await facade.execute(request)
        await facade.drain_background()
        return r
    return _run(_go())

# ===== composition root / protocol =====
class TestProtocolConformance:
    def test_implements_protocol(self):
        facade, *_ = _build_facade()
        assert isinstance(facade, TaskServiceProtocol)


# ===== execute =====
class TestExecute:
    def test_execute_first_frame(self):
        facade, svc, *__, runner = _build_facade(decomposer=lambda g: [_child("c1"), _child("c2")])
        result = _exec(facade, _task_info_request())
        assert result.task_id == "t1"
        assert result.success is True
        assert result.run_id is not None
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "t1").status == Status.PLANNING  # v4:父委托态
        assert svc._get_node(graph, "c1").status == Status.RUNNING
        assert svc._get_node(graph, "c2").status == Status.RUNNING
        assert len(runner.run_calls) == 1
        assert {n.node_id for n in runner.run_calls[0]} == {"c1", "c2"}

    def test_execute_no_plan_gap_closed_finishes(self):
        # Step2:plan[]+has_gap=F = 根 gap 闭(终验通过)→ 翻根 DONE + 图 DONE
        facade, svc, *_ = _build_facade(decomposer=lambda g: [])
        result = _exec(facade, _task_info_request())
        assert result.success is True
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "t1").status == Status.SUCCESS
        assert graph.status == Status.SUCCESS


# ===== get_task_dashboard =====
class TestGetDashboard:
    def test_returns_full_graph(self):
        facade, svc, *_ = _build_facade()
        _exec(facade, _task_info_request())
        g = facade.get_task_dashboard("t1")
        assert g.tasks[0].node_id == "t1"

    def test_subtree_projection(self):
        facade, svc, *_ = _build_facade(decomposer=lambda g: [_child("c1")])
        _exec(facade, _task_info_request())
        sub = facade.get_task_dashboard("t1", "c1")
        assert {n.node_id for n in sub.tasks} == {"c1"}

    def test_root_dashboard_runtime_backfills_missing_identity(self):
        from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService

        graph_service = TaskGraphService()
        facade, _, *_ = _build_facade(svc=graph_service)
        info = _task_info("t-dashboard")
        info.owner_bot_id = "owner-bot"
        info.owner_user_id = "owner-user"
        info.execution_config["main_session_id"] = "main-session"
        graph_service.initialize_graph(info)

        dashboard = facade.get_task_dashboard("t-dashboard")
        root = dashboard.tasks[0]

        assert root.run_info.run_mode == "single_bot"
        assert root.run_info.assignee == "owner-bot"
        assert root.run_info.extend_props["session_id"] == "main-session"
        assert root.run_info.extend_props["assignee_owner_id"] == "owner-user"
        assert root.run_info.end_time is None


# ===== callback =====
class TestCallback:
    def test_callback_property_is_tasklopcallback(self):
        facade, *_ = _build_facade()
        from agentclaw.community.core.task.task_runner.callback_adapter import TaskLoopCallback
        assert isinstance(facade.callback, TaskLoopCallback)

    def test_report_result_flips_node_via_callback(self):
        facade, svc, *_ = _build_facade(decomposer=lambda g: [_child("c1")])
        _exec(facade, _task_info_request())
        _run(facade.callback.report_result(TaskCallbackData(data={
            "loop_task_id": "t1::c1", "workflow_type": "single_bot", "workflow_id": 1, "instance_id": 9,
            "result": {"success": True, "data": "done"},
        })))
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "c1").status == Status.SUCCESS


# ===== harness wiring =====
class TestHarnessWiring:
    def test_execute_registers_with_harness(self):
        from agentclaw.community.core.task.task_harness.harness import TaskHarness
        from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
        svc = TaskGraphService()
        harness = TaskHarness(svc)
        facade = _CaseTaskService(svc, planner_factory=lambda g: [_child("c1")], harness=harness,
                              task_id_provider=lambda: "t1")
        _exec(facade, _task_info_request())
        assert "t1" in harness._registered
        assert harness._on_harness_fn == facade._engine.on_harness

    def test_dashboard_registers_task_for_harness_on_this_worker(self):
        from agentclaw.community.core.task.task_harness.harness import TaskHarness
        from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService

        svc = TaskGraphService()
        harness = TaskHarness(svc)
        facade = _CaseTaskService(
            svc, planner_factory=lambda g: [_child("c1")], harness=harness,
            task_id_provider=lambda: "t1",
        )
        _exec(facade, _task_info_request())
        harness._registered.clear()  # simulate a dashboard request on another worker

        facade.get_task_dashboard("t1")

        assert "t1" in harness._registered


# ===== V1/V2 回归:验收 100% 回投(无 verify port);BBS 投递归 runner(无 bbs market)=====
class TestAcceptanceViaReport:
    def test_root_terminal_pass_via_gap_closed(self):
        # 语义A:全子 DONE + plan[]→ gap 闭=终验通过 → 翻根 DONE + graph DONE(无需 owner bot 回投)
        from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
        svc = TaskGraphService()
        # decomposer 首批产 c1,c1 DONE 后 plan[]→ gap 闭=终验通过 → 翻根 DONE
        facade = _CaseTaskService(svc, planner_factory=lambda g: [_child("c1")],
                              task_id_provider=lambda: "t1")
        _exec(facade, _task_info_request())
        _run(facade.callback.report_result(TaskCallbackData(data={
            "loop_task_id": "t1::c1", "workflow_type": "single_bot", "workflow_id": 1, "instance_id": 1,
            "result": {"success": True, "data": "x"},
        })))
        # c1 DONE → 根 plan[](无新子,去重空)→ gap 闭=终验通过 → 翻根 DONE + graph DONE
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "t1").status == Status.SUCCESS
        assert graph.status == Status.SUCCESS


class TestBbsEscalationNoMarket:
    def test_bbs_escalation_marks_bbs_mode_no_market_publish(self):
        # V2:升 BBS 只标 bbs_mode,无 bbs market publish
        from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
        svc = TaskGraphService()
        facade = _CaseTaskService(svc, planner_factory=lambda g: [_child("c1", "t3")],
                                  task_id_provider=lambda: "t3")
        _exec(facade, _task_info_request("t3", max_depth=1))
        _run(facade.callback.report_result(TaskCallbackData(data={
            "loop_task_id": "t3::c1", "workflow_type": "single_bot", "workflow_id": 1, "instance_id": 1,
            "result": {"success": False, "fail_detail": "缺x"},
        })))
        graph = svc.query_task_dashboard("t3")
        # 动态任务验收 FAIL→节点 HUNG，并进入 BBS 恢复态。
        _c1 = svc._get_node(graph, "c1")
        assert _c1.status == Status.HUNG
        assert _c1.run_info.acceptance_result is not None
        assert _c1.run_info.acceptance_result.verdict == AcceptanceVerdict.FAILED  # verdict 不改


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
