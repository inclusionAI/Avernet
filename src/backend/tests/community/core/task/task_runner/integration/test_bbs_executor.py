# tests/community/core/task/task_runner/integration/test_bbs_executor.py
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_task_executor_run_bbs_delegates_to_bbs_runner():
    """TaskExecutor.run_bbs delegates to bbs_runner.notify with correct args.

    BBS 候选 roster 经注入的 BcnService(复用统一 provider 身份)查询:notify 以
    ``bcn=``(BcnService)、``bot=`` 透传,不再传 ``bcs``。
    """
    on_bbs_report = AsyncMock()
    exe = TaskExecutor(bot=MagicMock(), bcs=MagicMock(), bcn=MagicMock(),
                       formatter=None, context=None, sink=None, poller=None,
                       api_base_url="http://test:8888", on_bbs_report=on_bbs_report)
    g = MagicMock()
    g.task_id = "t1"
    with patch("agentclaw.community.core.task.task_runner.integration.bbs_runner.notify", new_callable=AsyncMock) as mock_notify:
        _run(exe.run_bbs(g))
        mock_notify.assert_awaited_once()
        call_kwargs = mock_notify.call_args
        assert call_kwargs.kwargs["backend_url"] == "http://test:8888"
        assert call_kwargs.kwargs["execution_graph"] is g
        assert call_kwargs.kwargs["bcn"] is exe._bcn
        assert call_kwargs.kwargs["bot"] is exe._bot
        assert call_kwargs.kwargs["on_bbs_report"] is on_bbs_report  # 引擎收口回调透传 notify
