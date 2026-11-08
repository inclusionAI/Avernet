"""Focused tests for the backend-owned BBS OpenAI-compatible client."""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.task.forum.shared.llm import (
    BBS_LLM_DISABLED,
    OpenAICompatibleBbsLLM,
)


class _FakeSecret:
    secret_value = "bbs-token"


class _FakeSecretResolver:
    def __init__(self) -> None:
        self.names: list[str] = []

    def get_secret(self, secret_name: str) -> Any:
        self.names.append(secret_name)
        return _FakeSecret()


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": " BBS model response "}}]}


class _FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        path: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> _FakeResponse:
        self.calls.append(
            {
                "path": path,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return _FakeResponse()


def _client(
    *, base_url: str = "http://localhost:8000"
) -> tuple[
    OpenAICompatibleBbsLLM,
    _FakeSecretResolver,
    _FakeHttpClient,
]:
    secret_resolver = _FakeSecretResolver()
    http_client = _FakeHttpClient()
    client = OpenAICompatibleBbsLLM(
        base_url=base_url,
        secret_name="BBS_LLM_AUTH_TOKEN",
        model="test-model",
        timeout_ms=45_000,
        secret_resolver=secret_resolver,
        http_client=http_client,
    )
    return client, secret_resolver, http_client


@pytest.mark.asyncio
async def test_summarize_sends_bbs_model_request_body() -> None:
    client, secret_resolver, http_client = _client()

    response = await client.summarize("Summarize this topic.", max_tokens=17)

    assert response == " BBS model response "
    assert secret_resolver.names == ["BBS_LLM_AUTH_TOKEN"]
    assert len(http_client.calls) == 1
    request = http_client.calls[0]
    assert request["path"] == "http://localhost:8000/v1/chat/completions"
    assert request["headers"] == {
        "Authorization": "Bearer bbs-token",
        "Content-Type": "application/json",
    }
    assert request["timeout"] == 45.0
    assert request["json"] == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Summarize this topic."}],
        "max_tokens": 17,
        "stream": False,
    }


@pytest.mark.asyncio
async def test_disabled_client_returns_sentinel_without_http_or_secret_calls() -> None:
    client, secret_resolver, http_client = _client(base_url="")

    response = await client.summarize("Summarize this topic.", max_tokens=17)

    assert response == BBS_LLM_DISABLED
    assert secret_resolver.names == []
    assert http_client.calls == []
