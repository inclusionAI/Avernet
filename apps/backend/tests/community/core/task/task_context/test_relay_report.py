from __future__ import annotations

from dataclasses import replace

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskNodePatch,
    TaskReportCommand,
    TaskReportEventType,
    TaskReportStatus,
    TaskReportTrace,
    TaskReportTraceStep,
    TaskSpec,
    NodeAction,
)
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)


def _graph_service() -> TaskGraphService:
    graph = TaskExecutionGraph(
        task_id="task-1",
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        extend_props={
            "execution_config": {"orchestration_mode": "relay", "MAX_LOOP": 3},
            "runtime_profile": {"allowed_run_modes": ["single_bot", "bbs"]},
        },
    )
    root = TaskNode(
        node_id="task-1",
        task_id="task-1",
        status=Status.RUNNING,
        task_spec=TaskSpec(Context("background", title="root"), Goal("finish", [])),
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


def _execution() -> TaskReportCommand:
    return TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="execution-1",
        relay_token="attempt-1",
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=TaskNodePatch(
            task_id="task-1",
            node_id="task-1",
            execution_decision="ACCEPTED",
            actual_goal=Goal("cover part", []),
            output_patch={"result": "evidence"},
            local_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
        ),
    )


def _declined_execution() -> TaskReportCommand:
    return TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="execution-declined",
        relay_token="attempt-1",
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=TaskNodePatch(
            task_id="task-1",
            node_id="task-1",
            execution_decision="DECLINED",
            failure_reason="capability_mismatch",
        ),
    )


def test_report_creates_one_successor_and_projects_replay_receipt() -> None:
    service = _graph_service()
    execution = _execution()
    assert service.get_node_context("task-1", "task-1").actual_goal is None
    assert service.apply_relay_report(execution) == service.apply_relay_report(
        execution
    )
    assert service.get_node_context("task-1", "task-1").actual_goal == Goal(
        "cover part", []
    )
    assert (
        service.get_node_context("task-1", "task-1").capability_failure_reason is None
    )
    planned = TaskNode(
        node_id=None,
        task_id="task-1",
        status=None,
        task_spec=TaskSpec(Context("next", title="next"), Goal("finish remainder", [])),
        run_info=RuntimeInfo(run_mode="single_bot", assignee="bot-2"),
        node_run_graph=None,
    )
    command = TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="plan-1",
        relay_token="attempt-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=planned,
    )
    result = service.apply_relay_report(command)
    assert result.target_node_id is not None
    assert service.apply_relay_report(command) == result
    assert len(service.query_task_dashboard("task-1").tasks) == 2
    context = service.get_node_context("task-1", "task-1")
    assert context.accepted_plan_report == result
    assert context.all_done_output == []
    next_context = service.get_node_context("task-1", result.target_node_id)
    assert [fact.node_id for fact in next_context.all_done_output] == ["task-1"]
    actions = service.query_task_dashboard("task-1").tasks[0].run_info.action_log
    assert [event.action for event in actions] == [
        NodeAction.EXECUTE,
        NodeAction.VERIFY,
        NodeAction.PLAN,
        NodeAction.TRANSITION,
    ]


def test_report_rejects_conflicting_replay_and_invalid_plan_branch() -> None:
    service = _graph_service()
    service.apply_relay_report(_execution())
    changed = _execution()
    changed.event.output_patch["result"] = "other evidence"
    with pytest.raises(TaskStateError, match="conflicts"):
        service.apply_relay_report(changed)
    with pytest.raises(TaskStateError, match="exactly one"):
        service.apply_relay_report(
            TaskReportCommand(
                task_id="task-1",
                node_id="task-1",
                event_id="plan-invalid",
                relay_token="attempt-1",
                event_type=TaskReportEventType.PLAN_RESULT,
                event=None,
            )
        )
    assert service.query_task_dashboard("task-1").tasks[0].status == Status.RUNNING


