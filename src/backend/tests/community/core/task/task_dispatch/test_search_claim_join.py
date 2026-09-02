"""JOIN post-filter 契约测试(PRD 6 条): ``SearchBasedDispatchStrategy._apply_claim_join``。

直接单测 ``_apply_claim_join``(隔离 LLM/discover),对照:
JOIN 开: ①单∈→HIT ②单∉→MISS ③多全∈→HIT_MULTI 全 ④多全∉→MISS
         ⑤多部分∈→HIT_MULTI 命中子集 ⑥多部分∈剩1→降 HIT_SINGLE ⑦原 MISS→MISS ⑧HIT_GROUP 不动
JOIN 关 / bcn 缺失 / 名册空 / 取名册失败 → fail-open 透传。
"""

from __future__ import annotations

import asyncio

from agentclaw.community.core.task.task_dispatch.strategies import (
    GroupFormation,
    SearchBasedDispatchStrategy,
    SearchOutcome,
    SearchResult,
    _rule_based_search_result,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Gate:
    def __init__(self, enabled: bool) -> None:
        self._e = enabled

    def is_enabled(self) -> bool:
        return self._e


class _Bcn:
    def __init__(self, entries=None, exc=None) -> None:
        self._entries, self._exc = entries, exc
        self.calls = 0

    def list_bots_by_task_modes(self, *, claim=None, dream=None, match="any"):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._entries or []


# claim_on 名单:A, B(bcs 形式 {product}:{owner})
CLAIM_ON = [{"bot_id": "A:Ao"}, {"bot_id": "B:Bo"}]
CANDS = [
    {"bot_id": "A", "bot_name": "AN", "owner_id": "Ao", "owner_name": "AON"},
    {"bot_id": "B", "bot_name": "BN", "owner_id": "Bo", "owner_name": "BON"},
    {"bot_id": "X", "bot_name": "XN", "owner_id": "Xo", "owner_name": "XON"},
]


def _single(bot_id):
    return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id=bot_id)


def _multi(bot_ids):
    return SearchResult(
        outcome=SearchOutcome.HIT_MULTI_BOTS,
        group_formation=GroupFormation(bot_ids=list(bot_ids), collab_mode="chat"),
    )


def _miss(reason="r"):
    return SearchResult(outcome=SearchOutcome.MISS, miss_reason=reason)


def _group(gid):
    return SearchResult(outcome=SearchOutcome.HIT_GROUP, group_id=gid)


def _strat(bcn=None, gate=None):
    return SearchBasedDispatchStrategy(bcn=bcn, join_gate=gate)


def test_join_on_single_in_keeps():
    r = _run(_strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(_single("A"), CANDS))
    assert r.outcome == SearchOutcome.HIT_SINGLE and r.bot_id == "A"
    assert r.unauthorized_bots is None


def test_join_on_single_bcs_form_normalizes():
    r = _run(
        _strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(_single("B:Bo"), CANDS)
    )
    assert r.outcome == SearchOutcome.HIT_SINGLE and r.bot_id == "B:Bo"


def test_join_on_single_out_miss():
    r = _run(_strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(_single("X"), CANDS))
    assert r.outcome == SearchOutcome.MISS and r.miss_reason == "claim_mode_off"
    assert r.unauthorized_bots == [
        {"bot_id": "X", "owner_user_id": "Xo", "reason": "claim_mode_off"},
    ]


def test_join_on_multi_all_in_keeps():
    r = _run(
        _strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(_multi(["A", "B"]), CANDS)
    )
    assert r.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert r.group_formation.bot_ids == ["A", "B"]
    assert r.unauthorized_bots is None


def test_join_on_multi_none_in_miss():
    r = _run(
        _strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(_multi(["X", "Y"]), CANDS)
    )
    assert r.outcome == SearchOutcome.MISS and r.miss_reason == "claim_mode_off_multi"
    assert r.unauthorized_bots == [
        {"bot_id": "X", "owner_user_id": "Xo", "reason": "claim_mode_off"},
        {"bot_id": "Y", "owner_user_id": "", "reason": "claim_mode_off"},
    ]


def test_join_on_single_out_rule_pool_owner_from_suffix():
    """规则派发(``_RULE_TEST_BOT_POOL`` 的 ``product:owner``)bot 不在 prefetch 候选内时,
    unauthorized_bots 的 owner_user_id 从 bot_id 的 ``:owner`` 后缀解析,不再落空串。"""
    r = _run(
        _strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(
            _single("20260824_nwlj25w6:35983"), CANDS
        )
    )
    assert r.outcome == SearchOutcome.MISS and r.miss_reason == "claim_mode_off"
    assert r.unauthorized_bots == [
        {"bot_id": "20260824_nwlj25w6", "owner_user_id": "35983", "reason": "claim_mode_off"},
    ]


