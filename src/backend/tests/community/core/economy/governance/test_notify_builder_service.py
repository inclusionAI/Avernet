"""Tests for notify_builder_service — TC-card notification builders.

The target module is a set of pure render/build functions (no DB). Each test
constructs explicit inputs and asserts on real rendered output, covering
the simplified reason builder, structured-JSON parsing, per-field
truncation, token formatting, and the base64 deep-link builder.

Legacy verbose templates (render_governance_notify, render_governance_remind,
_format_action_items, _urgency_from_structured, _resolve_action_link) have
been removed; their tests are also removed.
"""
from __future__ import annotations

import base64
import json
import urllib.parse

from agentclaw.community.core.economy.governance.services import notify_render_service as nb


# ── _parse_notification_structured ──────────────────────────────


class TestParseNotificationStructured:
    def test_none_input_returns_none(self):
        assert nb._parse_notification_structured(None) is None

    def test_empty_string_returns_none(self):
        assert nb._parse_notification_structured("") is None

    def test_invalid_json_returns_none(self):
        assert nb._parse_notification_structured("{not json") is None

    def test_json_without_meta_returns_none(self):
        assert nb._parse_notification_structured('{"title": "x"}') is None

    def test_json_list_returns_none(self):
        # valid JSON but not a dict → None
        assert nb._parse_notification_structured("[1, 2, 3]") is None

    def test_valid_dict_string(self):
        out = nb._parse_notification_structured('{"meta": {"owner": "a"}}')
        assert out == {"meta": {"owner": "a"}}

    def test_dict_passthrough(self):
        payload = {"meta": {"owner": "a"}}
        assert nb._parse_notification_structured(payload) is payload


# ── _format_hit_dimensions ──────────────────────────────────────


class TestFormatHitDimensions:
    def test_python_list_translated(self):
        out = nb._format_hit_dimensions(["cron_high_freq", "low_efficiency"])
        assert out == "Cron 高频调用 · 低效率"

    def test_json_list_string(self):
        out = nb._format_hit_dimensions('["quality_defect"]')
        assert out == "质量缺陷"

    def test_json_scalar_string_kept_as_single(self):
        # valid JSON but not a list → the *raw* string (incl. quotes) is
        # kept as a single item and translated as-is (no map entry).
        out = nb._format_hit_dimensions('"high_error"')
        assert out == '"high_error"'

    def test_comma_separated_string(self):
        out = nb._format_hit_dimensions("cron_high_freq, low_utility")
        assert out == "Cron 高频调用 · 低效用"

    def test_auxiliary_dimension_filtered(self):
        out = nb._format_hit_dimensions(["cron_token_ratio", "high_error"])
        assert out == "高错误率"

    def test_unknown_key_kept_as_is(self):
        out = nb._format_hit_dimensions(["some_unknown"])
        assert out == "some_unknown"

    def test_non_string_non_list_input(self):
        out = nb._format_hit_dimensions(123)
        assert out == "123"


# ── _fmt_tokens ─────────────────────────────────────────────────


class TestFmtTokens:
    def test_billions(self):
        assert nb._fmt_tokens(2_0000_0000) == "2.00 亿"

    def test_wan(self):
        assert nb._fmt_tokens(30000) == "3 万"

    def test_small_number_comma(self):
        assert nb._fmt_tokens(1234) == "1,234"

    def test_non_numeric_becomes_zero(self):
        assert nb._fmt_tokens("abc") == "0"


# ── _shorten ────────────────────────────────────────────────────


class TestShorten:
    def test_none_returns_empty(self):
        assert nb._shorten(None, 10) == ""

    def test_under_limit_unchanged(self):
        assert nb._shorten("hi", 10) == "hi"

    def test_over_limit_truncated_with_ellipsis(self):
        out = nb._shorten("abcdefghij", 5)
        assert out == "abcd…"
        assert len(out) == 5

    def test_strips_whitespace(self):
        assert nb._shorten("  hello  ", 10) == "hello"


# ── _extract_primary_suggestion ─────────────────────────────────