def test_report_trace_is_recorded_once_and_is_part_of_event_fingerprint() -> None:
    service = _graph_service()
    trace = TaskReportTrace(
        steps=[
            TaskReportTraceStep(
                action_type="execute",
                action_result="context_read",
                action_input="读取上下文",
            ),
            TaskReportTraceStep(action_type="plan", action_result="gaps_refreshed"),
            TaskReportTraceStep(action_type="verify", action_result="accept_pass"),
        ]
    )
    execution = replace(_execution(), trace=trace)

    assert service.apply_relay_report(execution).status == TaskReportStatus.APPLIED

    # trace 不再注入 action_log payload(持久载体已改 trajectory 表行)
    actions = service.query_task_dashboard("task-1").tasks[0].run_info.action_log
    for action in actions:
        assert "trace" not in action.payload

    # trace 属于 event fingerprint:同 event_id 不同 trace → 冲突拒绝
    changed_trace = TaskReportTrace(
        steps=[
            TaskReportTraceStep(action_type="execute", action_result="changed"),
        ]
    )
    with pytest.raises(TaskStateError, match="conflicts"):
        service.apply_relay_report(replace(execution, trace=changed_trace))


def test_plan_report_trace_is_recorded_once() -> None:
    service = _graph_service()
    service.apply_relay_report(_execution())
    planned = TaskNode(
        node_id=None,
        task_id="task-1",
        status=None,
        task_spec=TaskSpec(Context("next", title="next"), Goal("finish remainder", [])),
        run_info=RuntimeInfo(run_mode="single_bot", assignee="bot-2"),
        node_run_graph=None,
    )
    command = TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="plan-trace",
        relay_token="attempt-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=planned,
        trace=TaskReportTrace(
            steps=[
                TaskReportTraceStep(
                    action_type="plan",
                    action_result="gaps_refreshed",
                    action_input="S5A_REFRESH_GAPS",
                ),
            ]
        ),
    )

    result = service.apply_relay_report(command)

    actions = service.query_task_dashboard("task-1").tasks[0].run_info.action_log
    assert result.status == TaskReportStatus.APPLIED
    # trace 不再注入 action_log(持久载体改为 trajectory 表行);重放同 command 幂等
    for action in actions:
        assert "trace" not in action.payload


def test_accepted_empty_output_is_still_a_recoverable_execution_fact() -> None:
    service = _graph_service()
    execution = _execution()
    execution = replace(
        execution,
        event=replace(execution.event, output_patch={}),
    )

    assert service.apply_relay_report(execution).status == TaskReportStatus.APPLIED

    context = service.get_node_context("task-1", "task-1")
    assert context.accepted_execution_patch is not None
    assert context.accepted_execution_patch.output_patch == {}
    assert context.actual_goal == Goal("cover part", [])


def test_declined_execution_recovery_projects_capability_failure_reason() -> None:
    service = _graph_service()
    assert service.apply_relay_report(_declined_execution()).status.value == "APPLIED"

    context = service.get_node_context("task-1", "task-1")

    assert context.actual_goal is None
    assert context.capability_failure_reason == "capability_mismatch"
    assert context.accepted_execution_patch is not None
    assert context.accepted_execution_patch.execution_decision == "DECLINED"


def test_report_completion_is_idempotent_and_does_not_create_node() -> None:
    service = _graph_service()
    service.apply_relay_report(_execution())
    complete = TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="plan-complete",
        relay_token="attempt-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=None,
        task_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
    )
    result = service.apply_relay_report(complete)
    assert result.task_completed
    assert service.apply_relay_report(complete) == result
    graph = service.query_task_dashboard("task-1")
    assert graph.status == Status.DONE
    assert len(graph.tasks) == 1
    assert [event.action for event in graph.tasks[0].run_info.action_log] == [
        NodeAction.EXECUTE,
        NodeAction.VERIFY,
        NodeAction.PLAN,
        NodeAction.TRANSITION,
    ]


def test_retry_fences_old_execution_without_changing_node_id() -> None:
    service = _graph_service()
    stale = _execution()
    service.update_task_node_info(
        TaskNodePatch(
            task_id="task-1",
            node_id="task-1",
            extend_props_patch={"harness_retries": 1},
        )
    )
    current = service.query_task_dashboard("task-1").tasks[0]
    new_token = current.run_info.extend_props["relay_token"]
    assert current.node_id == "task-1"
    assert new_token != "attempt-1"
    with pytest.raises(TaskStateError, match="expired"):
        service.apply_relay_report(stale)
    from dataclasses import replace

    retried = replace(stale, relay_token=new_token, event_id="execution-2")
    result = service.apply_relay_report(retried)
    assert service.apply_relay_report(retried) == result


def test_event_id_conflict_is_rejected_after_accepted_report() -> None:
    service = _graph_service()
    accepted = _execution()
    service.apply_relay_report(accepted)
    changed = _execution()
    changed.event.output_patch["result"] = "changed"
    with pytest.raises(TaskStateError, match="event_id conflicts"):
        service.apply_relay_report(changed)
