from __future__ import annotations

from tests.community.core.task.task_center.test_relay_execution import (
    _accepted,
    _child_spec,
    _request,
    _run,
    _service,
)
from agentclaw.community.core.task.domain.models import Status


def _report(service, *, event_id: str, node_id: str, event_type: str, **kwargs):
    return _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=node_id,
            event_type=event_type,
            event_id=event_id,
            **kwargs,
        )
    )


def test_relay_second_bbs_miss_publishes_next_baton() -> None:
    """A claimed BBS baton can itself hand another MISS to the BBS square."""
    service, graph_service = _service()
    _run(service.execute(_request()))
    first_turn = _report(
        service,
        event_id="first-exec",
        node_id="relay-task",
        event_type="EXECUTION_RESULT",
        holder_id="main-bot",
        progress_reason="首棒完成",
        payload=_accepted("done"),
    )["relay_turn"]
    first_plan = _report(
        service,
        event_id="first-plan",
        node_id="relay-task",
        event_type="PLAN_RESULT",
        holder_id="main-bot",
        relay_turn=first_turn,
        progress_reason="第一棒后转 BBS",
        payload={"gaps": ["gap-1"], "next_task_spec": _child_spec()},
    )
    first_bbs = first_plan["target_node_id"]
    _report(
        service,
        event_id="first-miss",
        node_id=first_bbs,
        event_type="DISPATCH_RESULT",
        holder_id="main-bot",
        relay_turn=first_turn,
        progress_reason="无候选",
        failure_reason="无候选",
        payload={"outcome": "MISS"},
    )
    service.claim_bbs_task("relay-task", "bbs-bot", first_bbs)
    second_turn = _report(
        service,
        event_id="bbs-exec",
        node_id=first_bbs,
        event_type="EXECUTION_RESULT",
        holder_id="bbs-bot",
        progress_reason="BBS 完成",
        payload=_accepted({"bbs": "done"}),
    )["relay_turn"]
    second_plan = _report(
        service,
        event_id="bbs-plan",
        node_id=first_bbs,
        event_type="PLAN_RESULT",
        holder_id="bbs-bot",
        relay_turn=second_turn,
        progress_reason="第二棒后仍有缺口",
        payload={"gaps": ["gap-2"], "next_task_spec": _child_spec()},
    )
    second_bbs = second_plan["target_node_id"]
    second_miss = _report(
        service,
        event_id="second-miss",
        node_id=second_bbs,
        event_type="DISPATCH_RESULT",
        holder_id="bbs-bot",
        relay_turn=second_turn,
        progress_reason="第二棒后无候选",
        failure_reason="无候选",
        payload={"outcome": "MISS"},
    )
    graph = graph_service.query_task_dashboard("relay-task")
    first_node = next(node for node in graph.tasks if node.node_id == first_bbs)
    next_bbs = next(node for node in graph.tasks if node.node_id == second_bbs)
    assert second_miss["published_bbs"] is True
    # The predecessor stays DONE; only the newly planned baton becomes a
    # PENDING BBS node in the square.
    assert first_node.status is Status.DONE
    assert next_bbs.status is Status.PENDING
    assert next_bbs.run_info.run_mode == "bbs"
    assert graph.extend_props["bbs_mode"] is True
    assert graph.extend_props["bbs_node_id"] == second_bbs
