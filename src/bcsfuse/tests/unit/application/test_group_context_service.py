"""
Tests for GroupContextService

G9: 群组上下文服务测试
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.application.services.bot_fuse.group_context_service import (
    GroupContextService,
    GroupMessage,
)
from src.domain.models.llm_response import LLMResponse, FinishReason


class FakeLLMGateway:
    """测试用 Fake LLM Gateway，返回固定的 raw_text。"""

    def __init__(self, raw_text: str):
        self._raw_text = raw_text

    def generate(self, request):
        return LLMResponse(
            provider_id="fake",
            model_id="fake",
            raw_text=self._raw_text,
            parse_success=True,
            latency_ms=0,
            finish_reason=FinishReason.STOP,
        )


@pytest.mark.asyncio
async def test_summarize_fallback_to_raw_context_when_llm_extraction_fails():
    """
    当 LLM 摘要提取失败（返回空的 JSON code fence）时，
    summarize() 应该将原始群消息文本作为 context_summary 返回，
    而不是返回空摘要导致最终 Prompt 丢失群消息上下文。
    """
    service = GroupContextService(
        llm_gateway=FakeLLMGateway("```json"),
        bcn_base_url="http://example.com",
        context_limit=10,
    )

    service._fetch_group_messages = AsyncMock(
        return_value=[
            GroupMessage(
                sender="user",
                content="帮我看看各位专家的方案",
                role="user",
                timestamp=1,
            ),
            GroupMessage(
                sender="平台营销方案",
                content="我建议双11做满减活动，预算 10 万。",
                role="assistant",
                timestamp=2,
            ),
        ]
    )

    result = await service.summarize(
        question="帮我review群里各专家的方案",
        group_id="grp-test",
    )

    assert result.context_messages_count == 2
    assert result.context_summary, "LLM 提取失败时，context_summary 不应为空"
    assert "满减活动" in result.context_summary, "fallback 摘要应包含群消息原文"
    assert result.key_messages == [], "LLM 提取失败时 key_messages 应为空列表"


@pytest.mark.asyncio
async def test_summarize_fallback_to_raw_context_on_empty_json_value():
    """
    LLM 返回合法 JSON 但 context_summary 为空时也应 fallback。
    """
    service = GroupContextService(
        llm_gateway=FakeLLMGateway('```json\n{"context_summary": ""}\n```'),
        bcn_base_url="http://example.com",
        context_limit=10,
    )

    service._fetch_group_messages = AsyncMock(
        return_value=[
            GroupMessage(
                sender="平台供应链",
                content="库存已备好，支持 5000 单/天。",
                role="assistant",
                timestamp=1,
            ),
        ]
    )

    result = await service.summarize(
        question="帮我review群里各专家的方案",
        group_id="grp-test",
    )

    assert result.context_messages_count == 1
    assert "5000 单/天" in result.context_summary


@pytest.mark.asyncio
async def test_summarize_extracts_key_messages_with_sender():
    """
    LLM 正常提取时，应解析出带 sender 的 key_messages 供最终 Prompt 使用。
    """
    raw_response = '''```json
{
  "context_summary": "各专家已提交方案。",
  "key_messages": [
    {"sender": "平台营销方案", "content": "我建议双11做满减活动，预算 10 万。"},
    {"sender": "平台供应链", "content": "库存已备好，支持 5000 单/天。"}
  ]
}
```'''
    service = GroupContextService(
        llm_gateway=FakeLLMGateway(raw_response),
        bcn_base_url="http://example.com",
        context_limit=10,
    )

    service._fetch_group_messages = AsyncMock(
        return_value=[
            GroupMessage(
                sender="平台营销方案",
                content="我建议双11做满减活动，预算 10 万。",
                role="assistant",
                timestamp=1,
            ),
            GroupMessage(
                sender="平台供应链",
                content="库存已备好，支持 5000 单/天。",
                role="assistant",
                timestamp=2,
            ),
        ]
    )

    result = await service.summarize(
        question="帮我review群里各专家的方案",
        group_id="grp-test",
    )

    assert result.context_summary == "各专家已提交方案。"
    assert len(result.key_messages) == 2
    assert result.key_messages[0]["sender"] == "平台营销方案"
    assert "满减活动" in result.key_messages[0]["content"]
    assert result.key_messages[1]["sender"] == "平台供应链"
    assert "5000 单/天" in result.key_messages[1]["content"]


@pytest.mark.parametrize(
    "strategy, session_id, expected_path",
    [
        ("chat", "", "/groups/grp-1/messages"),
        ("chat", "sid-1", "/groups/grp-1/messages"),
        ("manager_worker", "sid-1", "/sessions/sid-1/messages"),
        ("state_machine", "sid-1", "/sessions/sid-1/messages"),
        ("STATE_MACHINE", "sid-1", "/sessions/sid-1/messages"),
    ],
)
def test_resolve_history_url_uses_session_for_managed_groups(strategy, session_id, expected_path):
    """
    manager_worker 和 state_machine（含 DAG 拓扑）群的真实对话存在 session 下，
    必须走 /sessions/{session_id}/messages，否则 fuse 会拿不到群消息。
    """
    service = GroupContextService(
        llm_gateway=FakeLLMGateway(""),
        bcn_base_url="http://example.com",
    )
    url = service._resolve_history_url(
        "grp-1",
        {"group_strategy": strategy, "latest_running_session_id": session_id},
    )
    assert url == f"http://example.com{expected_path}"
