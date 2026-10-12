from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    NodeAction,
    NodeActionEvent,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    TaskTrajectory,
    TrajectoryActionType,
)
from agentclaw.community.core.task.task_context.task_trajectory.relay_action_projection import (
    merge_relay_action_log,
)


def test_merge_relay_action_log_projects_and_deduplicates_events() -> None:
    graph = TaskExecutionGraph(
        task_id="task-1",
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        extend_props={"execution_config": {"orchestration_mode": "relay"}},
    )
    node = TaskNode(
        node_id="node-1",
        task_id="task-1",
        status=Status.RUNNING,
        task_spec=None,
        run_info=RuntimeInfo(
            run_mode="single_bot",
            assignee="bot-1",
            action_log=[
                NodeActionEvent(
                    seq=1,
                    ts=100,
                    action=NodeAction.DISPATCH,
                    status_from=Status.PENDING,
                    status_to=Status.RUNNING,
                    payload={
                        "__node_id": "node-1",
                        "phase": "search",
                        "action_result": "search",
                        "query": "risk model",
                        "candidate_count": 1,
                    },
                ),
                NodeActionEvent(
                    seq=2,
                    ts=200,
                    action=NodeAction.RESET,
                    attempt=1,
                    status_from=Status.RUNNING,
                    status_to=Status.PENDING,
                    payload={
                        "__node_id": "node-1",
                        "reason": "timeout",
                        "failure_reason": "timeout",
                    },
                ),
            ],
        ),
        node_run_graph=graph,
    )
    graph.tasks.append(node)
    trajectory = TaskTrajectory(
        task_id="task-1",
        gmt_create=300,
        gmt_modified=300,
    )

    merge_relay_action_log(trajectory, graph)
    merge_relay_action_log(trajectory, graph)

    assert [event.action_type for event in trajectory.timeline] == [
        TrajectoryActionType.DISPATCH,
        TrajectoryActionType.RESET,
    ]
    assert trajectory.timeline[0].search_probe == {
        "query": "risk model",
        "candidate_count": 1,
    }
    assert trajectory.timeline[1].error_msg == "timeout"
    assert trajectory.gmt_create == 100
    assert trajectory.gmt_modified == 200


def test_merge_action_log_leaves_centralized_trajectory_unchanged() -> None:
    graph = TaskExecutionGraph(
        task_id="task-1",
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
    )
    trajectory = TaskTrajectory(
        task_id="task-1",
        gmt_create=100,
        gmt_modified=100,
    )

    merge_relay_action_log(trajectory, graph)

    assert trajectory.timeline == []