class TestExtractPrimarySuggestion:
    def test_empty_returns_empty(self):
        assert nb._extract_primary_suggestion(None) == ""
        assert nb._extract_primary_suggestion([]) == ""

    def test_title_and_desc_joined(self):
        out = nb._extract_primary_suggestion(
            [{"title": "缓存", "description": "加缓存"}]
        )
        assert out == "缓存：加缓存"

    def test_action_and_what_to_change_aliases(self):
        out = nb._extract_primary_suggestion(
            [{"action": "拆分", "what_to_change": "拆成两步"}]
        )
        assert out == "拆分：拆成两步"

    def test_only_title(self):
        out = nb._extract_primary_suggestion([{"title": "only"}])
        assert out == "only"

    def test_only_desc(self):
        out = nb._extract_primary_suggestion([{"description": "d"}])
        assert out == "d"

    def test_long_title_desc_truncated_to_120(self):
        long_desc = "x" * 200
        out = nb._extract_primary_suggestion(
            [{"title": "t", "description": long_desc}]
        )
        assert len(out) == 120
        assert out.endswith("…")


# ── build_governance_reason ─────────────────────────────────────


class TestBuildGovernanceReason:
    def test_minimal_fallback_defaults_to_bot(self):
        out = nb.build_governance_reason()
        assert "**「Bot」**" in out
        assert "近期存在可优化的 Token 消耗" in out

    def test_structured_full(self):
        structured = {
            "meta": {
                "botName": "CostBot",
                "hit_dimensions": ["low_efficiency", "high_error"],
                "daily_tokens": 50000,
            },
            "problem_summary": "调用过于频繁导致成本偏高",
        }
        out = nb.build_governance_reason(
            notification_structured=structured,
            dt_version="20260623",
        )
        assert "**「CostBot」**" in out
        assert "**命中维度**：低效率 · 高错误率" in out
        assert "**日均消耗**：5 万 Token" in out
        assert "**采样日期**：2026-06-23" in out
        assert "**主要问题**：调用过于频繁导致成本偏高" in out

    def test_overdue_prefix(self):
        out = nb.build_governance_reason(bot_name="B", overdue_days=4)
        assert "⚠️ 此通知已超期 4 天未处理" in out

    def test_daily_tokens_string_variant(self):
        structured = {"meta": {"botName": "B", "dailyTokenUsage": "  10万  "}}
        out = nb.build_governance_reason(notification_structured=structured)
        assert "**日均消耗**：10万 Token" in out

    def test_title_from_structured_title(self):
        # no botName/bot_name → falls to structured title
        structured = {"meta": {}, "title": "FromTitle"}
        out = nb.build_governance_reason(notification_structured=structured)
        assert "**「FromTitle」**" in out

    def test_fallback_dimensions_and_problem_no_structured(self):
        out = nb.build_governance_reason(
            bot_name="FB",
            hit_dimensions="cron_high_freq",
            task_summary="问题描述",
        )
        assert "**命中维度**：Cron 高频调用" in out
        assert "**主要问题**：问题描述" in out

    def test_dt_version_non_8_len_passthrough(self):
        out = nb.build_governance_reason(bot_name="B", dt_version="2026")
        assert "**采样日期**：2026" in out

    def test_no_optional_blocks_when_empty(self):
        out = nb.build_governance_reason(bot_name="B")
        assert "命中维度" not in out
        assert "日均消耗" not in out
        assert "采样日期" not in out
        assert "主要问题" not in out

    def test_max_length_truncation(self):
        # daily_tokens is the one un-shortened string field, so an
        # oversized value forces the final _MAX_REASON_LENGTH cap (line 526).
        structured = {
            "meta": {"botName": "B", "dailyTokenUsage": "万" * 2000},
            "problem_summary": "问" * 50,
        }
        out = nb.build_governance_reason(
            notification_structured=structured,
        )
        # result is bounded by _MAX_REASON_LENGTH and ends with ellipsis
        assert len(out) == nb._MAX_REASON_LENGTH
        assert out.endswith("…")


# ── build_card_notification_data ────────────────────────────────


