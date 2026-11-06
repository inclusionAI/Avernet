"""Exercise the real PydanticAI/OpenAI-compatible wire path with a local HTTP transport."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic_ai import models
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from pydanticai_player.brain import PydanticPlayer


async def test_framework_generates_schema_and_retries_over_provider_wire(monkeypatch, vote_task):
    requests = []

    async def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        tool = payload["tools"][0]["function"]
        assert "target" in tool["parameters"]["properties"]
        assert payload["thinking"] == {"type": "disabled"}
        target = 2 if len(requests) == 1 else 3
        return httpx.Response(
            200,
            json={
                "id": f"completion-{len(requests)}",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": f"call-{len(requests)}",
                                    "type": "function",
                                    "function": {
                                        "name": tool["name"],
                                        "arguments": json.dumps({"target": target}),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )

    # ALLOW_MODEL_REQUESTS is enabled only with a no-network MockTransport.
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        model = OpenAIChatModel(
            "test-model",
            provider=OpenAIProvider(
                base_url="https://test.invalid", api_key="test", http_client=client
            ),
            profile=OpenAIModelProfile(openai_supports_strict_tool_definition=False),
            settings={"extra_body": {"thinking": {"type": "disabled"}}},
        )
        answer = await PydanticPlayer(model).answer(vote_task)
    assert answer.text == "我投3号" and answer.requests == 2
    assert answer.input_tokens == 10 and answer.output_tokens == 6
    assert any(
        m["role"] == "tool" and "legal seats" in str(m["content"]) for m in requests[1]["messages"]
    )


async def test_configuration_does_not_expose_keys():
    from pydanticai_player.config import Settings

    settings = Settings(
        base_url="https://test.invalid", api_key="sensitive-test-value", model="test"
    )
    assert "sensitive-test-value" not in repr(settings)
    with pytest.raises(ValueError):
        Settings(base_url="https://u:p@a.test", api_key="test", model="test")
