from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any

import httpx

from agentclaw.community.core.agentcompute.domain.models import (
    CompletionRequest,
    CompletionResponse,
    FinishReason,
    LlmCallKind,
    ModelId,
    TokenUsage,
)


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_model: str,
        orchestrator_model: str | None = None,
        timeout_seconds: float = 60.0,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._orchestrator_model = orchestrator_model
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = self._resolve_model(request.model, request.kind)
        body = {
            "model": model,
            "messages": [
                *([{"role": "system", "content": request.system_prompt}] if request.system_prompt else []),
                {"role": "user", "content": request.user_prompt},
            ],
            "max_tokens": request.max_tokens or self._max_tokens,
            "temperature": request.temperature,
        }
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post("/chat/completions", json=body)
                response.raise_for_status()
                return self._parse_response(response.json(), model)
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    raise RuntimeError(
                        f"chat completion failed (client error): {exc.response.status_code}"
                    ) from exc
                last_exc = exc
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                last_exc = exc

            if attempt < self._max_retries:
                jitter = random.uniform(0.0, 0.4 * (2 ** attempt))
                await asyncio.sleep(min(2 ** attempt + jitter, 30.0))
            else:
                raise RuntimeError(
                    f"chat completion failed after {self._max_retries + 1} attempts"
                ) from last_exc

        raise RuntimeError("unreachable")

    def _resolve_model(self, model: ModelId | None, kind: LlmCallKind) -> str:
        if model is not None:
            return model
        if kind == LlmCallKind.ORCHESTRATOR and self._orchestrator_model:
            return self._orchestrator_model
        return self._default_model

    @staticmethod
    def _parse_response(data: dict[str, Any], model: str) -> CompletionResponse:
        choice = data["choices"][0]
        raw_finish = choice.get("finish_reason", "stop")
        finish = FinishReason.ERROR
        try:
            finish = FinishReason(raw_finish)
        except ValueError:
            pass
        usage_raw = data.get("usage", {})
        return CompletionResponse(
            text=choice["message"]["content"],
            model=ModelId(model),
            token_usage=TokenUsage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
            ),
            finish_reason=finish,
        )


class StubLLMProvider:
    def __init__(self, script: list[str] | None = None) -> None:
        self._script = script or []
        self._calls = 0

    def set_script(self, script: list[str]) -> None:
        self._script = script
        self._calls = 0

    @property
    def call_count(self) -> int:
        return self._calls

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if self._calls < len(self._script):
            text = self._script[self._calls]
            self._calls += 1
        else:
            text = '{"kind": "finish", "answer": "stub done"}'
            self._calls += 1
        return CompletionResponse(
            text=text,
            model=request.model or ModelId("stub"),
            token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            finish_reason=FinishReason.STOP,
        )