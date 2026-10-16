from __future__ import annotations

from agentclaw.community.core.agentswarm.domain.models import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    FinishReason,
    ModelId,
    TokenUsage,
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

    async def stream_complete(self, request: CompletionRequest):
        # Mirrors complete(): one script entry consumed per call, yielded
        # as a single delta chunk followed by the usage-carrying final chunk
        # so streaming callers see the same scripted battery.
        if self._calls < len(self._script):
            text = self._script[self._calls]
            self._calls += 1
        else:
            text = '{"kind": "finish", "answer": "stub done"}'
            self._calls += 1
        yield CompletionChunk(text=text)
        yield CompletionChunk(
            finish_reason=FinishReason.STOP,
            model=request.model or ModelId("stub"),
            token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        )