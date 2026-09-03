import asyncio

from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_center.engine import CoopGroupStart, ExecutionEngine
from agentclaw.community.core.task.task_runner.client.ports import BotSendResult


class _FakeRunner:
    async def trigger_workflow(self, *, bot_id, message, metadata=None):
        return BotSendResult(run_id="r", session_id="ws-s")
    async def form_coop_group(self, gf):
        return "grp-1"
    async def get_group_session(self, group_id):
        return "sess-for-grp-1"


def _engine_with_fake_runner():
    eng = ExecutionEngine.__new__(ExecutionEngine)
    eng._runner = _FakeRunner()
    return eng


def test_trigger_single_bot_workflow_returns_bot_send_result():
    eng = _engine_with_fake_runner()
    res = asyncio.new_event_loop().run_until_complete(
        eng.trigger_single_bot_workflow(task_id="t1", bot_id="b1", message="/wf 1")
    )
    assert isinstance(res, BotSendResult)
    assert res.session_id == "ws-s"


def test_start_coop_group_creates_then_fetches_session():
    eng = _engine_with_fake_runner()
    start = asyncio.new_event_loop().run_until_complete(
        eng.start_coop_group(GroupFormation(bot_ids=["b1"], collab_mode="state_machine"))
    )
    assert isinstance(start, CoopGroupStart)
    assert start.group_id == "grp-1"
    assert start.session_id == "sess-for-grp-1"
