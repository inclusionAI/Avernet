"""M5 TaskService facade 单测(对齐 tasks.md T5.2/T5.3)。

组合根装配 + 2 API 契约 + callback 回投 + harness 接线 + verify_port/bbs_market 注入 seam。
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
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


# ===== helpers =====
def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_channel_type="bot",
        source_channel_id="b1",
    )


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec, run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


# ===== in-test stubs (seams) =====
class StubDecomposer:
    def __init__(self, factory: Callable[[object], list[TaskNode]] | None = None):
        self._factory = factory or (lambda g: [])

    def decompose(self, graph) -> list[TaskNode]:
        return self._factory(graph)


class StubDiscover:
    def __init__(self, bot_id="bot1"):
        self.bot_id = bot_id

    def search(self, node: TaskNode):
        from agentclaw.community.core.task.task_dispatch.protocols import SearchResult, SearchOutcome
        return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id=self.bot_id)


class StubRunner:
    def __init__(self):
        self.run_calls: list[list[TaskNode]] = []

    def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        self.run_calls.append(list(toDoTaskList))
        return [True] * len(toDoTaskList)

    def form_coop_group(self, gf):
        return "grp_stub"

    def query_status(self, task_id): return Status.PENDING
    def query_detail(self, node): return node
    def query_result(self, node): return node
    def query_bot_tasks(self, bot_id): return []


class StubVerify:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def request_verify(self, task_id, node_id):
        self.calls.append((task_id, node_id))


class StubBbs:
    def __init__(self):
        self.calls: list[str] = []

    def publish_task(self, task_id):
        self.calls.append(task_id)


def _build_facade(svc=None, *, decomposer=None, discover=None, runner=None,
                  harness=None, verify=None, bbs=None) -> tuple[TaskService, TaskGraphService, object, object, object, StubRunner]:
    svc = svc or TaskGraphService()
    from agentclaw.community.core.task.task_plan.planner import TaskPlanner
    from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
    runner = runner or StubRunner()
    planner = TaskPlanner(StubDecomposer() if decomposer is None else decomposer)
    dispatcher = TaskDispatcher(StubDiscover() if discover is None else discover, runner)
    facade = TaskService(svc, planner, dispatcher, runner, harness=harness,
                          verify_port=verify, bbs_market=bbs)
    return facade, svc, planner, dispatcher, discover, runner


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
        decomposer = StubDecomposer(lambda g: [_child("c1"), _child("c2")])
        facade, svc, *__, runner = _build_facade(decomposer=decomposer)
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
        facade, svc, *_ = _build_facade(decomposer=StubDecomposer(lambda g: []))
        result = _run(facade.execute(_task_info()))
        assert result.success is True
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "t1").status == Status.PENDING  # 无可规划


# ===== get_task_dashboard =====
class TestGetDashboard:
    def test_returns_full_graph(self):
        facade, svc, *_ = _build_facade()
        _run(facade.execute(_task_info()))
        g = facade.get_task_dashboard("t1")
        assert g.tasks[0].node_id == "t1"

    def test_subtree_projection(self):
        decomposer = StubDecomposer(lambda g: [_child("c1")])
        facade, svc, *_ = _build_facade(decomposer=decomposer)
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
        decomposer = StubDecomposer(lambda g: [_child("c1")])
        facade, svc, *_ = _build_facade(decomposer=decomposer)
        _run(facade.execute(_task_info()))
        # c1 RUNNING → 回投 PASS
        facade.callback.report_result(TaskCallbackData(
            loop_task_id="t1::c1", workflow_type="single_bot", workflow_id=1, instance_id=9,
            result={"success": True, "data": "done"},
        ))
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "c1").status == Status.DONE


# ===== harness wiring =====
class TestHarnessWiring:
    def test_execute_registers_with_harness(self):
        from agentclaw.community.core.task.task_harness.harness import TaskHarness
        svc = TaskGraphService()
        runner = StubRunner()
        from agentclaw.community.core.task.task_plan.planner import TaskPlanner
        from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
        harness = TaskHarness(svc)
        planner = TaskPlanner(StubDecomposer(lambda g: [_child("c1")]))
        dispatcher = TaskDispatcher(StubDiscover(), runner)
        facade = TaskService(svc, planner, dispatcher, runner, harness=harness)
        _run(facade.execute(_task_info()))
        assert "t1" in harness._registered
        # on_harness_fn 已回填为编排核 on_harness
        assert harness._on_harness_fn == facade._engine.on_harness


# ===== verify_port / bbs_market seams =====
class TestSeams:
    def test_default_verify_is_noop(self):
        # 根终验条件:全非根 DONE + plan[] → 请求终验。no-op verify 不报错。
        decomposer = StubDecomposer(lambda g: [])
        facade, svc, *_ = _build_facade(decomposer=decomposer)
        _run(facade.execute(_task_info()))
        # 根 PENDING,无子,plan[]→ 无终验触发(根仍 PENDING,因 plan无可规划 target 返[]不调终验)
        # 实际:on_execute 条件a→plan[](返空)→不 add→不终验。根仍 PENDING。
        graph = svc.query_task_dashboard("t1")
        assert svc._get_node(graph, "t1").status == Status.PENDING

    def test_injected_verify_called_on_root_terminal(self):
        # 构造:根下 c1 DONE + plan(root)==[] → 根终验触发 injected verify
        decomposer = StubDecomposer(lambda g: [])
        verify = StubVerify()
        facade, svc, *_ = _build_facade(decomposer=decomposer, verify=verify)
        _run(facade.execute(_task_info()))
        # 手动 add c1 并标 DONE,再回投根 PASS 模拟 — 但终验触发需经 on_pass 上行。
        # 直接经 facade 回投 c1 PASS(c1 需 RUNNING):先 add c1 让 plan 产它。
        # 简化:用第二个 decomposer 产 c1,回投 c1 PASS → 根 gap 闭 → 终验。
        svc2 = TaskGraphService()
        runner = StubRunner()
        from agentclaw.community.core.task.task_plan.planner import TaskPlanner
        from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
        dec1 = StubDecomposer(lambda g: [_child("c1", "t2")])
        planner = TaskPlanner(dec1)
        dispatcher = TaskDispatcher(StubDiscover(), runner)
        facade2 = TaskService(svc2, planner, dispatcher, runner, verify_port=verify)
        _run(facade2.execute(_task_info("t2")))
        facade2.callback.report_result(TaskCallbackData(
            loop_task_id="t2::c1", workflow_type="single_bot", workflow_id=1, instance_id=1,
            result={"success": True, "data": "x"},
        ))
        # c1 DONE → 根 plan[](decompose 返[]) → 根终验 → verify 调
        assert ("t2", "t2") in verify.calls

    def test_injected_bbs_market_called_on_escalation(self):
        # MAX_DEPTH=1 → c1 FAIL+gaps → depth≥MAX → 升 BBS → injected bbs.publish
        svc = TaskGraphService()
        runner = StubRunner()
        from agentclaw.community.core.task.task_plan.planner import TaskPlanner
        from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
        ti = _task_info("t3")
        ti.execution_config["MAX_DEPTH"] = 1
        bbs = StubBbs()
        planner = TaskPlanner(StubDecomposer(lambda g: [_child("c1", "t3")]))
        dispatcher = TaskDispatcher(StubDiscover(), runner)
        facade = TaskService(svc, planner, dispatcher, runner, bbs_market=bbs)
        _run(facade.execute(ti))
        facade.callback.report_result(TaskCallbackData(
            loop_task_id="t3::c1", workflow_type="single_bot", workflow_id=1, instance_id=1,
            result={"success": False, "fail_detail": "缺x"},
        ))
        assert "t3" in bbs.calls


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
