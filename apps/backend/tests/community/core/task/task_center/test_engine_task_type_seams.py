import asyncio

from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.execution_adapters import CoopGroupStart, CentralizedExecutionAdapter


class _FakeRunner:
    async def form_coop_group(self, gf):
        return "grp-1"
    async def get_group_session(self, group_id):
        return "sess-for-grp-1"


def _engine_with_fake_runner():
    eng = CentralizedExecutionAdapter.__new__(CentralizedExecutionAdapter)
    eng._runner = _FakeRunner()
    return eng


def test_start_coop_group_creates_then_fetches_session():
    eng = _engine_with_fake_runner()
    start = asyncio.new_event_loop().run_until_complete(
        eng.start_coop_group(GroupFormation(bot_ids=["b1"], collab_mode="state_machine"))
    )
    assert isinstance(start, CoopGroupStart)
    assert start.group_id == "grp-1"
    assert start.session_id == "sess-for-grp-1"
