from __future__ import annotations

import hashlib
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
from agentclaw.community.core.task.task_context import relay_report
from agentclaw.community.core.task.task_context.task_artifact.models import UrlLinkContent
from agentclaw.community.core.task.task_context.task_artifact.report_item import (
    ArtifactReportItem,
)
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)


def _record_search_candidates(service, candidates=("bot-2", "bot-manager", "bot-worker")):
    service.append_action_event(
        "task-1",
        "task-1",
        NodeAction.DISPATCH,
        {
            "phase": "search",
            "action_result": "search",
            "query": "test relay candidates",
            "relay_token_fingerprint": hashlib.sha256(b"attempt-1").hexdigest(),
            "candidate_count": len(candidates),
            "candidates": [
                {"bot_id": candidate, "name": candidate, "score": 0.9}
                for candidate in candidates
            ],
        },
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
    _record_search_candidates(service)
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


def test_node_context_projects_current_graph_attempt() -> None:
    service = _graph_service()
    node = service.query_task_dashboard("task-1").tasks[0]
    node.run_info.extend_props["harness_retries"] = 1

    assert service.get_node_context("task-1", "task-1").attempt == 1


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
    assert next_context.used_executor_ids == ["bot-1", "bot-2"]
    actions = service.query_task_dashboard("task-1").tasks[0].run_info.action_log
    assert [event.action for event in actions] == [
        NodeAction.DISPATCH,
        NodeAction.EXECUTE,
        NodeAction.VERIFY,
        NodeAction.PLAN,
        NodeAction.TRANSITION,
    ]



def _record_relay_search(service, *, bot_ids, relay_token="attempt-1"):
    service.append_action_event(
        "task-1",
        "task-1",
        NodeAction.DISPATCH,
        {
            "phase": "search",
            "action_result": "search",
            "query": "relay candidates",
            "relay_token_fingerprint": hashlib.sha256(relay_token.encode()).hexdigest(),
            "candidate_count": len(bot_ids),
            "candidates": [
                {"bot_id": bot_id, "name": bot_id, "score": 1.0}
                for bot_id in bot_ids
            ],
        },
    )


def test_successor_plan_requires_a_current_generation_search() -> None:
    service = _graph_service()
    service.apply_relay_report(_execution())
    service.query_task_dashboard("task-1").tasks[0].run_info.action_log.clear()

    with pytest.raises(
        TaskStateError, match="current-generation candidate search"
    ):
        service.apply_relay_report(
            _planned_single_bot_command(event_id="plan-no-search", assignee="bot-2")
        )


def test_successor_assignee_must_come_from_searched_candidates() -> None:
    service = _graph_service()
    service.apply_relay_report(_execution())
    node = service.query_task_dashboard("task-1").tasks[0]
    node.run_info.action_log.clear()
    _record_relay_search(service, bot_ids=["bot-1"])

    with pytest.raises(
        TaskStateError, match="must come from searched candidates"
    ):
        service.apply_relay_report(
            _planned_single_bot_command(event_id="plan-fake-bot", assignee="bot-2")
        )


def test_successor_plan_rejects_an_empty_search_receipt() -> None:
    service = _graph_service()
    service.apply_relay_report(_execution())
    node = service.query_task_dashboard("task-1").tasks[0]
    node.run_info.action_log.clear()
    _record_relay_search(service, bot_ids=[])

    with pytest.raises(
        TaskStateError, match="at least one searched candidate"
    ):
        service.apply_relay_report(
            _planned_single_bot_command(event_id="plan-empty-search", assignee="bot-2")
        )


def test_successor_accepts_composite_candidate_after_runtime_split() -> None:
    service = _graph_service()
    service.apply_relay_report(_execution())
    node = service.query_task_dashboard("task-1").tasks[0]
    node.run_info.action_log.clear()
    _record_relay_search(service, bot_ids=["bot-2:owner-2"])

    result = service.apply_relay_report(
        _planned_single_bot_command(
            event_id="plan-composite-search",
            assignee="bot-2:owner-2",
        )
    )

    assert result.target_node_id is not None
    successor = service.query_task_dashboard("task-1").tasks[1]
    assert successor.run_info.assignee == "bot-2"
    assert successor.run_info.extend_props["assignee_owner_id"] == "owner-2"


def test_successor_assignee_scope_must_match_searched_candidate() -> None:
    service = _graph_service()
    service.apply_relay_report(_execution())
    node = service.query_task_dashboard("task-1").tasks[0]
    node.run_info.action_log.clear()
    _record_relay_search(service, bot_ids=["bot-2:146836"])

    with pytest.raises(
        TaskStateError, match="must come from searched candidates"
    ):
        service.apply_relay_report(
            _planned_single_bot_command(
                event_id="plan-owner-scope",
                assignee="bot-2:35983",
            )
        )


def _planned_single_bot_command(
    *, event_id: str, assignee: str | None
) -> TaskReportCommand:
    planned = TaskNode(
        node_id=None,
        task_id="task-1",
        status=None,
        task_spec=TaskSpec(Context("next", title="next"), Goal("finish remainder", [])),
        run_info=RuntimeInfo(run_mode="single_bot", assignee=assignee),
        node_run_graph=None,
    )
    return TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id=event_id,
        relay_token="attempt-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=planned,
    )


