"""Unit tests for _build_enriched_payload (Task 3) — v2 self-contained payload.

Uses a lightweight fake ticket (SimpleNamespace) — no DB. Verifies:
ticket_ref / analysis_snapshot / items 正文快照 / unevaluated / degradation.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from agentclaw.community.core.economy.governance.services.feedback_service import (
    _build_enriched_payload,
)


_NOTIFICATION_STRUCTURED = json.dumps({
    "schema_version": "v1",
    "title": "Bot 成本优化建议: TestBot",
    "meta": {
        "owner": "寿子",
        "department": "dept-x",
        "daily_tokens": "2.25 亿",
        "hit_dimensions": ["cron_token_ratio", "low_efficiency"],
        "governance_action": "notify_owner",
    },
    "problem_summary": "日均2.25亿Token。",
    "action_items": [
        {
            "index": 1,
            "action": "Cron入口增加轻量状态检查",
            "what_to_change": "pipeline入口先查ODPS分区",
            "why": "空跑消耗3M Token",
            "expected_effect": "减少空跑",
            "needs_owner_confirm": False,
        },
        {
            "index": 2,
            "action": "空结果场景提前退出Pipeline",
            "what_to_change": "阶段1后risk_events为空则跳过",
            "why": "仍尝试执行后续阶段",
            "expected_effect": "降低单次成本",
            "needs_owner_confirm": False,
        },
    ],
})


def _fake_ticket() -> SimpleNamespace:
    return SimpleNamespace(
        ticket_id="t-001",
        worker_id="168640:bot-1",
        dt_version="20260622",
        triggered_dimensions="cron_token_ratio,low_efficiency",
        hit_dimensions_count=2,
        severity="P1",
        initial_decision="actionable",
        estimated_saving_tokens=107243934,
        saving_ratio=0.4763,
        notification_structured=_NOTIFICATION_STRUCTURED,
    )


class TestBuildEnrichedPayload:
    def test_top_level_version_and_ticket_ref(self):
        out = _build_enriched_payload(
            ticket=_fake_ticket(), notification_id="n-1",
            raw_response="optimized", remark=None, repair_deadline=None,
            feedback_payload=None, source="card_callback",
            actor_id="168640", now=datetime(2026, 7, 14, 15, 38, 11),
        )
        assert out["feedback_schema_version"] == 2
        ref = out["ticket_ref"]
        assert ref["notification_id"] == "n-1"
        assert ref["worker_id"] == "168640:bot-1"
        assert ref["dt_version"] == "20260622"
        assert ref["ticket_id"] == "t-001"

    def test_analysis_snapshot_from_ticket_columns(self):
        out = _build_enriched_payload(
            ticket=_fake_ticket(), notification_id="n-1",
            raw_response="optimized", remark=None, repair_deadline=None,
            feedback_payload=None, source="card_callback",
            actor_id="168640", now=datetime(2026, 7, 14, 15, 38, 11),
        )
        snap = out["analysis_snapshot"]
        assert snap["hit_dimensions"] == ["cron_token_ratio", "low_efficiency"]
        assert snap["hit_dimensions_count"] == 2
        assert snap["governance_decision"] == "actionable"
        assert snap["governance_urgency"] == "P1"
        assert snap["expected_token_saving"] == 107243934
        assert snap["saving_ratio"] == 0.4763
        assert snap["suggestion_count"] == 2
        assert snap["governance_action"] == "notify_owner"  # from meta

    def test_items_carry_suggestion_text_snapshot(self):
        out = _build_enriched_payload(
            ticket=_fake_ticket(), notification_id="n-1",
            raw_response="optimized", remark=None, repair_deadline=None,
            feedback_payload={"items": [{"index": 1, "action": "accepted"}]},
            source="card_callback", actor_id="168640",
            now=datetime(2026, 7, 14, 15, 38, 11),
        )
        items = {it["index"]: it for it in out["items"]}
        assert items[1]["decision"] == "accepted"
        assert items[1]["suggestion_action"] == "Cron入口增加轻量状态检查"
        assert items[1]["what_to_change"] == "pipeline入口先查ODPS分区"
        assert items[1]["why"] == "空跑消耗3M Token"
        assert items[1]["expected_effect"] == "减少空跑"

    def test_unrated_item_marked_unevaluated(self):
        """User only rated item 1; item 2 → unevaluated with text snapshot."""
        out = _build_enriched_payload(
            ticket=_fake_ticket(), notification_id="n-1",
            raw_response="optimized", remark=None, repair_deadline=None,
            feedback_payload={"items": [{"index": 1, "action": "accepted"}]},
            source="card_callback", actor_id="168640",
            now=datetime(2026, 7, 14, 15, 38, 11),
        )
        items = {it["index"]: it for it in out["items"]}
        assert items[2]["decision"] == "unevaluated"
        assert items[2]["remark"] is None
        # unevaluated item still carries the suggestion text
        assert items[2]["suggestion_action"] == "空结果场景提前退出Pipeline"

    def test_overall_decisions_and_deferred(self):
        out = _build_enriched_payload(
            ticket=_fake_ticket(), notification_id="n-1",
            raw_response="need_time", remark=None,
            repair_deadline=datetime(2026, 7, 20),
            feedback_payload=None, source="card_callback",
            actor_id="168640", now=datetime(2026, 7, 14, 15, 38, 11),
        )
        assert out["overall"]["decision"] == "deferred"
        assert out["overall"]["deferred_until"] == "2026-07-20T00:00:00"
        assert out["overall"]["consistency_flag"] == "overall_dominates"

    def test_consistency_flag_partial_mix(self):
        out = _build_enriched_payload(
            ticket=_fake_ticket(), notification_id="n-1",
            raw_response="optimized", remark=None, repair_deadline=None,
            feedback_payload={"items": [
                {"index": 1, "action": "accepted"},
                {"index": 2, "action": "rejected", "remark": "no"},
            ]},
            source="card_callback", actor_id="168640",
            now=datetime(2026, 7, 14, 15, 38, 11),
        )
        assert out["overall"]["consistency_flag"] == "partial_mix"

    def test_degradation_when_notification_structured_unparseable(self):
        """Corrupt notification_structured → items degrade, user decisions kept."""
        ticket = _fake_ticket()
        ticket.notification_structured = "{not json"
        out = _build_enriched_payload(
            ticket=ticket, notification_id="n-1",
            raw_response="dispute", remark="wrong", repair_deadline=None,
            feedback_payload={"items": [{"index": 1, "action": "rejected"}]},
            source="card_callback", actor_id="168640",
            now=datetime(2026, 7, 14, 15, 38, 11),
        )
        # no suggestion full set → fallback to user items only, text=None
        assert len(out["items"]) == 1
        assert out["items"][0]["decision"] == "rejected"
        assert out["items"][0]["suggestion_action"] is None
        assert out["items"][0]["what_to_change"] is None
        # snapshot suggestion_count = len(degraded items) = 1
        assert out["analysis_snapshot"]["suggestion_count"] == 1
        # overall decision preserved
        assert out["overall"]["decision"] == "rejected"

    def test_degradation_when_action_items_not_list(self):
        """action_items 非 list(畸形 notification_structured)不崩,降级输出用户点评项。

        防御 Text 列无 schema 约束:action_items 可能是 string/dict 等非 list 值,
        旧实现对非 dict 元素 sug.get 会抛 AttributeError 穿透 resolve 的 except →
        500。isinstance 守卫拦下,走"无建议全集"降级,用户决策不丢。
        """
        ticket = _fake_ticket()
        ticket.notification_structured = json.dumps({"action_items": "not-a-list"})
        out = _build_enriched_payload(
            ticket=ticket, notification_id="n-1",
            raw_response="optimized", remark=None, repair_deadline=None,
            feedback_payload={"items": [{"index": 1, "action": "accepted"}]},
            source="card_callback", actor_id="168640",
            now=datetime(2026, 7, 14, 15, 38, 11),
        )
        # 非 list → 当作无建议全集,降级输出用户点评项
        assert len(out["items"]) == 1
        assert out["items"][0]["decision"] == "accepted"
        assert out["items"][0]["suggestion_action"] is None

    def test_degradation_when_action_items_has_non_dict_elements(self):
        """action_items list 含非 dict 元素 → 跳过非法元素,合法项仍输出。"""
        ticket = _fake_ticket()
        ticket.notification_structured = json.dumps({"action_items": [
            "bad-string",                        # 非 dict → 跳过
            123,                                  # 非 dict → 跳过
            {"index": 2, "action": "sug2", "what_to_change": "wc2"},  # 合法
        ]})
        out = _build_enriched_payload(
            ticket=ticket, notification_id="n-1",
            raw_response="optimized", remark=None, repair_deadline=None,
            feedback_payload={"items": [{"index": 2, "action": "accepted"}]},
            source="card_callback", actor_id="168640",
            now=datetime(2026, 7, 14, 15, 38, 11),
        )
        items = {it["index"]: it for it in out["items"]}
        # 两个非法元素被跳过,只剩 index=2 那条
        assert set(items.keys()) == {2}
        assert items[2]["decision"] == "accepted"
        assert items[2]["suggestion_action"] == "sug2"

    def test_pydantic_model_input_coerced(self):
        """feedback_payload may arrive as a Pydantic model (router path)."""
        from agentclaw.community.adapters.http.economy.schemas import (
            CardCallbackFeedbackItem,
            CardCallbackFeedbackPayload,
        )
        payload = CardCallbackFeedbackPayload(
            items=[CardCallbackFeedbackItem(index=1, action="accepted")],
        )
        out = _build_enriched_payload(
            ticket=_fake_ticket(), notification_id="n-1",
            raw_response="optimized", remark=None, repair_deadline=None,
            feedback_payload=payload, source="card_callback",
            actor_id="168640", now=datetime(2026, 7, 14, 15, 38, 11),
        )
        items = {it["index"]: it for it in out["items"]}
        assert items[1]["decision"] == "accepted"

    def test_meta_block(self):
        out = _build_enriched_payload(
            ticket=_fake_ticket(), notification_id="n-1",
            raw_response="optimized", remark=None, repair_deadline=None,
            feedback_payload=None, source="card_callback",
            actor_id="168640", now=datetime(2026, 7, 14, 15, 38, 11),
        )
        assert out["meta"]["response_source"] == "card_callback"
        assert out["meta"]["submitted_at"] == "2026-07-14T15:38:11"
        assert out["meta"]["actor_id"] == "168640"

    def test_output_is_json_serializable(self):
        out = _build_enriched_payload(
            ticket=_fake_ticket(), notification_id="n-1",
            raw_response="optimized", remark="好", repair_deadline=None,
            feedback_payload={"items": [{"index": 1, "action": "accepted"}]},
            source="card_callback", actor_id="168640",
            now=datetime(2026, 7, 14, 15, 38, 11),
        )
        s = json.dumps(out, ensure_ascii=False)
        assert '"feedback_schema_version": 2' in s