# tests/community/core/task/task_runner/integration/test_bbs_runner.py
import asyncio
import json
from unittest.mock import MagicMock

from agentclaw.community.core.task.task_runner.integration.bbs_runner import notify


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _execution_graph(task_id="t1"):
    g = MagicMock()
    g.task_id = task_id
    g.tasks = []
    return g


class _FakeBot:
    def __init__(self, rates):
        """rates: {bot_id: completion_rate or None (None=simulate error)}"""
        self._rates = rates
        self.sent_messages: list[tuple] = []

    async def send_and_wait_async(self, *, bot_id, message, metadata=None, timeout=180.0, poll_interval=2.0):
        rate = self._rates.get(bot_id)
        if rate is None:
            raise RuntimeError("bot error")
        return {"status": "COMPLETED",
                "result": {"content": json.dumps({"completion_rate": rate})}}

    async def send_message(self, *, bot_id, message, metadata):
        self.sent_messages.append((bot_id, message, metadata))
        from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult
        return BotSendResult(run_id=f"r_{bot_id}", session_id=None)


class _FakeBcn:
    """Fake BcnService.list_bots_by_task_modes: sync (bbs_runner 经 asyncio.to_thread 调)，记录断言。"""

    def __init__(self, roster):
        self._roster = roster

    def list_bots_by_task_modes(self, *, claim=None, dream=None, match="any"):
        assert claim is True
        assert dream is True
        assert match == "all"
        return list(self._roster)


def _roster(*bot_ids: str) -> list[dict]:
    return [
        {
            "bot_id": bot_id,
            "name": bot_id,
            "env": "local",
            "task_claim_mode": True,
            "task_dream_mode": True,
        }
        for bot_id in bot_ids
    ]


class _FakeGraph:
    def __init__(self):
        self.claimed = None
        self.cleared = False

    def claim_bbs_owner(self, task_id, bot_id):
        self.claimed = bot_id
        return MagicMock(success=True)

    def update_task_node_info(self, patch):
        if patch.extend_props_patch and patch.extend_props_patch.get("bbs_owner") is None:
            self.cleared = True


def test_notify_selects_highest_completion_rate_and_claims_and_sends():
    """bid→select→claim→send: picks highest completion_rate, claims root, sends task message."""
    roster = _roster("A", "B", "C")
    bot = _FakeBot(rates={"A": 50, "B": 90, "C": 70})
    bcn = _FakeBcn(roster)
    graph = _FakeGraph()
    g = _execution_graph()

    _run(notify(g, bcn=bcn, bot=bot, graph=graph, backend_url="http://localhost:8888", skill_name="bbs-relay-single-task"))

    assert graph.claimed == "B"  # highest completion_rate
    assert len(bot.sent_messages) == 1
    msg_bot, msg_text, msg_meta = bot.sent_messages[0]
    assert msg_bot == "B"
    assert "bbs-relay-single-task" in msg_text
    assert "t1" in msg_text
    assert "http://localhost:8888" in msg_text
    assert "B" in msg_text  # winner's own bot_id
    assert not graph.cleared  # send succeeded, claim not rolled back


def test_notify_empty_roster_returns_silently():
    """空 roster → 静默返回(不 claim、不 send)。"""
    bot = _FakeBot(rates={})
    bcn = _FakeBcn([])
    graph = _FakeGraph()
    _run(notify(_execution_graph("t2"), bcn=bcn, bot=bot, graph=graph, backend_url="http://x", skill_name="s"))
    assert graph.claimed is None
    assert bot.sent_messages == []


def test_notify_all_bids_failed_returns_silently():
    """全 bid 失败/超时 → 静默返回。"""
    roster = _roster("A")
    bot = _FakeBot(rates={"A": None})  # None → raises
    bcn = _FakeBcn(roster)
    graph = _FakeGraph()
    _run(notify(_execution_graph("t3"), bcn=bcn, bot=bot, graph=graph, backend_url="http://x", skill_name="s"))
    assert graph.claimed is None


def test_notify_send_message_failure_rolls_back_claim():
    """send_message 失败 → clear bbs_owner(回收 claim)。"""
    roster = _roster("W")

    class _BotSendFails(_FakeBot):
        async def send_message(self, *, bot_id, message, metadata):
            raise RuntimeError("send failed")

    bot = _BotSendFails(rates={"W": 80})
    bcn = _FakeBcn(roster)
    graph = _FakeGraph()
    _run(notify(_execution_graph("t4"), bcn=bcn, bot=bot, graph=graph, backend_url="http://x", skill_name="s"))
    assert graph.claimed == "W"
    assert graph.cleared  # bbs_owner cleared


def test_notify_bcn_none_returns_silently():
    _run(notify(_execution_graph("t5"), bcn=None, bot=_FakeBot({}), graph=_FakeGraph(), backend_url="http://x"))
    # no exception, no claim