class TestBuildCardNotificationData:
    def test_fallback_minimal_shape(self):
        out = nb.build_card_notification_data(
            notification_structured=None,
            notification_id="nid-1",
            bot_name="FBot",
            bot_id="b1",
            governance_max_priority="P1",
            saving_ratio=0.2,
            dt_version="20260629",
            expected_token_saving=999,
        )
        assert out["notification_id"] == "nid-1"
        assert out["noticeId"] == "nid-1"
        assert out["title"] == "FBot"
        assert out["botId"] == "b1"
        assert out["severity"] == "P1"
        assert out["optimizationPotential"] == 999
        assert out["optimizationRate"] == "20.0%"
        assert out["statDate"] == "20260629"
        assert out["meta"] == {}
        assert out["optimizationSuggestions"] == []

    def test_fallback_title_default_when_no_botname(self):
        out = nb.build_card_notification_data(
            notification_structured=None,
            notification_id="nid",
        )
        assert out["title"] == "成本优化通知"
        assert out["optimizationRate"] == ""

    def test_invalid_json_degrades_to_fallback(self):
        out = nb.build_card_notification_data(
            notification_structured="{bad json",
            notification_id="nid",
            bot_name="B",
        )
        assert out["meta"] == {}
        assert out["action_items"] == []

    def test_non_dict_json_degrades_to_fallback(self):
        out = nb.build_card_notification_data(
            notification_structured="[1,2]",
            notification_id="nid",
        )
        assert out["title"] == "成本优化通知"

    def test_structured_full_contract(self):
        structured = json.dumps(
            {
                "schema_version": "v2",
                "title": "TitleBot",
                "problem_summary": "问题",
                "has_skill_section": True,
                "disclaimer": "免责",
                "degraded": False,
                "meta": {
                    "owner": "carol",
                    "botName": "MetaBot",
                    "severity": "P0",
                    "daily_tokens_raw": 88888,
                    "saving_ratio": 0.33,
                    "organization": "TeamX",
                },
                "action_items": [
                    {"action": "act1", "what_to_change": "change1"},
                    {"title": "t2", "description": "d2"},
                ],
                "extra_key": "kept",
            }
        )
        out = nb.build_card_notification_data(
            notification_structured=structured,
            notification_id="nid-9",
            bot_id="bX",
            expected_token_saving=1234,
        )
        assert out["schema_version"] == "v2"
        assert out["title"] == "TitleBot"
        assert out["severity"] == "P0"
        assert out["botName"] == "MetaBot"
        assert out["botId"] == "bX"
        assert out["owner"] == "carol"
        assert out["organization"] == "TeamX"
        assert out["dailyTokenUsage"] == 88888
        assert out["optimizationPotential"] == 1234
        assert out["optimizationRate"] == "33.0%"
        assert out["optimizationSuggestions"] == [
            {"title": "act1", "description": "change1"},
            {"title": "t2", "description": "d2"},
        ]
        # unknown keys preserved verbatim
        assert out["extra_key"] == "kept"
        assert out["feedback"]["isAdopted"] is None

    def test_structured_severity_and_org_fallbacks(self):
        # severity falls through to optimization_summary; org via department
        structured = {
            "meta": {
                "optimization_summary": "SUMSEV",
                "department": "DeptY",
                "dailyTokenUsage": 42,
                "optimizationRate": "9%",
            },
        }
        out = nb.build_card_notification_data(
            notification_structured=structured,
            notification_id="nid",
            owner_id="fallback-owner",
        )
        assert out["severity"] == "SUMSEV"
        assert out["organization"] == "DeptY"
        assert out["dailyTokenUsage"] == 42
        assert out["optimizationRate"] == "9%"
        # owner defaults to owner_id when meta lacks owner
        assert out["owner"] == "fallback-owner"

    def test_optimization_suggestions_from_optimizationSuggestions_key(self):
        structured = {
            "meta": {},
            "optimizationSuggestions": [{"action": "a", "what_to_change": "w"}],
        }
        out = nb.build_card_notification_data(
            notification_structured=structured,
            notification_id="nid",
        )
        assert out["optimizationSuggestions"] == [
            {"title": "a", "description": "w"}
        ]

    def test_saving_ratio_derived_opt_rate(self):
        structured = {"meta": {"saving_ratio": 0.5}}
        out = nb.build_card_notification_data(
            notification_structured=structured,
            notification_id="nid",
        )
        assert out["optimizationRate"] == "50.0%"


