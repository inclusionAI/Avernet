"""任务模式 roster 圈定派发候选的单测(对齐 plan.md §11 / backend §I-§K)。

覆盖 ``_scope_by_task_mode_roster`` 全分支(provider_id 空/bcs 缺省/roster 异常 fail-open/
roster 空→清空/roster 命中→取交),以及 ``SearchBasedDispatchStrategy.apply`` 把 roster 圈定
接入搜推(喂给 owner bot 的候选集被裁剪)。门槛默认 ``task_claim_mode=true``。
"""
from __future__ import annotations

import asyncio
import json

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, TaskInfo, TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.strategies import (
    SearchBasedDispatchStrategy, _scope_by_task_mode_roster,
)
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import BotTaskModeRoster


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _cands(*bot_ids: str) -> list[dict]:
    return [{"bot_id": bid, "bot_name": f"name_{bid}", "recommend": {"score": 0.5}} for bid in bot_ids]


def _roster(*bot_ids: str) -> list[BotTaskModeRoster]:
    return [BotTaskModeRoster(bot_id=bid, name=f"name_{bid}", env="dev",
                              task_claim_mode=True, task_dream_mode=False) for bid in bot_ids]


class _Bcs:
    """fake BcsClientPort.list_bots_by_task_modes:记录调用 + 可注入 roster / 抛错。"""

    def __init__(self, roster=None, raise_=False):
        self._roster = list(roster or [])
        self._raise = raise_
        self.calls: list[dict] = []

    async def list_bots_by_task_modes(self, *, provider_id, claim=None, dream=None, match="any"):
        self.calls.append({"provider_id": provider_id, "claim": claim, "dream": dream, "match": match})
        if self._raise:
            raise RuntimeError("bcs down")
        return list(self._roster)


class TestScopeByTaskModeRoster:
    def test_provider_id_empty_keeps_candidates(self):
        bcs = _Bcs(_roster("A"))
        out = _run(_scope_by_task_mode_roster(bcs, "", _cands("A", "B")))
        assert [c["bot_id"] for c in out] == ["A", "B"]
        assert bcs.calls == []  # provider_id 空 → 不调 bcs

    def test_bcs_none_keeps_candidates(self):
        out = _run(_scope_by_task_mode_roster(None, "p", _cands("A", "B")))
        assert [c["bot_id"] for c in out] == ["A", "B"]

    def test_intersect_keeps_only_roster_bots(self):
        bcs = _Bcs(_roster("A", "C"))
        out = _run(_scope_by_task_mode_roster(bcs, "p", _cands("A", "B", "C")))
        assert [c["bot_id"] for c in out] == ["A", "C"]  # 保候选序,取交
        assert bcs.calls == [{"provider_id": "p", "claim": True, "dream": None, "match": "any"}]

    def test_empty_roster_clears_candidates(self):
        bcs = _Bcs(_roster())
        out = _run(_scope_by_task_mode_roster(bcs, "p", _cands("A", "B")))
        assert out == []

    def test_bcs_error_fail_open_keeps_candidates(self):
        bcs = _Bcs(raise_=True)
        out = _run(_scope_by_task_mode_roster(bcs, "p", _cands("A", "B")))
        assert [c["bot_id"] for c in out] == ["A", "B"]  # 异常 fail-open 沿用候选


class _Discover:
    """同步 search_by_keyword(被 asyncio.to_thread 包);忽略 keyword 返回固定候选集。"""

    def __init__(self, items):
        self._items = items

    def search_by_keyword(self, *, keyword, user_id, top_k, min_score, filters):
        return {"items": list(self._items)}


class _Bot:
    """捕获 send_and_wait_async 的 message(即喂给 owner bot 的 prompt);返 HIT_SINGLE 指定 bot。"""

    def __init__(self, pick="A"):
        self._pick = pick
        self.captured: list[str] = []

    async def send_and_wait_async(self, *, bot_id, message, metadata=None,
                                  timeout=180.0, poll_interval=2.0):
        self.captured.append(message)
        return {"status": "COMPLETED",
                "result": {"content": json.dumps({"outcome": "HIT_SINGLE", "bot_id": self._pick})}}


def _task_info() -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id="t1", title="供应链", instruction="do"),
            context=Context(background="研究"),
            goal=Goal(objective="分析", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_channel_type="bot",
        source_channel_id="owner_bot",
    )


def _graph():
    svc = TaskGraphService()
    svc.initialize_graph(_task_info())
    return svc.query_task_dashboard("t1")


class TestStrategyAppliesRosterScoping:
    def test_scoping_on_keeps_only_roster_bot_in_prompt(self):
        bot, discover, bcs = _Bot(pick="A"), _Discover(_cands("A", "B", "C")), _Bcs(_roster("A"))
        strat = SearchBasedDispatchStrategy(bot, discover, bcs=bcs, provider_id="p")
        result = _run(strat.apply(_any_node(), _graph()))
        assert result.bot_id == "A"
        prompt = bot.captured[0]
        assert '"bot_id": "A"' in prompt or '"bot_id":"A"' in prompt
        assert '"B"' not in prompt and '"C"' not in prompt  # B/C 被 roster 圈定裁掉
        assert bcs.calls == [{"provider_id": "p", "claim": True, "dream": None, "match": "any"}]

    def test_scoping_off_when_provider_id_empty_bcs_not_called(self):
        bot, discover, bcs = _Bot(pick="C"), _Discover(_cands("A", "B", "C")), _Bcs(_roster("A"))
        strat = SearchBasedDispatchStrategy(bot, discover, bcs=bcs, provider_id="")
        _run(strat.apply(_any_node(), _graph()))
        prompt = bot.captured[0]
        assert '"A"' in prompt and '"B"' in prompt and '"C"' in prompt  # 未裁剪
        assert bcs.calls == []  # provider_id 空 → 不调 roster

    def test_scoping_fail_open_on_bcs_error(self):
        bot, discover, bcs = _Bot(pick="B"), _Discover(_cands("A", "B", "C")), _Bcs(raise_=True)
        strat = SearchBasedDispatchStrategy(bot, discover, bcs=bcs, provider_id="p")
        _run(strat.apply(_any_node(), _graph()))
        prompt = bot.captured[0]
        assert '"A"' in prompt and '"B"' in prompt and '"C"' in prompt  # fail-open 沿用候选
        assert len(bcs.calls) == 1


def _any_node():
    from agentclaw.community.core.task.domain.models import RuntimeInfo, Status, TaskNode
    return TaskNode(node_id="c1", task_id="t1", status=Status.PENDING,
                    task_spec=_task_info().task_spec, run_info=RuntimeInfo(),
                    node_run_graph=None)  # type: ignore[arg-type]
