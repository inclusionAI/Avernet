from agentclaw.community.core.task.domain.models import (
    Context, Goal, Metadata, RuntimeInfo, Status, TaskExecutionGraph, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_plan.static_plan import StaticPlanDefinition
from agentclaw.community.core.task.task_plan.static_plan_runtime import StaticPlanRuntime


def _graph():
    graph = TaskExecutionGraph(run_id=1, loop_round=0, status=Status.RUNNING, task_id="t1")
    root = TaskNode(
        node_id="t1", task_id="t1", status=Status.PLANNING,
        task_spec=TaskSpec(Metadata("t1", "root", "root"), Context(""), Goal("root", [])),
        run_info=RuntimeInfo(), node_run_graph=graph,
    )
    graph.tasks.append(root)
    return graph


def _runtime():
    plan = StaticPlanDefinition.from_yaml(
        """
        template_id: demo
        input_schema:
          okr: {type: string, required: true}
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
        """
    )
    return StaticPlanRuntime(plan, {"okr": "increase conversion"})


def test_runtime_marks_two_root_nodes_ready_together_and_resolves_input():
    graph = _graph()
    runtime = _runtime()
    graph.tasks.extend(runtime.nodes("t1", graph.tasks[0].task_spec))

    readiness = runtime.ready(graph)

    assert {node.node_id for node in readiness.ready} == {"risk", "strategy"}
    assert readiness.skipped == ()
    assert {node.task_spec.context.extend_props["static_input"]["okr"] for node in readiness.ready} == {"increase conversion"}
    assert readiness.ready[0].run_info.extend_props["pending_group_formation"].bot_ids == ["risk-a", "risk-b"]


def test_runtime_join_is_not_ready_until_both_predecessors_done():
    graph = _graph()
    runtime = _runtime()
    graph.tasks.extend(runtime.nodes("t1", graph.tasks[0].task_spec))

    first = runtime.ready(graph)
    for node in first.ready:
        node.status = Status.SUCCESS
        if node.node_id == "risk":
            node.run_info.output["risk_result"] = "risk"
        else:
            node.run_info.output["strategy_result"] = "strategy"

    second = runtime.ready(graph)
    assert [node.node_id for node in second.ready] == ["approval"]
    assert second.ready[0].task_spec.context.extend_props["static_input"] == {
        "risk_result": "risk", "strategy_result": "strategy"
    }


def test_runtime_propagates_owner_user_id_to_static_children():
    """static 子节点应从 graph.extend_props 透传 owner_user_id → assignee_owner_id。

    DirectDispatchStrategy 对 static 只设 static_bot_id、不设 owner_id;_dispatch_single_bot
    经 compose_bot_identity 需 owner_id 才能拼出 BaaS 接受的复合 bot_id:owner。不透传 → 裸 bot_id
    → 公网 BaaS 拒 → start_run_failed。owner_id 取自 graph(提交请求注入),与 root 一致。
    """
    graph = _graph()
    graph.extend_props["owner_user_id"] = "owner-1"
    runtime = _runtime()
    graph.tasks.extend(runtime.nodes("t1", graph.tasks[0].task_spec))

    readiness = runtime.ready(graph)

    assert readiness.ready  # risk(collab) + strategy(bot) 两根
    for node in readiness.ready:
        assert node.run_info.extend_props["assignee_owner_id"] == "owner-1"


def test_runtime_omits_assignee_owner_id_when_graph_has_no_owner():
    """无 owner_user_id 时不写 assignee_owner_id(不造假,保持与未配置/内网宽松后端一致)。"""
    graph = _graph()  # 不设 owner_user_id
    runtime = _runtime()
    graph.tasks.extend(runtime.nodes("t1", graph.tasks[0].task_spec))

    readiness = runtime.ready(graph)

    for node in readiness.ready:
        assert "assignee_owner_id" not in node.run_info.extend_props
