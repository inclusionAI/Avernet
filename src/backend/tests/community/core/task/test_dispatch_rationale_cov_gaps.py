"""覆盖缺口 — ``task_dispatch/rationale.py``(批次划分漏网的最后一文件,26 行)。

* ``_summarize_join_drop(filter_ran=True)``:JOIN 掉单原因四分类
  (catalog_miss / score_below_threshold / claim_mode_off / 坏分数归 claim_mode_off)
  + 空 bot_id 跳过。
* ``_extract_skill_response_content``:非 dict run / 标量 result 两降级。
* ``_build_search_rationale``:候选列表含非 dict/无 bot_id 条目(防御 continue)、
  坏分数触发整体装配降级(返回 None + ``sr.assembly_error`` 回填)、
  敌意 sr(不可写属性)→ 再降级吞掉仅返 None(决策 #14 精神)。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.task_dispatch.rationale import (
    _build_search_rationale,
    _claim_product,
    _extract_skill_response_content,
    _summarize_join_drop,
)
from agentclaw.community.core.task.task_dispatch.strategies import (
    SearchOutcome,
    SearchResult,
)


def _sr(*, unauthorized=None, outcome=SearchOutcome.HIT_SINGLE,
        bot_id=None, group_formation=None) -> SearchResult:
    # ``unauthorized_bots`` 是 legacy claim-join 过滤时代的动态字段(生产现恒
    # filter_ran=False;True 分支为保留的分类法 API)→ 动态挂接。
    sr = SearchResult(outcome=outcome, bot_id=bot_id, group_formation=group_formation)
    sr.unauthorized_bots = unauthorized or []
    return sr


# ---------------------------------------------------------------------------
# _summarize_join_drop — filter_ran=True 的 JOIN 掉单原因分类
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_summarize_join_drop_filter_ran_classifies_all_reasons():
    candidates = [
        "not-a-dict",                                   # 预取清单脏条目 → 忽略
        {"bot_id": "in-cat:u", "recommend": {}},         # 无 score 字段
        {"bot_id": "low-score:u", "recommend": {"score": "0.1"}},
        {"bot_id": "high-score:u", "recommend": {"score": 0.9}},
        {"bot_id": "bad-score:u", "recommend": {"score": "oops"}},  # 非数值
    ]
    sr = _sr(unauthorized=[
        {"bot_id": "ghost:u"},       # 不在预取清单 → catalog_miss
        {"bot_id": ""},              # 空 bot_id → 跳过
        {"bot_id": "in-cat:u"},
        {"bot_id": "low-score:u"},
        {"bot_id": "high-score:u"},
        {"bot_id": "bad-score:u"},
    ])

    applied, dropped = _summarize_join_drop(sr, candidates, True)

    assert applied is True
    reasons = {d.bot_id: d.reason for d in dropped}
    assert reasons["ghost:u"] == "catalog_miss"
    assert reasons["in-cat:u"] == "claim_mode_off"            # 无 score 信号
    assert reasons["low-score:u"] == "score_below_threshold"  # 0.1 < 0.5
    assert reasons["high-score:u"] == "claim_mode_off"        # 0.9 ≥ 阈值
    assert reasons["bad-score:u"] == "claim_mode_off"         # 坏分数按无信号处理


@pytest.mark.unit
def test_summarize_join_drop_filter_off_flags_would_be_catalog_miss():
    sr = _sr(outcome=SearchOutcome.HIT_SINGLE, bot_id="visible:u",
             unauthorized=[])
    applied, dropped = _summarize_join_drop(sr, [{"bot_id": "elsewhere:u"}], False)
    assert applied is False
    assert [d.bot_id for d in dropped] == ["visible:u"]
    assert dropped[0].reason == "claim_filter_disabled"


@pytest.mark.unit
def test_summarize_join_drop_filter_off_multi_bots_group_formation():
    formation = SimpleNamespace(bot_ids=["m1:u", "m2:u"])
    sr = _sr(outcome=SearchOutcome.HIT_MULTI_BOTS, group_formation=formation)
    applied, dropped = _summarize_join_drop(sr, [{"bot_id": "m1:u"}], False)
    assert applied is False
    # m1 在预取清单中 → 不旗;m2 不在 → claim_filter_disabled
    assert [d.bot_id for d in dropped] == ["m2:u"]
    assert dropped[0].reason == "claim_filter_disabled"


# ---------------------------------------------------------------------------
# _extract_skill_response_content — 非 dict run / 标量 result 降级
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_skill_response_content_degrades():
    assert _extract_skill_response_content("not-a-dict") == ""
    assert _extract_skill_response_content(None) == ""
    assert _extract_skill_response_content({"result": {"content": "x"}}) == "x"
    assert _extract_skill_response_content({"result": {"content": None}}) == ""
    # result 为标量(非 dict):原样字符串化
    assert _extract_skill_response_content({"result": "raw"}) == "raw"
    assert _extract_skill_response_content({"result": None}) == ""


# ---------------------------------------------------------------------------
# _build_search_rationale — 防御装配
# ---------------------------------------------------------------------------


def _node():
    return SimpleNamespace(node_id="n1")


@pytest.mark.unit
def test_build_rationale_skips_malformed_candidates():
    sr = _sr()
    r = _build_search_rationale(
        node=_node(),
        candidates=["garbage", {"no_bot_id": 1},
                    {"bot_id": "good:u", "recommend": {"score": 0.7}}],
        sr=sr, use_skill=False, prompt_text=None, response_text=None,
        filter_ran=False, prefetch_tokens=["tok"],
    )
    assert r is not None
    assert r.decision_mode == "rule"
    assert [c.bot_id for c in r.candidates] == ["good:u"]
    assert r.candidates[0].recommend_score == 0.7


@pytest.mark.unit
def test_build_rationale_skill_mode_computes_digests():
    import hashlib
    sr = _sr()
    r = _build_search_rationale(
        node=_node(), candidates=[{"bot_id": "botA:u", "recommend": {"score": 0.6}}],
        sr=sr, use_skill=True,
        prompt_text="找一位数据分析 bot", response_text="{\"children\": []}",
        filter_ran=False, prefetch_tokens=["分析"],
    )
    assert r is not None
    assert r.decision_mode == "skill"
    assert r.skill_prompt_digest == hashlib.sha256("找一位数据分析 bot".encode()).hexdigest()
    assert r.skill_response_digest == hashlib.sha256(
        b'{"children": []}').hexdigest()
    # rule 模式无 prompt → 无摘要
    r_rule = _build_search_rationale(
        node=_node(), candidates=[], sr=_sr(), use_skill=False,
        prompt_text=None, response_text=None, filter_ran=False, prefetch_tokens=[],
    )
    assert r_rule.skill_prompt_digest is None
    assert r_rule.skill_response_digest is None


@pytest.mark.unit
def test_build_rationale_bad_score_degrades_to_none_with_assembly_error():
    sr = _sr()
    r = _build_search_rationale(
        node=_node(),
        candidates=[{"bot_id": "b:u", "recommend": {"score": "not-a-number"}}],
        sr=sr, use_skill=False, prompt_text=None, response_text=None,
        filter_ran=False, prefetch_tokens=[],
    )
    assert r is None
    assert sr.assembly_error is not None
    assert sr.assembly_error.startswith("rationale_assembly_failed:")
    assert "ValueError" in sr.assembly_error or "TypeError" in sr.assembly_error


@pytest.mark.unit
def test_build_rationale_hostile_sr_still_returns_none_without_side_effects():
    # 敌意 sr(object() 属性不可写):assembly_error 回填抛 AttributeError →
    # 再降级吞掉,仅返回 None,主流程不受影响(决策 #14)。
    hostile_sr = object()
    r = _build_search_rationale(
        node=_node(),
        candidates=[{"bot_id": "b:u", "recommend": {"score": {"nested": "dict"}}}],
        sr=hostile_sr, use_skill=False, prompt_text=None, response_text=None,
        filter_ran=False, prefetch_tokens=[],
    )
    assert r is None
    # 敌意对象未被改动)object 无 __dict__,赋值必然抛 → 无副作用
    assert not hasattr(hostile_sr, "assembly_error")


# ---------------------------------------------------------------------------
# _claim_product — 归一(既有测试未覆盖到的形态顺手补)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_claim_product_normalizes_composite_and_plain_ids():
    assert _claim_product("botA:user1") == "botA"
    assert _claim_product("botA") == "botA"
    assert _claim_product("") == ""
    assert _claim_product(None) == ""