def test_report_rejects_bbs_plan_when_runtime_profile_disallows_it() -> None:
    service = _graph_service()
    graph = service.query_task_dashboard("task-1")
    graph.extend_props["runtime_profile"] = {
        "allowed_run_modes": ["single_bot", "coop_group"]
    }
    command = _planned_single_bot_command(event_id="unused", assignee="bot-2")
    command.event.run_info.run_mode = "bbs"
    command.event.run_info.assignee = None
    command = replace(command, event_id="plan-bbs")

    assert service.apply_relay_report(_execution()).status == (
        TaskReportStatus.APPLIED
    )

    with pytest.raises(TaskStateError, match="planned run mode is not allowed: bbs"):
        service.apply_relay_report(command)


@pytest.mark.parametrize("assignee", ["NOT_DECIDED", "   "])
def test_plan_rejects_placeholder_single_bot_assignee(assignee: str) -> None:
    service = _graph_service()

    assert service.apply_relay_report(_execution()).status == (
        TaskReportStatus.APPLIED
    )

    with pytest.raises(
        TaskStateError, match="single_bot plan requires a real bot assignee"
    ):
        service.apply_relay_report(
            _planned_single_bot_command(
                event_id=f"plan-invalid-{assignee.strip() or 'blank'}",
                assignee=assignee,
            )
        )

    graph = service.query_task_dashboard("task-1")
    assert len(graph.tasks) == 1
    assert graph.tasks[0].status == Status.RUNNING
    assert not [
        event for event in graph.tasks[0].run_info.action_log
        if event.action == NodeAction.PLAN
    ]


def test_report_accepts_latest_skill_fact_after_infra_hung_state() -> None:
    service = _graph_service()
    node = service.query_task_dashboard("task-1").tasks[0]
    node.status = Status.HUNG
    node.run_info.failure_reason = "infra_reported_hung"
    graph = service.query_task_dashboard("task-1")
    graph.status = Status.HUNG
    graph.extend_props["hung_reason"] = "infra_reported_hung"

    assert service.apply_relay_report(_execution()).status == TaskReportStatus.APPLIED

    complete = TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="plan-after-hung",
        relay_token="attempt-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=None,
        task_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
    )
    result = service.apply_relay_report(complete)
    assert result.task_completed
    assert result.status == TaskReportStatus.APPLIED

    graph = service.query_task_dashboard("task-1")
    assert graph.status == Status.DONE
    assert node.status == Status.SUCCESS
    assert node.run_info.failure_reason is None
    assert "hung_reason" not in graph.extend_props
    transitions = [
        event for event in graph.tasks[0].run_info.action_log
        if event.action == NodeAction.TRANSITION
    ]
    assert transitions[-1].status_from == Status.HUNG
    assert transitions[-1].status_to == Status.SUCCESS

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
                action_type="S1_CONTEXT",
                action_result="context_read",
                action_input="读取上下文",
            ),
            TaskReportTraceStep(
                action_type="S2_GAPS",
                action_result="gaps_refreshed",
                action_input="{\"gaps\":[\"ac3\"]}",
            ),
            TaskReportTraceStep(
                action_type="S3_CAPABILITY",
                action_result="accept_pass",
                action_input="{\"actual_goal\":\"完成缺口\"}",
            ),
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
            TaskReportTraceStep(action_type="S1_CONTEXT", action_result="changed"),
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
                    action_type="S5A_REFRESH_GAPS",
                    action_result="gaps_refreshed",
                    action_input="{\"refresh_task_gaps\":[\"ac3\"]}",
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