def test_join_on_multi_rule_pool_owner_from_suffix_mixed():
    """多候选混入规则池 bot:候选内(带 owner_id)走候选回查;候选外的 product:owner 走后缀解析。"""
    r = _run(
        _strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(
            _multi(["default:146836", "20260824_nwlj25w6:35983", "X"]), CANDS
        )
    )
    assert r.outcome == SearchOutcome.MISS and r.miss_reason == "claim_mode_off_multi"
    assert r.unauthorized_bots == [
        {"bot_id": "default", "owner_user_id": "146836", "reason": "claim_mode_off"},
        {"bot_id": "20260824_nwlj25w6", "owner_user_id": "35983", "reason": "claim_mode_off"},
        {"bot_id": "X", "owner_user_id": "Xo", "reason": "claim_mode_off"},
    ]


def test_join_on_multi_partial_keeps_subset():
    r = _run(
        _strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(
            _multi(["A", "X", "B"]), CANDS
        )
    )
    assert r.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert r.group_formation.bot_ids == ["A", "B"]
    assert r.unauthorized_bots == [
        {"bot_id": "X", "owner_user_id": "Xo", "reason": "claim_mode_off"},
    ]


def test_join_on_multi_partial_to_single_demotes():
    r = _run(
        _strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(_multi(["A", "X"]), CANDS)
    )
    assert r.outcome == SearchOutcome.HIT_SINGLE
    assert r.bot_id == "A" and r.bot_name == "AN" and r.owner_id == "Ao"
    assert r.owner_name == "AON"
    assert r.unauthorized_bots == [
        {"bot_id": "X", "owner_user_id": "Xo", "reason": "claim_mode_off"},
    ]


def test_join_on_miss_passthrough():
    r = _run(_strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(_miss("foo"), CANDS))
    assert r.outcome == SearchOutcome.MISS and r.miss_reason == "foo"


def test_join_on_group_untouched():
    r = _run(_strat(_Bcn(CLAIM_ON), _Gate(True))._apply_claim_join(_group("g1"), CANDS))
    assert r.outcome == SearchOutcome.HIT_GROUP and r.group_id == "g1"


def test_join_off_passthrough():
    b = _Bcn(CLAIM_ON)
    r = _run(_strat(b, _Gate(False))._apply_claim_join(_single("X"), CANDS))
    assert r.outcome == SearchOutcome.HIT_SINGLE and r.bot_id == "X"
    assert b.calls == 0  # 开关关不取名册


def test_bcn_none_passthrough():
    r = _run(_strat(None, _Gate(True))._apply_claim_join(_single("X"), CANDS))
    assert r.outcome == SearchOutcome.HIT_SINGLE and r.bot_id == "X"


def test_gate_none_passthrough():
    r = _run(_strat(_Bcn(CLAIM_ON), None)._apply_claim_join(_single("X"), CANDS))
    assert r.outcome == SearchOutcome.HIT_SINGLE and r.bot_id == "X"


def test_bcn_raises_fail_open():
    b = _Bcn(exc=RuntimeError("bcs down"))
    r = _run(_strat(b, _Gate(True))._apply_claim_join(_single("X"), CANDS))
    assert r.outcome == SearchOutcome.HIT_SINGLE and r.bot_id == "X"


def test_empty_roster_fail_open():
    b = _Bcn([])
    r = _run(_strat(b, _Gate(True))._apply_claim_join(_single("X"), CANDS))
    # 空名册(BCS 返回 [])按 fail-open 透传,等价于 JOIN 关 → 原 HIT_SINGLE 不降级
    assert r.outcome == SearchOutcome.HIT_SINGLE and r.bot_id == "X"


def test_rule_dispatch_single_candidate_random_miss_allows_bbs_escalation(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.random",
        lambda: 0.39,
    )

    result = _rule_based_search_result([{"bot_id": "candidate-a"}])

    assert result.outcome == SearchOutcome.MISS
    assert result.bot_id is None
    assert result.group_formation is None
    assert result.miss_reason == "rule_single_candidate_random_miss"


def test_rule_dispatch_multi_candidate_caps_fixed_pool_group_at_three(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.random",
        lambda: 0.99,
    )
    observed = {}

    def sample(values, count):
        observed["count"] = count
        return list(values)[:count]

    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.sample",
        sample,
    )

    result = _rule_based_search_result(
        [{"bot_id": f"candidate-{index}"} for index in range(8)]
    )

    assert result.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert observed["count"] == 3
    assert len(result.group_formation.bot_ids) == 3
    assert len(result.group_formation.members_info) == 3


def test_rule_dispatch_single_candidate_keeps_hit_after_40_percent_threshold(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.random",
        lambda: 0.4,
    )
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.sample",
        lambda values, count: list(values)[:count],
    )

    result = _rule_based_search_result([{"bot_id": "candidate-a"}])

    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id in {
        "20260825_bohtfhe6:35983",
        "default:35983",
        "default:146836",
        "20260825_p8e63hms:35983",
        "20260823_1c4am0ei:146836",
        "20260826_q3tbj2da:146836",
        "20260826_fmszf5aq:146836",
        "20260826_20rphqo0:146836",
    }
