import asyncio

from agentclaw.community.core.task.task_runner.client.ports import BotSendResult
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import TaskExecutor
from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
    TaskExecutorResultPoller,
)
from agentclaw.community.core.task.task_runner.task_runner import TaskRunner


class _FakeBot:
    async def ensure_grant(self, bot_id): return None
    async def send_message(self, *, bot_id, message, metadata):
        return BotSendResult(run_id="r", session_id="ws-session")
    async def get_run(self, run_id): return {}
    async def cancel_run(self, run_id): return None
    async def send_and_wait_async(self, **kw): return {}


class _FakeBcs:
    async def get_group(self, group_id):
        return {"latest_running_session_id": f"latest-for-{group_id}"}


def _executor_with_backends():
    # poller __init__ is (bot, bcs, ...) — no `sink` kwarg; sink is set via set_on_result.
    poller = TaskExecutorResultPoller(bot=None, bcs=None)
    ex = TaskExecutor.__new__(TaskExecutor)
    ex._bot = _FakeBot()
    ex._bcs = _FakeBcs()
    ex._poller = poller
    ex._group_meta = {"g_stash": {"session_id": "stashed"}}
    return ex


def test_get_group_session_reads_stashed_then_fetches_latest():
    ex = _executor_with_backends()
    runner = TaskRunner(graph=None, execution_backend=ex)
    loop = asyncio.new_event_loop()
    assert loop.run_until_complete(runner.get_group_session("g_stash")) == "stashed"
    # absent in _group_meta -> GET /groups/{id} 响应的 latest_running_session_id 取最近 session
    assert loop.run_until_complete(runner.get_group_session("g_new")) == "latest-for-g_new"