def _final_artifact_item() -> ArtifactReportItem:
    return ArtifactReportItem(
        artifact_name="最终报告链接",
        artifact_kind="url_link",
        session_id=None,
        task_id="task-1",
        node_id="task-1",
        attempt=0,
        content=UrlLinkContent(
            url="https://example.com/final-report",
            type="最终报告链接",
            is_final_artifact="true",
        ),
    )


def _capture_node_calls(monkeypatch):
    calls = []

    class CaptureService:
        def capture_node(
            self,
            graph,
            node,
            *,
            artifact_patch,
            now_ms,
            derive_text_from_output=True,
        ):
            calls.append((node.node_id, artifact_patch))
            return []

    monkeypatch.setattr(
        relay_report, "default_publish_service", lambda: CaptureService()
    )
    return calls


def test_terminal_completion_captures_final_artifacts(monkeypatch) -> None:
    captured = _capture_node_calls(monkeypatch)
    service = _graph_service()
    service.apply_relay_report(_execution())
    final_item = _final_artifact_item()
    command = TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="plan-complete-final",
        relay_token="attempt-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=None,
        task_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
        artifact_patch=[final_item],
        final_conclusion="业务整体结论：主因已确认并完成处理",
    )

    result = service.apply_relay_report(command)

    assert result.task_completed
    assert captured[-1] == ("task-1", [final_item])
    assert service.query_task_dashboard("task-1").output["final_conclusion"] == (
        "业务整体结论：主因已确认并完成处理"
    )

def test_terminal_hung_captures_final_artifacts(monkeypatch) -> None:
    captured = _capture_node_calls(monkeypatch)
    service = _graph_service()
    service.apply_relay_report(_execution())
    final_item = _final_artifact_item()
    command = TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="plan-hung-final",
        relay_token="attempt-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=None,
        failure_reason="relay_no_candidate",
        artifact_patch=[final_item],
        final_conclusion="业务整体结论：已确认事实与下一步建议",
    )

    result = service.apply_relay_report(command)

    assert result.task_hung
    assert captured[-1] == ("task-1", [final_item])
    assert service.query_task_dashboard("task-1").output["final_conclusion"] == (
        "业务整体结论：已确认事实与下一步建议"
    )

def test_successor_plan_result_rejects_final_artifacts(monkeypatch) -> None:
    captured = _capture_node_calls(monkeypatch)
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
        event_id="plan-successor-final",
        relay_token="attempt-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=planned,
        artifact_patch=[_final_artifact_item()],
    )

    with pytest.raises(TaskStateError, match="successor PLAN_RESULT"):
        service.apply_relay_report(command)

    assert captured[-1] == ("task-1", None)

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
        NodeAction.DISPATCH,
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


def test_plan_splits_composite_single_bot_identity_for_node_projection() -> None:
    """Runtime state stores split identity while S5D candidates stay composite."""
    service = _graph_service()
    assert service.apply_relay_report(_execution()).status == (
        TaskReportStatus.APPLIED
    )
    node = service.query_task_dashboard("task-1").tasks[0]
    node.run_info.action_log.clear()
    _record_relay_search(service, bot_ids=["bot-2:owner-2"])

    command = _planned_single_bot_command(
        event_id="plan-composite", assignee="bot-2:owner-2"
    )
    result = service.apply_relay_report(command)

    assert result.target_node_id is not None
    successor = service.query_task_dashboard("task-1").tasks[1]
    assert successor.run_info.assignee == "bot-2"
    assert successor.run_info.extend_props["assignee_owner_id"] == "owner-2"
    context = service.get_node_context("task-1", result.target_node_id)
    assert context.used_executor_ids == ["bot-1", "bot-2:owner-2"]
