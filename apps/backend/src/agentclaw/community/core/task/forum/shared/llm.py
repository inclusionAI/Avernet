"""BBS-specific OpenAI-compatible LLM client.

The model endpoint, model name, timeout, token, and HTTP transport are all
injected. This client shares no configuration or secret with the Harness
health-check LLM, so BBS cannot accidentally consume another feature's quota.
Both background tasks consume this same client through ``BbsLLMProtocol``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentclaw.community.core.task.forum.shared.protocols import BbsLLMProtocol
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

BBS_LLM_DISABLED = "[bbs llm disabled]"


class OpenAICompatibleBbsLLM(BbsLLMProtocol):
    """Minimal one-shot chat-completion client for backend BBS tasks."""

    def __init__(
        self,
        *,
        base_url: str,
        secret_name: str,
        model: str,
        timeout_ms: int,
        secret_resolver: SecretResolver,
        http_client: HttpClient,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret_name = secret_name
        self._model = model
        self._timeout_seconds = timeout_ms / 1000.0
        self._secret_resolver = secret_resolver
        self._http = http_client
        self._token = self._resolve_token()

    def _resolve_token(self) -> str:
        if not self._base_url or not self._secret_name:
            return ""
        secret = self._secret_resolver.get_secret(self._secret_name)
        secret_value = getattr(secret, "secret_value", None)
        if secret_value is None or not str(secret_value).strip():
            return ""
        return str(secret_value)

    async def summarize(self, prompt: str, *, max_tokens: int) -> str:
        if not prompt.strip():
            raise ValueError("BBS LLM prompt must be a non-empty string")
        if max_tokens <= 0:
            raise ValueError("BBS LLM max_tokens must be positive")
        if not self._base_url or not self._token:
            return BBS_LLM_DISABLED

        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        response = await asyncio.to_thread(
            self._http.post,
            f"{self._base_url}/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                "BBS LLM response is missing choices[0].message.content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("BBS LLM response content must be a non-empty string")
        return content


__all__ = ["BBS_LLM_DISABLED", "OpenAICompatibleBbsLLM"]
