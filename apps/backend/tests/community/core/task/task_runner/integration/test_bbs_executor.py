import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import TaskExecutor


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _node(objective: str) -> TaskNode:
    return TaskNode(
        node_id="t1",
        task_id="t1",
        status=Status.HUNG,
        task_spec=TaskSpec(context=Context("updated context", title="BBS"), goal=Goal(objective, [AcceptanceCriteria("a1", "done")])),
        run_info=RuntimeInfo(run_mode="bbs"),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def test_task_executor_dispatch_bbs_delegates_to_modal_executor():
    """A BBS node is dispatched with the caller's current node snapshot."""
    on_bbs_report = AsyncMock()
    graph = MagicMock()
    persisted_root = _node("persisted objective")
    execution_graph = TaskExecutionGraph(
        run_id=1,
        loop_round=1,
        status=Status.HUNG,
        tasks=[persisted_root],
        task_id="t1",
    )
    graph.query_task_dashboard.return_value = execution_graph
    exe = TaskExecutor(
        bot=MagicMock(), bcs=MagicMock(), bcn=MagicMock(),
        formatter=None, context=None, sink=None, poller=None,
        graph=graph, api_base_url="http://test:8888", on_bbs_report=on_bbs_report,
    )
    node = _node("upper-layer updated objective")

    with patch(
        "agentclaw.community.core.task.task_runner.modal_executor.bbs_modal_executor.notify",
        new_callable=AsyncMock,
    ) as mock_notify:
        assert _run(exe.dispatch([node])) == [True]

    mock_notify.assert_awaited_once()
    kwargs = mock_notify.call_args.kwargs
    assert kwargs["backend_url"] == "http://test:8888"
    dispatched_graph = kwargs["execution_graph"]
    assert dispatched_graph is not execution_graph
    assert dispatched_graph.tasks[0] is node
    assert dispatched_graph.tasks[0].task_spec.goal.objective == "upper-layer updated objective"
    assert execution_graph.tasks == [persisted_root]
    assert kwargs["bcn"] is exe._bcn
    assert kwargs["bot"] is exe._bot
    assert kwargs["on_bbs_report"] is on_bbs_report


def test_task_executor_dispatch_bbs_propagates_graph_lookup_failure():
    graph = MagicMock()
    graph.query_task_dashboard.side_effect = RuntimeError("graph unavailable")
    exe = TaskExecutor(
        bot=MagicMock(), bcs=MagicMock(), bcn=MagicMock(),
        formatter=None, context=None, sink=None, poller=None,
        graph=graph, api_base_url="http://test:8888",
    )

    with pytest.raises(RuntimeError, match="graph unavailable"):
        _run(exe.dispatch([_node("updated objective")]))


def test_task_executor_dispatch_bbs_returns_false_when_node_missing():
    graph = MagicMock()
    graph.query_task_dashboard.return_value = TaskExecutionGraph(
        run_id=1, loop_round=1, status=Status.HUNG, tasks=[], task_id="t1",
    )
    exe = TaskExecutor(
        bot=MagicMock(), bcs=MagicMock(), bcn=MagicMock(),
        formatter=None, context=None, sink=None, poller=None,
        graph=graph, api_base_url="http://test:8888",
    )

    with patch(
        "agentclaw.community.core.task.task_runner.modal_executor.bbs_modal_executor.notify",
        new_callable=AsyncMock,
    ) as notify:
        assert _run(exe.dispatch([_node("updated objective")])) == [False]
        notify.assert_not_awaited()


def test_release_relay_bbs_claim_emits_bbs_released_trajectory():
    """零盲区补点③:认领失败的 relay BBS 棒归还广场时,同步落 execute/bbs_released
    轨迹行(前一条 bbs_execution_* 行只记因,不记"已回广场"这个态)。"""
    from types import SimpleNamespace

    from agentclaw.community.core.task.task_runner.modal_executor import (
        bbs_modal_executor as bbs,
    )

    graph = MagicMock()  # report 两笔补丁 fire-and-forget
    node = SimpleNamespace(
        node_id="baton-1", status=Status.RUNNING,
        run_info=SimpleNamespace(extend_props={}),
    )
    execution_graph = SimpleNamespace(task_id="t1", tasks=[node])
    emissions: list[tuple] = []
    tcs = SimpleNamespace(
        emit_trajectory_event=lambda *a, **k: emissions.append((a, k))
    )

    bbs._release_relay_bbs_claim(
        graph, "t1", "baton-1", "relay BBS bot replied without EXECUTION_RESULT",
        task_context_service=tcs, execution_graph=execution_graph,
    )

    # 两笔归还补丁(NODE_PATCH + GRAPH_PATCH)与一条轨迹事件
    assert graph.report.call_count == 2
    assert len(emissions) == 1
    args, kwargs = emissions[0]
    assert args[0] == "t1"
    assert args[1] == "baton-1"
    assert args[2] == "execute"
    assert kwargs["action_result"] == "bbs_released"
    ext = kwargs["ext_info"]
    assert ext["execution_mode"] == "bbs"
    assert ext["phase"] == "bbs_modal"
    assert "EXECUTION_RESULT" in ext["release_reason"]

    # 未接线(task_context_service/execution_graph 缺省)→ 静默跳过发射,不抛不炸
    bbs._release_relay_bbs_claim(graph, "t1", "baton-1", "boom")
    assert graph.report.call_count == 4  # 补丁照常,轨迹不增
    assert len(emissions) == 1


def test_release_relay_bbs_claim_trajectory_failure_does_not_block_patches():
    """轨迹发射抛错被吞(决策 #14,_emit_bbs_trajectory 内建):归还补丁不回滚不外抛。"""
    from types import SimpleNamespace

    from agentclaw.community.core.task.task_runner.modal_executor import (
        bbs_modal_executor as bbs,
    )

    graph = MagicMock()

    class _ExplodingTcs:
        def emit_trajectory_event(self, *args, **kwargs):
            raise RuntimeError("tcs down")

    bbs._release_relay_bbs_claim(
        graph, "t1", "baton-1", "boom",
        task_context_service=_ExplodingTcs(),
        execution_graph=SimpleNamespace(task_id="t1", tasks=[]),
    )
    assert graph.report.call_count == 2  # 两笔补丁已落地,发射失败不阻断
