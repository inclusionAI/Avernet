# tests/community/core/task/task_runner/integration/test_bbs_executor.py
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _node(objective: str) -> TaskNode:
    return TaskNode(
        node_id="t1",
        task_id="t1",
        status=Status.HUNG,
        task_spec=TaskSpec(
            Metadata("t1", "BBS", "execute updated task"),
            Context("updated context"),
            Goal(objective, [AcceptanceCriteria("a1", "done")]),
        ),
        run_info=RuntimeInfo(run_mode="bbs"),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def test_task_executor_dispatch_bbs_delegates_to_bbs_runner():
    """TaskExecutor.dispatch delegates BBS nodes to bbs_runner.notify.

    BBS 候选 roster 经注入的 BcnService(复用统一 provider 身份)查询:notify 以
    ``bcn=``(BcnService)、``bot=`` 透传,不再传 ``bcs``。
    """
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
    exe = TaskExecutor(bot=MagicMock(), bcs=MagicMock(), bcn=MagicMock(),
                       formatter=None, context=None, sink=None, poller=None,
                       graph=graph, api_base_url="http://test:8888",
                       on_bbs_report=on_bbs_report)
    node = _node("upper-layer updated objective")
    with patch("agentclaw.community.core.task.task_runner.integration.bbs_runner.notify", new_callable=AsyncMock) as mock_notify:
        assert _run(exe.dispatch([node])) == [True]
        mock_notify.assert_awaited_once()
        call_kwargs = mock_notify.call_args
        assert call_kwargs.kwargs["backend_url"] == "http://test:8888"
        dispatched_graph = call_kwargs.kwargs["execution_graph"]
        assert dispatched_graph is not execution_graph
        assert dispatched_graph.tasks[0] is node
        assert dispatched_graph.tasks[0].task_spec.goal.objective == "upper-layer updated objective"
        assert execution_graph.tasks == [persisted_root]
        assert call_kwargs.kwargs["bcn"] is exe._bcn
        assert call_kwargs.kwargs["bot"] is exe._bot
        assert call_kwargs.kwargs["on_bbs_report"] is on_bbs_report  # 引擎收口回调透传 notify


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
        run_id=1,
        loop_round=1,
        status=Status.HUNG,
        tasks=[],
        task_id="t1",
    )
    exe = TaskExecutor(
        bot=MagicMock(), bcs=MagicMock(), bcn=MagicMock(),
        formatter=None, context=None, sink=None, poller=None,
        graph=graph, api_base_url="http://test:8888",
    )

    with patch(
        "agentclaw.community.core.task.task_runner.integration.bbs_runner.notify",
        new_callable=AsyncMock,
    ) as notify:
        assert _run(exe.dispatch([_node("updated objective")])) == [False]
        notify.assert_not_awaited()
