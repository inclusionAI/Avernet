from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import httpx

from agentclaw.community.core.agentswarm.domain.models import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    FinishReason,
    LlmCallKind,
    ModelId,
    TokenUsage,
)


# 4xx statuses that are transient (retryable): the server-side throttle /
# timeout / too-early markers. Every other 4xx is a permanent client error
# (bad auth, bad request) and must fail immediately without burning retries.
_TRANSIENT_STATUSES = frozenset({408, 425, 429})


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
        egress: Any = None,
    ) -> None:
        from agentclaw.community.core.agentswarm.runtime.security import validate_egress_url
        if egress is not None:
            validate_egress_url(
                base_url,
                allowed_hosts=egress.allowed_hosts,
                require_https=egress.require_https,
                surface="llm",
            )
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
            last_attempt = attempt == self._max_retries

            try:
                response = await self._client.post("/chat/completions", json=body)
                response.raise_for_status()
                parsed = self._parse_response(response.json(), model)
                return parsed
            except httpx.HTTPStatusError as exc:
                if (400 <= exc.response.status_code < 500
                        and exc.response.status_code not in _TRANSIENT_STATUSES):
                    raise RuntimeError(
                        f"chat completion failed (client error): {exc.response.status_code}"
                    ) from exc
                last_exc = exc
            except httpx.TimeoutException as exc:
                last_exc = exc
            except (httpx.ConnectError, httpx.ReadError) as exc:
                last_exc = exc

            if attempt < self._max_retries:
                jitter = random.uniform(0.0, 0.4 * (2 ** attempt))
                await asyncio.sleep(min(2 ** attempt + jitter, 30.0))
            else:
                raise RuntimeError(
                    f"chat completion failed after {self._max_retries + 1} attempts"
                ) from last_exc

        raise RuntimeError("unreachable")

    def stream_complete(self, request: CompletionRequest):
        # Streaming twin of complete(): stream_options.include_usage makes
        # the send a final usage-carrying data event before [DONE], so a
        # consumer can record tokens without a second call. No retry here —
        # a stream that dies mid-flight surfaces to the caller as terminal;
        # partial text already yielded stays the caller's to discard. A
        # stream that ends without the [DONE] marker and without any
        # finish_reason is server truncation, and raises instead of
        # synthesizing a fake successful STOP.
        return self._stream_chunks(request)

    async def _stream_chunks(self, request: CompletionRequest):
        model = self._resolve_model(request.model, request.kind)
        body = {
            "model": model,
            "messages": [
                *([{"role": "system", "content": request.system_prompt}] if request.system_prompt else []),
                {"role": "user", "content": request.user_prompt},
            ],
            "max_tokens": request.max_tokens or self._max_tokens,
            "temperature": request.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        async with self._client.stream("POST", "/chat/completions", json=body) as response:
            if response.status_code >= 400:
                raise RuntimeError(
                    f"chat completion stream failed ({response.status_code})"
                )
            usage: TokenUsage | None = None
            finish_reason: FinishReason | None = None
            completed = False
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    completed = True
                    break
                event = json.loads(data)
                if event.get("usage"):
                    usage = TokenUsage(
                        prompt_tokens=event["usage"].get("prompt_tokens", 0),
                        completion_tokens=event["usage"].get("completion_tokens", 0),
                        total_tokens=event["usage"].get("total_tokens", 0),
                    )
                    continue
                choices = event.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta_text = (choice.get("delta") or {}).get("content") or ""
                raw_finish = choice.get("finish_reason")
                if raw_finish:
                    try:
                        finish_reason = FinishReason(raw_finish)
                    except ValueError:
                        finish_reason = FinishReason.ERROR
                if delta_text:
                    yield CompletionChunk(text=delta_text)
            if not completed and finish_reason is None:
                raise RuntimeError(
                    "chat completion stream ended without completion: "
                    "no [DONE] marker and no finish_reason received "
                    "(stream truncated by server)"
                )
            yield CompletionChunk(
                finish_reason=finish_reason or FinishReason.STOP,
                model=ModelId(model),
                token_usage=usage or TokenUsage(0, 0, 0),
            )
    def _resolve_model(self, model: ModelId | None, kind: LlmCallKind) -> str:
        if model is not None:
            return model
        if kind == LlmCallKind.ORCHESTRATOR and self._orchestrator_model:
            return self._orchestrator_model
        return self._default_model

    @staticmethod
    def _parse_response(data: dict[str, Any], model: str) -> CompletionResponse:
        choices = data.get("choices") or []
        if not choices:
            raise ValueError(
                f"chat completion response has no choices (model={model!r})"
            )
        choice = choices[0]
        raw_finish = choice.get("finish_reason", "stop")
        finish = FinishReason.ERROR
        try:
            finish = FinishReason(raw_finish)
        except ValueError:
            pass
        usage_raw = data.get("usage") or {}
        return CompletionResponse(
            text=(choice.get("message") or {}).get("content") or "",
            model=ModelId(model),
            token_usage=TokenUsage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
            ),
            finish_reason=finish,
        )
