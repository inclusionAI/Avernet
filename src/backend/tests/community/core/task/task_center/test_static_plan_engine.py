import asyncio

from agentclaw.community.core.task.domain.models import (
    Context, Goal, Metadata, RuntimeInfo, Status, TaskExecutionGraph, TaskInfo,
    TaskNodePatch, TaskSpec, AcceptanceResult, AcceptanceVerdict,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
from agentclaw.community.core.task.task_dispatch.strategies import DirectDispatchStrategy
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


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
