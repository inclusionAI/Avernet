# tests/community/core/task/task_runner/integration/test_runner_bbs.py
import asyncio
from unittest.mock import MagicMock, AsyncMock
from agentclaw.community.core.task.task_runner.task_runner import TaskRunner


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_task_runner_run_bbs_delegates_to_execution_backend():
    backend = MagicMock()
    backend.run_bbs = AsyncMock()
    runner = TaskRunner(graph=None, execution_backend=backend)
    g = MagicMock()
    g.task_id = "t1"
    _run(runner.run_bbs(g))
    backend.run_bbs.assert_awaited_once_with(g)


def test_task_runner_run_bbs_stub_when_no_backend():
    runner = TaskRunner(graph=None)  # no backend
    g = MagicMock()
    g.task_id = "t1"
    _run(runner.run_bbs(g))  # no exception, no crash
