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
    relay_action_event_fields,
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


def test_relay_action_event_fields_builds_emit_kwargs() -> None:
    """写时发射的字段构建:与读时投影同源——同一动作两条路径产生同形事件;
    ``__node_id`` 改作定位参数返回、``__holder_id`` 归位 ext_info.holder_id
    (表行执行人的白名单投影来源),``relay_event_id`` 附带可追溯性。"""
    event = NodeActionEvent(
        seq=1,
        ts=1790000000123,
        action=NodeAction.EXECUTE,
        attempt=2,
        status_from=Status.RUNNING,
        status_to=Status.RUNNING,
        payload={
            "__node_id": "node-1",
            "__holder_id": "bot-9",
            "success": False,
            "execution_decision": "DECLINED",
            "failure_reason": "tool 500",
            "output": {},
        },
    )

    fields = relay_action_event_fields(event, relay_event_id="ev-77")

    assert fields["node_id"] == "node-1"
    assert fields["action_result"] == "failed"
    assert fields["attempt"] == 2
    assert fields["now_ms"] == 1790000000123, "发射行 gmt_create = 动作 ts(签名同源)"
    assert fields["status_from"] == Status.RUNNING
    assert fields["status_to"] == Status.RUNNING
    assert fields["error_msg"] == "tool 500"
    assert fields["ext_info"] == {
        "success": False,
        "execution_decision": "DECLINED",
        "failure_reason": "tool 500",
        "output": {},
        "holder_id": "bot-9",
        "relay_event_id": "ev-77",
    }


def test_merge_dedup_matches_written_rows_at_second_granularity() -> None:
    """写时发射行(action_log 同源同 ts)必须被读时投影去重:MySQL ``gmt_create``
    是秒精度 timestamp(ms 落库即被截断),去重签名按**秒粒度**比对,发射行与投影
    同秒即归一,时间线不出现双份。"""
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
                    ts=1790000000900,   # 与已落表行同秒(ms 尾差=MySQL 截断重读)
                    action=NodeAction.EXECUTE,
                    status_from=Status.RUNNING,
                    status_to=Status.RUNNING,
                    payload={"__node_id": "node-1", "success": True},
                ),
            ],
        ),
        node_run_graph=graph,
    )
    graph.tasks.append(node)
    # 已有表行:同 node/action/attempt/status,gmt_create=1790000000500(同秒不同 ms)
    trajectory = TaskTrajectory(
        task_id="task-1",
        gmt_create=1790000000500,
        gmt_modified=1790000000500,
        timeline=[
            _event("node-1", TrajectoryActionType.EXECUTE, 1790000000500,
                   Status.RUNNING, Status.RUNNING),
        ],
    )

    added = merge_relay_action_log(trajectory, graph)

    assert added == 0, "同秒同源动作应被去重(写时已落表,投影不再重复拼装)"
    assert len(trajectory.timeline) == 1


def _event(node_id, action_type, gmt_create, status_from, status_to):
    from agentclaw.community.core.task.task_context.task_trajectory.models import (
        TrajectoryEvent,
    )

    return TrajectoryEvent(
        task_id="task-1",
        node_id=node_id,
        action_type=action_type,
        action_result="success",
        attempt=0,
        gmt_create=gmt_create,
        gmt_modified=gmt_create,
        status_from=status_from,
        status_to=status_to,
    )


def test_projection_projects_trace_steps_from_action_payload() -> None:
    """trace_steps 投影一致性:携带 trace 的 action_log 事件(存量 relay 任务的
    读时投影路径)同样展开 trace_steps——与写时发射行的表行投影同形。"""
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
                    action=NodeAction.EXECUTE,
                    status_from=Status.RUNNING,
                    status_to=Status.RUNNING,
                    payload={
                        "__node_id": "node-1",
                        "success": True,
                        "trace": {
                            "steps": [
                                {"step": "S1", "status": "done",
                                 "detail": {"read": "spec"}},
                                {"step": "S5b", "status": "ok",
                                 "detail": {}},
                            ]
                        },
                    },
                ),
            ],
        ),
        node_run_graph=graph,
    )
    graph.tasks.append(node)
    trajectory = TaskTrajectory(task_id="task-1", gmt_create=100, gmt_modified=100)

    merge_relay_action_log(trajectory, graph)

    assert len(trajectory.timeline) == 1
    assert trajectory.timeline[0].trace_steps == [
        {"step": "S1", "status": "done", "detail": {"read": "spec"}},
        {"step": "S5b", "status": "ok", "detail": {}},
    ]


def test_projection_projects_s6_dispatch_trace_from_action_log() -> None:
    """S6 的 dispatch action log 也按 trace_steps 投影，服务端投递细览可排查。"""
    graph = TaskExecutionGraph(
        task_id="task-1",
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        extend_props={"execution_config": {"orchestration_mode": "relay"}},
    )
    node = TaskNode(
        node_id="node-s6",
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
                        "__node_id": "node-s6",
                        "phase": "delivery",
                        "action_result": "delivered",
                        "trace": {
                            "steps": [
                                {
                                    "step": "S6_DISPATCH",
                                    "status": "completed",
                                    "detail": {
                                        "action_type": "dispatch",
                                        "action_input": '{"target_node_id":"node-s6"}',
                                        "action_result": "delivered",
                                        "status_from": "PENDING",
                                        "status_to": "RUNNING",
                                        "boost_reason": "relay handoff accepted",
                                        "error_msg": None,
                                    },
                                }
                            ]
                        },
                    },
                )
            ],
        ),
        node_run_graph=graph,
    )
    graph.tasks.append(node)
    trajectory = TaskTrajectory(task_id="task-1", gmt_create=100, gmt_modified=100)

    merge_relay_action_log(trajectory, graph)

    assert trajectory.timeline[0].trace_steps == [
        {
            "step": "S6_DISPATCH",
            "status": "completed",
            "detail": {
                "action_type": "dispatch",
                "action_input": '{"target_node_id":"node-s6"}',
                "action_result": "delivered",
                "status_from": "PENDING",
                "status_to": "RUNNING",
                "boost_reason": "relay handoff accepted",
                "error_msg": None,
            },
        }
    ]
