from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)
from agentclaw.community.core.task.task_harness.harness import TaskHarness


def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_id=task_id,
        task_spec=TaskSpec(
            context=Context(background="bg", title="T"),
            goal=Goal(
                objective="o",
                acceptances=[AcceptanceCriteria(id="ac1", description="d")],
            ),
        ),
        source_type="bot",
        owner_bot_id="b1",
    )


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec,
        run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kwargs) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kwargs)


def _build_graph(svc: TaskGraphService, graph, *, relay: bool = True) -> None:
    if relay:
        graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
    svc.add_task_nodes([_child("c1")], parent_node_id="t1")
    root = svc._get_node(graph, "t1")
    root.status = Status.DONE  # hand-off taken: graph state follows the active leaf


def _dispatch_running(
    svc: TaskGraphService,
    *,
    run_mode: str,
    start_time_ms: int,
    assignee: str | None = None,
) -> None:
    svc.update_task_node_info(
        _patch(
            "t1",
            "c1",
            status=Status.RUNNING,
            run_mode=run_mode,
            assignee=assignee,
            start_time=start_time_ms,
        )
    )


def _recorder() -> list[TaskNodePatch]:
    return []


@pytest.mark.parametrize("run_mode", ["single_bot", "coop_group"])
def test_relay_stale_execution_node_is_terminated(run_mode):
    svc = TaskGraphService()
    graph = svc.initialize_graph(_task_info())
    _build_graph(svc, graph, relay=True)
    now_ms = 1_000_000_000_000
    _dispatch_running(
        svc,
        run_mode=run_mode,
        start_time_ms=now_ms - 7 * 3600 * 1000,
        assignee="bot1",
    )
    harness = TaskHarness(
        svc,
        lambda patch: _recorder().append(patch),
        clock=lambda: 0.0,
        wall_clock_ms=lambda: now_ms,
        default_sla_timeout=1.0,
        interval=0.0,
    )
    harness.register("t1")

    patches = harness._poll_once()
    node = svc._get_node(graph, "c1")

    assert len(patches) == 1
    assert patches[0].status == Status.HUNG
    assert node.status == Status.HUNG
    assert patches[0].failure_reason == "harness_early_exit: stale_6h"
    assert patches[0].extend_props_patch["harness_early_exit"]["reason"] == "stale_6h"
    assert graph.effective_status == Status.HUNG


def test_relay_fresh_node_is_not_early_exited():
    svc = TaskGraphService()
    graph = svc.initialize_graph(_task_info())
    _build_graph(svc, graph, relay=True)
    now_ms = 1_000_000_000_000
    _dispatch_running(
        svc,
        run_mode="single_bot",
        start_time_ms=now_ms - 3600 * 1000,
        assignee="bot1",
    )

    harness = TaskHarness(
        svc,
        lambda patch: _recorder().append(patch),
        clock=lambda: 0.0,
        wall_clock_ms=lambda: now_ms,
        default_sla_timeout=1.0,
        interval=0.0,
    )
    harness.register("t1")

    patches = harness._poll_once()
    node = svc._get_node(graph, "c1")

    assert patches == []
    assert node.status == Status.RUNNING
    assert graph.effective_status == Status.RUNNING


def test_non_relay_node_is_not_early_exited():
    svc = TaskGraphService()
    graph = svc.initialize_graph(_task_info())
    _build_graph(svc, graph, relay=False)
    now_ms = 1_000_000_000_000
    _dispatch_running(
        svc,
        run_mode="single_bot",
        start_time_ms=now_ms - 7 * 3600 * 1000,
        assignee="bot1",
    )

    harness = TaskHarness(
        svc,
        lambda patch: _recorder().append(patch),
        clock=lambda: 0.0,
        wall_clock_ms=lambda: now_ms,
        default_sla_timeout=1.0,
        interval=0.0,
    )
    harness.register("t1")

    patches = harness._poll_once()
    node = svc._get_node(graph, "c1")

    assert patches == []
    assert node.status == Status.RUNNING
    assert node.run_info.failure_reason is None


def test_relay_bbs_node_is_not_early_exited():
    svc = TaskGraphService()
    graph = svc.initialize_graph(_task_info())
    _build_graph(svc, graph, relay=True)
    now_ms = 1_000_000_000_000
    _dispatch_running(svc, run_mode="bbs", start_time_ms=now_ms - 7 * 3600 * 1000)

    harness = TaskHarness(
        svc,
        lambda patch: _recorder().append(patch),
        clock=lambda: 0.0,
        wall_clock_ms=lambda: now_ms,
        default_sla_timeout=1.0,
        interval=0.0,
    )
    harness.register("t1")

    patches = harness._poll_once()
    node = svc._get_node(graph, "c1")

    assert patches == []
    assert node.status == Status.RUNNING
    assert node.run_info.failure_reason is None
