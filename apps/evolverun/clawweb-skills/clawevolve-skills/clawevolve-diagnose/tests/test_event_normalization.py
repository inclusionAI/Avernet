from __future__ import annotations

from clawevolve_diagnose.acquisition.sessions import _is_trajectory_metadata, _raw_event_role
from clawevolve_diagnose.judge.ocsa_session_adapter import (
    _event_content_value,
    _event_role,
    build_judge_session_payload,
)


def test_model_completed_prompt_metadata_does_not_override_assistant_content() -> None:
    event = {
        "type": "model.completed",
        "data": {
            "finalPromptText": "用户真实请求",
            "assistantTexts": ["助手已完成任务"],
        },
    }
    assert _event_role(event) == "assistant"
    assert _event_content_value(event) == ["助手已完成任务"]


def test_trace_artifacts_are_metadata_and_tool_metas_are_preserved() -> None:
    event = {
        "type": "trace.artifacts",
        "data": {
            "finalPromptText": "用户真实请求",
            "toolMetas": [{"name": "fetch_biz_data"}, {"toolName": "mcp.search"}],
        },
    }
    assert _event_role(event) == "metadata"
    assert _raw_event_role(event) == "metadata"
    assert _is_trajectory_metadata(event)
    payload = build_judge_session_payload([event])
    assert payload["messages"] == [
        {"idx": 0, "role": "toolResult", "toolName": "fetch_biz_data", "content": "Observed tool invocation metadata; execution result unavailable.", "metadataOnly": True},
        {"idx": 1, "role": "toolResult", "toolName": "mcp.search", "content": "Observed tool invocation metadata; execution result unavailable.", "metadataOnly": True},
    ]
    assert payload["allExeSkill"] == "fetch_biz_data"
    assert payload["allExeMcp"] == "mcp.search"


def test_final_prompt_metadata_still_identifies_real_user_event() -> None:
    event = {"type": "trace.metadata", "data": {"finalPromptText": "用户真实请求"}}
    assert _event_role(event) == "user"
    assert _raw_event_role(event) == "user"


def test_nested_event_envelope_preserves_structured_signals() -> None:
    objects = [
        {
            "data": {
                "type": "model.completed",
                "finalPromptText": "用户请求",
                "assistantTexts": ["已读取技能", "已完成任务"],
            }
        },
        {
            "data": {
                "type": "trace.artifacts",
                "artifacts": {
                    "toolMetas": [{"skillName": "write_context_info_extract"}],
                    "toolUseStats": {"total": 1},
                },
            }
        },
    ]
    payload = build_judge_session_payload(objects)
    assert payload["assistantMsgCnt"] == 1
    assert payload["toolResultCnt"] == 1
    assert payload["allExeSkill"] == "write_context_info_extract"
    assert payload["toolUseStats"] == {"total": 1}


def test_messages_snapshot_is_primary_ocsa_evidence_and_preserves_corrections() -> None:
    objects = [
        {
            "type": "model.completed",
            "data": {
                "finalPromptText": "打开这个链接",
                "assistantTexts": ["无法访问，请复制内容"],
                "messagesSnapshot": [
                    {"role": "user", "content": "打开这个链接"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "web_fetch",
                                "input": {"url": "https://example.test/doc"},
                            }
                        ],
                    },
                    {
                        "role": "toolResult",
                        "toolName": "web_fetch",
                        "content": "login required",
                        "isError": True,
                    },
                    {"role": "assistant", "content": "无法访问，请复制内容"},
                    {"role": "user", "content": "你应该用知识库 MCP 访问"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "mcp.knowledge_detail",
                                "input": {"url": "https://example.test/doc"},
                            }
                        ],
                    },
                    {
                        "role": "toolResult",
                        "toolName": "mcp.knowledge_detail",
                        "content": "document content",
                    },
                ],
            },
        },
        {
            "type": "trace.artifacts",
            "data": {
                "finalPromptText": "打开这个链接",
                "toolMetas": [
                    {"toolName": "web_fetch"},
                    {"toolName": "mcp.knowledge_detail"},
                ],
            },
        },
    ]

    payload = build_judge_session_payload(objects)

    assert [message["role"] for message in payload["messages"][:7]] == [
        "user",
        "assistant",
        "toolResult",
        "assistant",
        "user",
        "assistant",
        "toolResult",
    ]
    assert len(payload["messages"]) == 7
    assert payload["messages"][2]["isError"] is True
    assert payload["messages"][4]["content"] == "你应该用知识库 MCP 访问"
    assert payload["allExeMcp"] == "mcp.knowledge_detail"
    assert not any(message.get("metadataOnly") for message in payload["messages"])
