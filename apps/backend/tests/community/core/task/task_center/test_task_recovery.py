from __future__ import annotations

import asyncio

from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    NodeAction,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskNodePatch,
    TaskReportCommand,
    TaskReportEventType,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.task_recovery import (
    CentralizedRecoveryStrategy,
    RelayRecoveryStrategy,
    TaskRecoveryCoordinator,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


def _run(coro):
    return asyncio.run(coro)


def _service() -> TaskGraphService:
    graph = TaskExecutionGraph(
        task_id="task-1",
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        extend_props={
            "execution_config": {
                "orchestration_mode": "relay",
                "MAX_HARNESS": 2,
            },
            "runtime_profile": {
                "recovery_strategy": "relay",
                "allowed_run_modes": ["single_bot", "bbs"],
            },
        },
    )
    root = TaskNode(
        node_id="task-1",
        task_id="task-1",
        status=Status.RUNNING,
        task_spec=TaskSpec(Context(title="root"), Goal("finish", [])),
        run_info=RuntimeInfo(
            run_mode="single_bot",
            assignee="bot-1",
            extend_props={"relay_token": "attempt-1"},
        ),
        node_run_graph=graph,
    )
    graph.tasks.append(root)
    service = TaskGraphService()
    service._graphs[graph.task_id] = graph
    return service


class _Intents:
    def __init__(self) -> None:
        self.requeued = []

    def requeue_delivery_intent(self, task_id, node_id, token, *, reason):
        self.requeued.append((task_id, node_id, token, reason))


class _Dispatch:
    def __init__(self) -> None:
        self.calls = []

    async def __call__(self, task_id, node_id):
        self.calls.append((task_id, node_id))


def _execution(token="attempt-1") -> TaskReportCommand:
    return TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="execution-1",
        relay_token=token,
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=TaskNodePatch(
            task_id="task-1",
            node_id="task-1",
            execution_decision="ACCEPTED",
            actual_goal=Goal("part", []),
            output_patch={"result": "evidence"},
            local_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
        ),
    )


def test_relay_recovery_starts_new_attempt_before_execution_is_accepted():
    graph = _service()
    intents = _Intents()
    dispatch = _Dispatch()
    strategy = RelayRecoveryStrategy(graph, intents, dispatch)

    _run(strategy.recover(TaskNodePatch(
        task_id="task-1", node_id="task-1", exec_error="timeout",
    )))

    node = graph.query_task_dashboard("task-1").tasks[0]
    assert node.status is Status.PENDING
    assert node.run_info.extend_props["harness_retries"] == 1
    assert node.run_info.extend_props["relay_token"] != "attempt-1"
    assert dispatch.calls == [("task-1", "task-1")]
    reset = node.run_info.action_log[-1]
    assert reset.action is NodeAction.RESET
    assert reset.payload["recovery_phase"] == "AWAITING_EXECUTION"
    assert reset.payload["token_rotated"] is True


def test_relay_recovery_resumes_plan_without_clearing_accepted_execution():
    graph = _service()
    graph.apply_relay_report(_execution())
    intents = _Intents()
    dispatch = _Dispatch()
    strategy = RelayRecoveryStrategy(graph, intents, dispatch)

    _run(strategy.recover(TaskNodePatch(
        task_id="task-1", node_id="task-1", exec_error="timeout",
    )))

    node = graph.query_task_dashboard("task-1").tasks[0]
    assert node.status is Status.RUNNING
    assert node.run_info.output == {"result": "evidence"}
    assert node.run_info.extend_props["relay_token"] == "attempt-1"
    assert node.run_info.extend_props["relay_resume_retries"] == 1
    assert intents.requeued[0][:3] == (
        "task-1",
        "task-1",
        "attempt-1",
    )
    assert dispatch.calls == [("task-1", "task-1")]
    reset = node.run_info.action_log[-1]
    assert reset.action is NodeAction.RESET
    assert reset.payload["recovery_phase"] == "AWAITING_PLAN"
    assert reset.payload["resume_from"] == "accepted_execution"


def test_relay_recovery_dispatches_the_accepted_plan_target():
    graph = _service()
    graph.apply_relay_report(_execution())
    plan = TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="plan-1",
        relay_token="attempt-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=TaskNode(
            node_id=None,
            task_id="task-1",
            status=None,
            task_spec=TaskSpec(Context(title="next"), Goal("next", [])),
            run_info=RuntimeInfo(run_mode="single_bot", assignee="bot-2"),
            node_run_graph=None,
        ),
    )
    target = graph.apply_relay_report(plan).target_node_id
    dispatch = _Dispatch()
    strategy = RelayRecoveryStrategy(graph, _Intents(), dispatch)

    _run(strategy.recover(TaskNodePatch(
        task_id="task-1", node_id="task-1", exec_error="timeout",
    )))

    assert dispatch.calls == [("task-1", target)]
    origin = graph.query_task_dashboard("task-1").tasks[0]
    assert origin.run_info.action_log[-1].payload["recovery_phase"] == "AWAITING_DISPATCH"


def test_relay_bbs_recovery_requeues_publish_after_execution_was_accepted():
    graph = _service()
    node = graph.query_task_dashboard("task-1").tasks[0]
    node.run_info.run_mode = "bbs"
    node.run_info.assignee = "bot-1"
    graph.apply_relay_report(_execution())
    intents = _Intents()
    dispatch = _Dispatch()

    _run(RelayRecoveryStrategy(graph, intents, dispatch).recover(TaskNodePatch(
        task_id="task-1",
        node_id="task-1",
        exec_error="planning_timeout",
    )))

    current = graph.query_task_dashboard("task-1").tasks[0]
    assert current.status is Status.PENDING
    assert not current.run_info.assignee
    assert intents.requeued[0][:3] == (
        "task-1",
        "task-1",
        current.run_info.extend_props["relay_token"],
    )
    assert dispatch.calls == [("task-1", "task-1")]


def test_coordinator_keeps_centralized_recovery_strategy_separate():
    graph = _service()
    graph.query_task_dashboard("task-1").extend_props["runtime_profile"] = {
        "recovery_strategy": "centralized",
    }
    calls = []

    async def central(patch):
        calls.append(patch.node_id)

    coordinator = TaskRecoveryCoordinator(
        graph,
        {"centralized": CentralizedRecoveryStrategy(central)},
    )
    _run(coordinator.recover(TaskNodePatch(
        task_id="task-1", node_id="task-1", exec_error="timeout",
    )))
    assert calls == ["task-1"]
