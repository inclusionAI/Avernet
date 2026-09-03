# tests/community/core/task/task_runner/integration/test_runner_bbs.py
import asyncio
from unittest.mock import MagicMock, AsyncMock
from agentclaw.community.core.task.task_runner.task_runner import TaskRunner


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _bbs_node():
    node = MagicMock()
    node.task_id = "t1"
    node.node_id = "t1"
    node.run_info.run_mode = "bbs"
    node.run_info.assignee = None
    return node


def test_task_runner_start_run_delegates_bbs_to_execution_backend():
    backend = MagicMock()
    backend.dispatch = AsyncMock(return_value=[True])
    runner = TaskRunner(graph=None, execution_backend=backend)
    node = _bbs_node()
    assert _run(runner.start_run([node])) == [True]
    backend.dispatch.assert_awaited_once_with([node])


def test_task_runner_start_run_bbs_stub_when_no_backend():
    runner = TaskRunner(graph=None)  # no backend
    assert _run(runner.start_run([_bbs_node()])) == [True]


def test_task_runner_sends_real_backend_one_batch():
    backend = MagicMock()
    backend.dispatch = AsyncMock(return_value=[True, False])
    runner = TaskRunner(graph=None, execution_backend=backend)
    nodes = [_bbs_node(), _bbs_node()]
    nodes[1].node_id = "t2"

    assert _run(runner.start_run(nodes)) == [True, False]
    backend.dispatch.assert_awaited_once_with(nodes)