# ── build_tc_card_detail_link ───────────────────────────────────


class TestBuildTcCardDetailLink:
    def _decode_inner(self, detail_link: str) -> str:
        """Unwrap the 3-layer nested link and return the inner preview URL."""
        # detailLink = open_platform_link?pcLink=<enc>&mobileLink=<enc>
        qs = detail_link.split("?", 1)[1]
        params = urllib.parse.parse_qs(qs)
        mobile_link = params["mobileLink"][0]
        return mobile_link

    def test_default_params_and_structure(self):
        data = {"foo": "bar", "n": 1}
        link = nb.build_tc_card_detail_link(
            bot_id="bot-1",
            card_id="card_abc",
            notification_data=data,
        )
        assert link.startswith(
            "dingtalk://dingtalkclient/action/open_platform_link?"
        )
        assert "pcLink=" in link
        assert "mobileLink=" in link
        assert "open_side_popup_wnd" in urllib.parse.unquote(link)

    def test_data_is_base64_wrapped(self):
        data = {"hello": "世界"}
        link = nb.build_tc_card_detail_link(
            bot_id="bot-1",
            card_id="card_abc",
            notification_data=data,
        )
        inner = self._decode_inner(link)
        inner_qs = urllib.parse.parse_qs(inner.split("?", 1)[1])
        data_b64 = inner_qs["data"][0]
        decoded = json.loads(base64.b64decode(data_b64).decode("utf-8"))
        assert decoded == {"data": {"hello": "世界"}}
        # default card params encoded too
        params_b64 = inner_qs["params"][0]
        params = json.loads(base64.b64decode(params_b64).decode("utf-8"))
        assert params == {"type": "custom", "botId": "bot-1"}

    def test_custom_card_params(self):
        link = nb.build_tc_card_detail_link(
            bot_id="bot-1",
            card_id="card_abc",
            notification_data={},
            card_params={"type": "x", "k": "v"},
        )
        inner = self._decode_inner(link)
        inner_qs = urllib.parse.parse_qs(inner.split("?", 1)[1])
        params = json.loads(base64.b64decode(inner_qs["params"][0]).decode("utf-8"))
        assert params == {"type": "x", "k": "v"}

    def test_callback_url_and_staff_id_encoded(self):
        link = nb.build_tc_card_detail_link(
            bot_id="bot-1",
            card_id="card_abc",
            notification_data={},
            iframe_callback_url="https://cb.example/x?a=1",
            staff_id="staff-42",
        )
        inner = self._decode_inner(link)
        assert "callbackUrl=" in inner
        assert "staffId=" in inner
        inner_qs = urllib.parse.parse_qs(inner.split("?", 1)[1])
        assert inner_qs["callbackUrl"][0] == "https://cb.example/x?a=1"
        assert inner_qs["staffId"][0] == "staff-42"

    def test_no_callback_no_staff_when_empty(self):
        link = nb.build_tc_card_detail_link(
            bot_id="bot-1",
            card_id="card_abc",
            notification_data={},
        )
        inner = self._decode_inner(link)
        assert "callbackUrl=" not in inner
        assert "staffId=" not in inner

    def test_non_serializable_data_uses_default_str(self):
        from datetime import datetime

        data = {"when": datetime(2026, 6, 29, 12, 0, 0)}
        link = nb.build_tc_card_detail_link(
            bot_id="bot-1",
            card_id="card_abc",
            notification_data=data,
        )
        inner = self._decode_inner(link)
        inner_qs = urllib.parse.parse_qs(inner.split("?", 1)[1])
        decoded = json.loads(
            base64.b64decode(inner_qs["data"][0]).decode("utf-8")
        )
        assert "2026-06-29" in decoded["data"]["when"]


