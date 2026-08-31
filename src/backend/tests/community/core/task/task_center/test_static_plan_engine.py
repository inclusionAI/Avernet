import asyncio

from agentclaw.community.core.task.domain.models import (
    Context, Goal, Metadata, RuntimeInfo, Status, TaskExecutionGraph, TaskInfo,
    TaskNodePatch, TaskSpec, AcceptanceResult, AcceptanceVerdict,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
from agentclaw.community.core.task.task_dispatch.strategies import DirectDispatchStrategy
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


PLAN = """
template_id: demo
entry_bot_id: entry-bot
nodes:
  - id: risk
    type: collaboration
    collaboration: {bot_ids: [risk-a, risk-b]}
    input: {okr: $.input.okr}
    output: {risk_result: $.report.result}
  - id: strategy
    type: bot
    bot_id: strategy-bot
    input: {okr: $.input.okr}
    output: {strategy_result: $.result}
  - id: approval
    type: bot
    bot_id: approval-bot
    depends_on: [risk, strategy]
    input:
      risk_result: $.risk.output.risk_result
      strategy_result: $.strategy.output.strategy_result
    output: {approved: $.result.approved}
  - id: implementation
    type: bot
    bot_id: implementation-bot
    depends_on: [approval]
    enabled_when: {expression: $.approval.output.approved == true}
    input: {approval: $.approval.output}
    output: {implementation_result: $.result}
"""


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Runner:
    def __init__(self):
        self.started = []
        self.groups = []

    async def start_run(self, nodes):
        self.started.append([n.node_id for n in nodes])
        return [True] * len(nodes)

    async def form_coop_group(self, group):
        self.groups.append(group)
        return f"group-{len(self.groups)}"


def _engine(graph, runner):
    class StaticEngine(ExecutionEngine):
        def _build_dispatcher(self):
            return TaskDispatcher(self._graph, pool=[DirectDispatchStrategy()])

        def _build_runner(self):
            return runner

    return StaticEngine(graph)


def _task_info():
    return TaskInfo(
        task_spec=TaskSpec(
            Metadata("t1", "OKR", "implement"),
            Context("", {"template_input": {"okr": "increase conversion"}}),
            Goal("okr-implementation", []),
        ),
        source_type="api",
        owner_bot_id="entry-bot",
        execution_config={"task_type": "static_plan", "static_plan_yaml": PLAN,
                          "template_input": {"okr": "increase conversion"}},
    )


def _report(engine, node_id, result):
    return _run(engine.on_report(TaskNodePatch(
        task_id="t1", node_id=node_id,
        output_patch={"result": result},
        acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE),
    )))


def test_static_plan_dispatches_parallel_branches_then_joins_and_continues():
    graph_service = TaskGraphService()
    graph = graph_service.initialize_graph(_task_info())
    runner = _Runner()
    engine = _engine(graph_service, runner)

    _run(engine.on_execute("t1"))

    assert {n.node_id for n in graph.tasks if n.status == Status.RUNNING} == {"risk", "strategy"}
    assert runner.started == [["risk", "strategy"]]
    assert [group.bot_ids for group in runner.groups] == [["risk-a", "risk-b"]]

    _report(engine, "risk", {"risk": "low"})
    assert not any(n.node_id == "approval" and n.status == Status.RUNNING for n in graph.tasks)
    _report(engine, "strategy", {"strategy": "ready"})

    approval = next(n for n in graph.tasks if n.node_id == "approval")
    assert approval.status == Status.RUNNING
    assert approval.task_spec.context.extend_props["static_input"] == {
        "risk_result": {"risk": "low"}, "strategy_result": {"strategy": "ready"}
    }

    _report(engine, "approval", {"approved": True})
    implementation = next(n for n in graph.tasks if n.node_id == "implementation")
    assert implementation.status == Status.RUNNING
    assert runner.started[-1] == ["implementation"]


def test_static_plan_rejects_implementation_when_approval_is_false():
    graph_service = TaskGraphService()
    graph = graph_service.initialize_graph(_task_info())
    runner = _Runner()
    engine = _engine(graph_service, runner)

    _run(engine.on_execute("t1"))
    _report(engine, "risk", {"risk": "high"})
    _report(engine, "strategy", {"strategy": "needs-review"})
    _report(engine, "approval", {"approved": False})

    implementation = next(n for n in graph.tasks if n.node_id == "implementation")
    assert implementation.status == Status.DONE
    assert implementation.run_info.output["skipped"] is True
    assert "implementation" not in runner.started[-1:]


def test_static_plan_default_real_report_with_fallback_timeout(monkeypatch):
    # 固定流程默认 = 真实上报(_static_auto_report_on False),并带 fallback 超时兜底(默认 80s),
    # 避免单节点不上报致整流程卡死;超时可由 execution_config.static_fallback_timeout / env 覆盖。
    monkeypatch.delenv("OCB_TASK_STATIC_FALLBACK_TIMEOUT", raising=False)
    monkeypatch.delenv("OCB_TASK_STATIC_AUTO_REPORT", raising=False)
    monkeypatch.delenv("OCB_TASK_STATIC_AUTO_REPORT_DELAY", raising=False)

    graph_service = TaskGraphService()
    graph_service.initialize_graph(_task_info())
    engine = _engine(graph_service, _Runner())

    # 默认真实上报模式
    assert engine._static_auto_report_on("t1") is False
    # fallback 兜底超时默认 80s
    assert engine._static_fallback_delay("t1") == 80.0

    # env OCB_TASK_STATIC_FALLBACK_TIMEOUT 覆盖
    monkeypatch.setenv("OCB_TASK_STATIC_FALLBACK_TIMEOUT", "30")
    assert engine._static_fallback_delay("t1") == 30.0
    monkeypatch.delenv("OCB_TASK_STATIC_FALLBACK_TIMEOUT")

    # execution_config.static_fallback_timeout 覆盖(env 缺省)
    gs2 = TaskGraphService()
    ti2 = TaskInfo(
        task_spec=TaskSpec(
            Metadata("t2", "OKR", "implement"),
            Context("", {"template_input": {"okr": "x"}}),
            Goal("okr-implementation", []),
        ),
        source_type="api", owner_bot_id="entry-bot",
        execution_config={"task_type": "static_plan", "static_plan_yaml": PLAN,
                          "template_input": {"okr": "x"}, "static_fallback_timeout": 15},
    )
    gs2.initialize_graph(ti2)
    assert _engine(gs2, _Runner())._static_fallback_delay("t2") == 15.0


def test_static_plan_invalid_fallback_timeout_uses_default(monkeypatch):
    monkeypatch.delenv("OCB_TASK_STATIC_FALLBACK_TIMEOUT", raising=False)

    graph_service = TaskGraphService()
    task_info = _task_info()
    task_info.execution_config["static_fallback_timeout"] = "not-a-number"
    graph_service.initialize_graph(task_info)

    assert _engine(graph_service, _Runner())._static_fallback_delay("t1") == 80.0


def test_static_plan_status_filter_ignores_empty_tokens():
    from agentclaw.community.core.task.task_center.task_service_support import (
        parse_status_filter,
    )

    assert parse_status_filter(", ,") is None
