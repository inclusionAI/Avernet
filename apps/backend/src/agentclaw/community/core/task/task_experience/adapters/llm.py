"""task_experience OpenAI-compatible chat-completion client.

Structural twin of
`community/core/task/forum/shared/llm.py:OpenAICompatibleBbsLLM`: transport,
credential, model and timeout are all injected through the
:mod:`agentclaw.community.plugin_api` seams (``HttpClient`` and
``SecretResolver``). This client shares no configuration or secret with any
other LLM consumer in the backend; ``task_experience`` services consume it
through :class:`TaskExperienceLLMProtocol` and never import ``httpx`` /
``urllib`` / ``ssl`` directly.

Credential and base URL are resolved at construct time; absence turns the
``chat`` call into a caller-skip sentinel (``TASK_EXPERIENCE_LLM_DISABLED``)
instead of raising, mirroring the BBS LLM contract.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentclaw.community.core.task.task_experience.protocols import (
    TaskExperienceLLMProtocol,
)
from agentclaw.community.core.task.task_experience.types import (
    TASK_EXPERIENCE_LLM_DISABLED,
)
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

__all__ = ["OpenAICompatibleTaskExperienceLLM"]


class OpenAICompatibleTaskExperienceLLM(TaskExperienceLLMProtocol):
    """Minimal one-shot chat-completion client for task_experience services.

    Construct with ``base_url`` (the OpenAI-compatible host, no ``/v1`` suffix
    required — the client appends ``/v1/chat/completions``), ``secret_name``
    (resolved via :class:`SecretResolver`), ``model``, ``timeout_ms``, plus
    injected ``SecretResolver`` and ``HttpClient`` (bound under the
    ``Annotated[HttpClient, "task_experience_llm"]`` DI qualifier upstream).
    """

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

    async def chat(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
    ) -> str:
        if not system.strip() and not user.strip():
            raise ValueError(
                "task_experience LLM: at least one of system / user prompt must be non-empty"
            )
        if max_tokens <= 0:
            raise ValueError("task_experience LLM max_tokens must be positive")
        if not self._base_url or not self._token:
            return TASK_EXPERIENCE_LLM_DISABLED

        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
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
                "task_experience LLM response is missing choices[0].message.content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError(
                "task_experience LLM response content must be a non-empty string"
            )
        return content.strip()
