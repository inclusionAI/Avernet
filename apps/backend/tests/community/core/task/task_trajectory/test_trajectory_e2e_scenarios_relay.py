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
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    TaskTrajectory,
    TrajectoryActionType,
)
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
)
from agentclaw.community.core.task.task_center.task_recovery import RelayRecoveryStrategy


class _Assembler:
    def assemble(self, task_id):
        return TaskTrajectory(task_id=task_id, gmt_create=0, gmt_modified=0)


class _Repo:
    def list_events_by_task(self, task_id):
        return []


class _Analyzer:
    async def analyze(self, *args, **kwargs):  # pragma: no cover - pure read only
        raise AssertionError("analysis must not run")


def _graph() -> TaskGraphService:
    graph = TaskExecutionGraph(
        task_id="task-1",
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        extend_props={
            "execution_config": {"orchestration_mode": "relay", "MAX_LOOP": 3},
            "runtime_profile": {
                "dispatcher_strategy": "relay_handoff",
                "recovery_strategy": "relay",
                "allowed_run_modes": ["single_bot"],
            },
        },
    )
    graph.tasks.append(TaskNode(
        node_id="task-1",
        task_id="task-1",
        status=Status.RUNNING,
        task_spec=TaskSpec(Context(title="root"), Goal("finish", [])),
        run_info=RuntimeInfo(
            run_mode="single_bot",
            assignee="bot-1",
            extend_props={"relay_token": "generation-1"},
        ),
        node_run_graph=graph,
    ))
    service = TaskGraphService()
    service._graphs[graph.task_id] = graph
    return service


def test_new_relay_protocol_projects_complete_baton_timeline() -> None:
    graph = _graph()
    graph.apply_relay_report(TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="execution-1",
        relay_token="generation-1",
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=TaskNodePatch(
            task_id="task-1",
            node_id="task-1",
            execution_decision="ACCEPTED",
            actual_goal=Goal("inspect", []),
            output_patch={"result": "evidence"},
            local_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
        ),
    ))
    graph.append_action_event(
        "task-1",
        "task-1",
        NodeAction.DISPATCH,
        {
            "phase": "search",
            "action_result": "search",
            "query": "incident response",
            "candidate_count": 1,
        },
    )
    report = graph.apply_relay_report(TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="plan-1",
        relay_token="generation-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=TaskNode(
            node_id=None,
            task_id="task-1",
            status=None,
            task_spec=TaskSpec(Context(title="next"), Goal("resolve", [])),
            run_info=RuntimeInfo(run_mode="single_bot", assignee="bot-2"),
            node_run_graph=None,
        ),
    ))
    graph.append_action_event(
        "task-1",
        report.target_node_id,
        NodeAction.DISPATCH,
        {"phase": "delivery", "action_result": "delivered", "assignee": "bot-2"},
        status_from=Status.PENDING,
        status_to=Status.RUNNING,
    )
    graph.update_task_node_info(TaskNodePatch(
        task_id="task-1",
        node_id=report.target_node_id,
        status=Status.RUNNING,
    ))
    target = next(
        node for node in graph.query_task_dashboard("task-1").tasks
        if node.node_id == report.target_node_id
    )
    target_token = target.run_info.extend_props["relay_token"]
    graph.apply_relay_report(TaskReportCommand(
        task_id="task-1",
        node_id=target.node_id,
        event_id="execution-2",
        relay_token=target_token,
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=TaskNodePatch(
            task_id="task-1",
            node_id=target.node_id,
            execution_decision="ACCEPTED",
            actual_goal=Goal("resolve", []),
            output_patch={"result": "resolved"},
            local_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
        ),
    ))
    complete = graph.apply_relay_report(TaskReportCommand(
        task_id="task-1",
        node_id=target.node_id,
        event_id="plan-2",
        relay_token=target_token,
        event_type=TaskReportEventType.PLAN_RESULT,
        event=None,
        task_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
    ))
    assert complete.task_completed is True

    trajectory = asyncio.run(TaskTrajectoryService(
        _Assembler(), _Repo(), _Analyzer(), graph=graph,
    ).get_trajectory("task-1"))

    actions = [event.action_type for event in trajectory.timeline]
    assert actions.count(TrajectoryActionType.EXECUTE) == 2
    assert actions.count(TrajectoryActionType.VERIFY) == 2
    assert actions.count(TrajectoryActionType.PLAN) == 2
    assert actions.count(TrajectoryActionType.TRANSITION) == 2
    assert actions.count(TrajectoryActionType.DISPATCH) == 2
    search = next(event for event in trajectory.timeline if event.search_probe)
    assert search.search_probe["query"] == "incident response"
    assert any(
        event.node_id == report.target_node_id
        and event.action_type is TrajectoryActionType.DISPATCH
        and event.action_result == "delivered"
        for event in trajectory.timeline
    )


def test_relay_bbs_plan_and_recovery_are_visible_in_timeline() -> None:
    graph = _graph()
    graph.query_task_dashboard("task-1").extend_props["runtime_profile"][
        "allowed_run_modes"
    ] = ["single_bot", "bbs"]
    graph.apply_relay_report(TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="execution-1",
        relay_token="generation-1",
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=TaskNodePatch(
            task_id="task-1",
            node_id="task-1",
            execution_decision="ACCEPTED",
            actual_goal=Goal("inspect", []),
            output_patch={"result": "partial"},
            local_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
        ),
    ))
    report = graph.apply_relay_report(TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="plan-bbs",
        relay_token="generation-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=TaskNode(
            node_id=None,
            task_id="task-1",
            status=None,
            task_spec=TaskSpec(Context(title="bbs"), Goal("find specialist", [])),
            run_info=RuntimeInfo(run_mode="bbs", assignee=None),
            node_run_graph=None,
        ),
    ))
    assert report.bbs_publish_pending is True

    bbs_node = next(
        node for node in graph.query_task_dashboard("task-1").tasks
        if node.node_id == report.target_node_id
    )

    class _Intents:
        def requeue_delivery_intent(self, *args, **kwargs):
            raise AssertionError("execution-stage recovery does not requeue")

    calls = []

    async def dispatch(task_id, node_id):
        calls.append((task_id, node_id))

    asyncio.run(RelayRecoveryStrategy(graph, _Intents(), dispatch).recover(
        TaskNodePatch(
            task_id="task-1",
            node_id=bbs_node.node_id,
            exec_error="claim_timeout",
        )
    ))

    trajectory = asyncio.run(TaskTrajectoryService(
        _Assembler(), _Repo(), _Analyzer(), graph=graph,
    ).get_trajectory("task-1"))
    assert calls == [("task-1", bbs_node.node_id)]
    assert any(
        event.node_id == bbs_node.node_id
        and event.action_type is TrajectoryActionType.RESET
        and event.action_result == "retry"
        for event in trajectory.timeline
    )
