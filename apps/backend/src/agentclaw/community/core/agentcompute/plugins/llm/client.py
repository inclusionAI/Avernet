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
from agentclaw.community.core.agentcompute.observability import (
    UNATTRIBUTED_TENANT, AgentComputeObservability,
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
        observability: AgentComputeObservability | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._orchestrator_model = orchestrator_model
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._obs = observability
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
            last_attempt = attempt == self._max_retries

            def note_failure(outcome: str) -> None:
                if self._obs is None:
                    return
                if last_attempt:
                    # The budget-exhausting attempt is its own outcome class.
                    self._obs.llm_call("exhausted")
                else:
                    self._obs.llm_call(outcome)

            try:
                response = await self._client.post("/chat/completions", json=body)
                response.raise_for_status()
                parsed = self._parse_response(response.json(), model)
                if self._obs is not None:
                    self._obs.llm_call("ok")
                    self._obs.token_usage(
                        UNATTRIBUTED_TENANT, request.kind, parsed.token_usage,
                    )
                return parsed
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    if self._obs is not None:
                        self._obs.llm_call("non_retryable")
                    raise RuntimeError(
                        f"chat completion failed (client error): {exc.response.status_code}"
                    ) from exc
                last_exc = exc
                note_failure("transient")
            except httpx.TimeoutException as exc:
                last_exc = exc
                note_failure("timeout")
            except (httpx.ConnectError, httpx.ReadError) as exc:
                last_exc = exc
                note_failure("transient")

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