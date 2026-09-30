from agentclaw.community.core.task.domain.models import RuntimeInfo, Status, TaskNode
from agentclaw.community.core.task.task_runner.client.ports import BotSendResult
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
    TaskExecutor,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
    TaskExecutorResultPoller,
)


class _FakeBot:
    async def ensure_grant(self, bot_id):
        return None

    async def send_message(self, *, bot_id, message, metadata):
        return BotSendResult(run_id="run-1", session_id="sess-1")

    async def get_run(self, run_id):
        return {}

    async def cancel_run(self, run_id):
        return None

    async def send_and_wait_async(self, **kw):
        return {}




class _PullSettings:
    def is_enabled(self, setting_type):
        assert setting_type == "skill_report_enabled"
        return False

class _FakeFormatter:
    def format_execute(self, ctx, node):
        return "hello"

    def format_verify(self, ctx, node):
        return ""


class _FakeContext:
    def build(self, task_id, node_id):
        return {}


def _root_node():
    return TaskNode(
        node_id="n1",
        task_id="t1",
        status=Status.PENDING,
        task_spec=None,  # type: ignore[arg-type]
        run_info=RuntimeInfo(run_mode="single_bot", assignee="bot-1"),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def test_dispatch_single_bot_reads_bot_send_result_and_carries_session_id():
    import asyncio

    poller = TaskExecutorResultPoller(
        bot=None, bcs=None
    )  # __init__ takes bot/bcs only; sink is set via set_on_result
    ex = TaskExecutor.__new__(TaskExecutor)
    ex._bot = _FakeBot()
    ex._formatter = _FakeFormatter()
    ex._context = _FakeContext()
    ex._poller = poller
    ex._graph = None  # __new__ 跳过 __init__;补 __init__ 默认(无图→_persist_dispatch_ids 跳过落库)
    # This test exercises the poller Pull branch explicitly. Production default is Push.
    ex._task_settings = _PullSettings()
    ex._api_base_url = ""  # __init__ 默认:_dispatch_single_bot ctx 携带 backend
    ex._identity_resolver = None
    ex._bot_token_provider = None
    ex._bcs = None
    ex._bcn = None
    ex._on_bbs_report = None
    ex._group_meta = {}

    ok = asyncio.new_event_loop().run_until_complete(
        ex._dispatch_single_bot(_root_node(), asyncio.Semaphore(1))
    )
    assert ok is True
    handle = poller._handles[-1]
    assert handle.run_id == "run-1"
    assert handle.session_id == "sess-1"


def test_bot_send_result_is_frozen_dataclass():
    r = BotSendResult(run_id="r", session_id="s")
    assert r.run_id == "r" and r.session_id == "s"
    import dataclasses

    assert dataclasses.is_dataclass(r)
