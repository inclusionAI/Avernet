"""client 包边角单测 —— 翻译器解析分支、BCS 身份解析防御分支、状态码映射 seam
与 singlebox final 文本聚合。全部为纯函数/纯存储操作,不触网。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.errors import BotIdentityResolutionError
from agentclaw.community.core.task.task_runner.client.bcs_bot_identity_resolver import (
    BotServiceBcsBotIdentityResolver,
)
from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsRateLimitError,
    _map_status as bcs_map_status,
)
from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
    OpenApiAuthError,
    OpenApiBadRequestError,
    OpenApiRateLimitError,
    OpenApiServerError,
    _map_status as oa_map_status,
    _resp_summary,
)
from agentclaw.community.core.task.task_runner.client.singlebox_engine_adapter import (
    _extract_final_text,
)
from agentclaw.community.core.task.task_runner.client.translators import (
    _cb,
    _parse_acceptance,
)


# ===== translators:_cb 兼容分支 与 _parse_acceptance 严格解析 =====
class TestTranslatorsEdges:
    def test_cb_keeps_legacy_fail_detail_field(self):
        data = _cb("t1::c1", "single_bot", success=False, fail_detail="timeout")
        result = data.data["result"]
        assert result["success"] is False
        assert result["fail_detail"] == "timeout"      # 旧调用方过渡兼容字段保留

    def test_parse_acceptance_rejects_non_object_json(self):
        with pytest.raises(ValueError, match="JSON object"):
            _parse_acceptance("[1, 2]")                  # 非 dict 终态拒绝

    def test_parse_acceptance_defaults_gaps_to_empty_on_success(self):
        success, gaps, data = _parse_acceptance('{"success": true}')
        assert success is True and gaps == [] and data is None   # gaps 缺省为空列表

    def test_parse_acceptance_rejects_non_list_gaps(self):
        with pytest.raises(ValueError, match="list of strings"):
            _parse_acceptance('{"success": false, "gaps": "single"}')  # gaps 必须列表


# ===== bcs_bot_identity_resolver 防御分支 =====
class _BotService:
    def __init__(self, items):
        self._items = items
        self.calls = []

    def list_bots_by_conditions(self, *, bot_ids, page, page_size):
        self.calls.append(list(bot_ids))
        return {"items": self._items}


class TestIdentityResolverEdges:
    def test_empty_input_raises(self):
        resolver = BotServiceBcsBotIdentityResolver(_BotService([]))
        with pytest.raises(BotIdentityResolutionError):
            resolver.resolve_many(["", "  "])            # 全空 id → 拒绝解析

    def test_fully_resolved_ids_skip_bot_service(self):
        resolver = BotServiceBcsBotIdentityResolver(_BotService([]))
        resolved = resolver.resolve_many(["b1:U1", "b2:U2"])
        assert resolved == {"b1:U1": "b1:U1", "b2:U2": "b2:U2"}  # 已带 ':' 原样透传
        assert resolver._bot_service.calls == []          # 不查 BotService

    def test_ambiguous_matches_raise(self):
        # 两条同 bot_id 记录 → 无法唯一解析 owner
        service = _BotService([{"bot_id": "b1", "owner_id": "U1"}, {"bot_id": "b1", "owner_id": "U2"}])
        with pytest.raises(BotIdentityResolutionError):
            BotServiceBcsBotIdentityResolver(service).resolve_many(["b1"])

    def test_non_dict_items_are_skipped(self):
        service = _BotService(["garbage", {"bot_id": "b1", "owner_id": "U1"}])
        resolved = BotServiceBcsBotIdentityResolver(service).resolve_many(["b1"])
        assert resolved == {"b1": "b1:U1"}                 # 非 dict 行跳过,合法行照常解析


# ===== 状态码映射与响应摘要 seam =====
class TestStatusMapSeams:
    def test_open_api_map_status_raises_by_family(self):
        for status, cls in (
            (401, OpenApiAuthError),
            (429, OpenApiRateLimitError),
            (400, OpenApiBadRequestError),
            (500, OpenApiServerError),
        ):
            with pytest.raises(cls):
                oa_map_status(SimpleNamespace(status_code=status, text="err"))

    def test_bcs_map_status_raises_rate_limit(self):
        with pytest.raises(BcsRateLimitError):
            bcs_map_status(SimpleNamespace(status_code=429, text="busy"))

    def test_resp_summary_bounded_and_unreadable_fallback(self):
        resp = SimpleNamespace(text="line1\nline2" + "x" * 600)
        assert _resp_summary(resp) == "line1 line2" + "x" * 489  # 换行折空格 + 500 上限截断

        class _Unreadable:  # .text 读取抛错 → 兜底占位,不向调用方抛
            @property
            def text(self):
                raise RuntimeError("gone")

        assert _resp_summary(_Unreadable()) == "<unreadable>"


# ===== singlebox final 文本聚合 =====
class TestExtractFinalText:
    def test_non_list_content_returns_empty(self):
        assert _extract_final_text({"message": {"content": "not-a-list"}}) == ""

    def test_non_dict_blocks_are_skipped(self):
        payload = {"message": {"content": ["raw", {"text": "正文一"}, "more"]}}
        assert _extract_final_text(payload) == "正文一"    # 非 dict 块跳过,保留有效文本
        assert _extract_final_text({"message": {"content": [42, None]}}) == ""