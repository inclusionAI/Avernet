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
    _coverage_route,
    _coverage_post_dispatch,
    _join_candidates_pool,
    _offpath_normal,
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
        self.call_args = []

    def list_bots_by_task_modes(
        self, *, claim=None, dream=None, match="any", visibility=None
    ):
        self.calls += 1
        self.call_args.append(
            {
                "claim": claim,
                "dream": dream,
                "match": match,
                "visibility": visibility,
            }
        )
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

# Rule 模式动态 claim 池(模拟 ``task_claim_mode=true & visibility=public`` 的 product:owner 名单)。
RULE_POOL = ["rule-a:1", "rule-b:2", "rule-c:3", "rule-d:4"]



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


def test_join_on_queries_claim_enabled_public_bots_with_all_match():
    b = _Bcn(CLAIM_ON)
    r = _run(_strat(b, _Gate(True))._apply_claim_join(_single("A"), CANDS))
    assert r.outcome == SearchOutcome.HIT_SINGLE and r.bot_id == "A"
    assert b.call_args == [
        {
            "claim": True,
            "dream": None,
            "match": "all",
            "visibility": "public",
        }
    ]


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
    """规则派发(动态 claim 池的 ``product:owner``)bot 不在 prefetch 候选内时,
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


# ===== off-path 正常派发(_offpath_normal): join+candidate-count,无随机/无回退/无定制 =====


def test_offpath_empty_joined_misses_without_fallback():
    """joined 空(候选∩池=空)→ MISS(no_candidates),不回退池。"""
    result = _offpath_normal([])

    assert result.outcome == SearchOutcome.MISS
    assert result.miss_reason == "no_candidates"
    assert result.group_formation is None


def test_offpath_one_or_two_joined_single_bot():
    """len(joined)≤2 → HIT_SINGLE(取 joined[0],owner 从 :owner 后缀解析)。"""
    r1 = _offpath_normal(["rule-a:1"])
    assert r1.outcome == SearchOutcome.HIT_SINGLE
    assert r1.bot_id == "rule-a:1"
    assert r1.owner_id == "1"

    r2 = _offpath_normal(["rule-a:1", "rule-b:2"])
    assert r2.outcome == SearchOutcome.HIT_SINGLE
    assert r2.bot_id == "rule-a:1"


def test_offpath_three_or_more_joined_manager_worker_group_capped_at_three():
    """len(joined)≥3 → HIT_MULTI_BOTS(前 3 个,manager_worker),确定性不随机。"""
    result = _offpath_normal(
        ["rule-a:1", "rule-b:2", "rule-c:3", "rule-d:4", "rule-e:5"]
    )

    assert result.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert result.group_formation.bot_ids == ["rule-a:1", "rule-b:2", "rule-c:3"]
    assert result.group_formation.collab_mode == "manager_worker"
    assert [m["role"] for m in result.group_formation.members_info] == [
        "manager", "worker", "worker"
    ]


# ===== _join_candidates_pool: 候选∩池 by product =====


def test_join_candidates_pool_intersects_by_product_in_candidate_order():
    """候选 product ∩ 池,按候选出现顺序(score 降序)返回池条目(product:owner),去重。"""
    joined = _join_candidates_pool(
        [{"bot_id": "rule-b"}, {"bot_id": "rule-a"}, {"bot_id": "nobody"}, {"bot_id": "rule-a"}],
        RULE_POOL,
    )

    assert joined == ["rule-b:2", "rule-a:1"]


def test_join_candidates_pool_empty_when_no_intersection():
    """候选与池无交集 → 空列表(off-path 兜底 MISS,不回退池)。"""
    assert _join_candidates_pool([{"bot_id": "x"}, {"bot_id": "y"}], RULE_POOL) == []


# ===== on-path 模式覆盖路由(_coverage_route): single→group→bbs =====


def test_coverage_route_forces_single_first():
    """covered={} & joined 非空 → 强制 single(joined[0]);pool 不参与。"""
    result = _coverage_route(["rule-a:1", "rule-b:2"], [], set())

    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "rule-a:1"


def test_coverage_route_forces_group_when_single_covered():
    """covered={single} & len(joined)≥2 → 强制 group(前 3)。"""
    result = _coverage_route(["rule-a:1", "rule-b:2", "rule-c:3"], [], {"single"})

    assert result.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert result.group_formation.bot_ids == ["rule-a:1", "rule-b:2", "rule-c:3"]


def test_coverage_route_forces_bbs_miss_when_single_and_group_covered():
    """covered={single,group} → 强制 MISS(mode_coverage_bbs) 升根级 BBS。"""
    result = _coverage_route(["rule-a:1"], [], {"single", "group"})

    assert result.outcome == SearchOutcome.MISS
    assert result.miss_reason == "mode_coverage_bbs"


def test_coverage_route_single_falls_back_to_pool_when_joined_empty():
    """join 为空 → claim+public 池兜底命中 single(保证模式可覆盖,不卡死)。"""
    result = _coverage_route([], ["pool-a:1", "pool-b:2"], set())

    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "pool-a:1"


def test_coverage_route_group_falls_back_to_pool_when_joined_insufficient():
    """joined 仅 1 不足 group → pool 兜底命中 group(前 3)。"""
    result = _coverage_route(["only:1"], ["pool-a:1", "pool-b:2", "pool-c:3"], {"single"})

    assert result.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert result.group_formation.bot_ids == ["pool-a:1", "pool-b:2", "pool-c:3"]


def test_coverage_route_bbs_when_joined_and_pool_both_empty():
    """joined 与 pool 均空 → single/group 跳过 → bbs MISS(恒可行)。"""
    result = _coverage_route([], [], set())

    assert result.outcome == SearchOutcome.MISS
    assert result.miss_reason == "mode_coverage_bbs"


def test_coverage_route_returns_none_when_all_covered():
    """全覆盖 → None(调用方兜底走 off-path 正常派发)。"""
    assert _coverage_route(["rule-a:1", "rule-b:2"], [], {"single", "group", "bbs"}) is None


# ===== on-path 全覆盖后兜底派发(_coverage_post_dispatch): joined/pool 兜底 + single/group 平均随机 =====


def test_coverage_post_dispatch_single_when_random_below_half(monkeypatch):
    """random<0.5 → HIT_SINGLE(joined[0],owner 解析);不走 group。"""
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.random",
        lambda: 0.3,
    )
    result = _coverage_post_dispatch(["rule-a:1", "rule-b:2", "rule-c:3"], [])

    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "rule-a:1"
    assert result.owner_id == "1"


def test_coverage_post_dispatch_group_when_random_at_or_above_half(monkeypatch):
    """random≥0.5 & len(bots)≥2 → HIT_MULTI_BOTS(前 3,manager_worker)。"""
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.random",
        lambda: 0.6,
    )
    result = _coverage_post_dispatch(["rule-a:1", "rule-b:2", "rule-c:3"], [])

    assert result.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert result.group_formation.bot_ids == ["rule-a:1", "rule-b:2", "rule-c:3"]


def test_coverage_post_dispatch_pool_fallback_when_joined_empty(monkeypatch):
    """joined 空 → pool 兜底;random≥0.5 & len(pool)≥2 → group(pool 前 3)。"""
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.random",
        lambda: 0.6,
    )
    result = _coverage_post_dispatch([], ["pool-a:1", "pool-b:2", "pool-c:3"])

    assert result.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert result.group_formation.bot_ids == ["pool-a:1", "pool-b:2", "pool-c:3"]


def test_coverage_post_dispatch_single_demote_when_fewer_than_two_bots(monkeypatch):
    """random≥0.5 但 len(bots)<2 → 降级 HIT_SINGLE(bots[0])。"""
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.random",
        lambda: 0.9,
    )
    result = _coverage_post_dispatch(["only:1"], [])

    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "only:1"
    assert result.owner_id == "1"


def test_coverage_post_dispatch_miss_when_joined_and_pool_both_empty():
    """joined 与 pool 均空 → MISS(no_candidates)(交现有 MISS→HUNG→BBS)。"""
    result = _coverage_post_dispatch([], [])

    assert result.outcome == SearchOutcome.MISS
    assert result.miss_reason == "no_candidates"




def test_load_rule_test_pool_returns_claim_enabled_bot_ids():
    """``_load_rule_test_pool`` 动态取 task_claim_mode=true & visibility=public 的 product:owner。"""
    bcn = _Bcn([{"bot_id": "A:Ao"}, {"bot_id": "B:Bo"}])

    pool = _run(_strat(bcn)._load_rule_test_pool())

    assert pool == ["A:Ao", "B:Bo"]
    assert bcn.call_args[-1] == {
        "claim": True,
        "dream": None,
        "match": "all",
        "visibility": "public",
    }


def test_load_rule_test_pool_empty_when_bcn_missing():
    """bcn 未注入(stub/测试) → 空池,由 _offpath_normal 兜底 MISS(no_candidates)。"""
    assert _run(_strat(None)._load_rule_test_pool()) == []


def test_load_rule_test_pool_empty_on_roster_failure():
    """BCS roster 取异常 → best-effort 空池,不阻断派发。"""
    bcn = _Bcn(exc=RuntimeError("roster down"))

    assert _run(_strat(bcn)._load_rule_test_pool()) == []


