"""Relay state-machine guards for stale execution reports."""

from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError

from tests.community.core.task.task_center.test_relay_execution import (
    _accepted,
    _child_spec,
    _request,
    _run,
    _service,
)


def test_relay_rejects_new_execution_event_after_successor_is_planned() -> None:
    """A newly generated execution report cannot revive a handed-off baton."""
    service, graph_service = _service()
    _run(service.execute(_request()))
    turn = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="stale-guard-exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("first output"),
        )
    )["relay_turn"]
    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="PLAN_RESULT",
            event_id="stale-guard-plan",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="仍有缺口",
            payload={"gaps": ["gap"], "next_task_spec": _child_spec()},
        )
    )

    with pytest.raises(TaskStateError, match="EXECUTION_RESULT target"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="stale-guard-exec-new",
                holder_id="main-bot",
                progress_reason="迟到的新执行事件尝试改写前序节点",
                payload=_accepted("late output"),
            )
        )

    # A state-guard rejection must release the new event reservation. It cannot
    # poison the same event_id with an in-progress reservation until TTL expiry.
    with pytest.raises(TaskStateError, match="EXECUTION_RESULT target"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="stale-guard-exec-new",
                holder_id="main-bot",
                progress_reason="按同一事件重试验证状态守卫不会占用幂等事件",
                payload=_accepted("late output"),
            )
        )

    graph = graph_service.query_task_dashboard("relay-task")
    origin = next(node for node in graph.tasks if node.node_id == "relay-task")
    assert origin.status.value == "DONE"
    assert origin.run_info.output == {"result": "first output"}
    assert len(graph.tasks) == 2
