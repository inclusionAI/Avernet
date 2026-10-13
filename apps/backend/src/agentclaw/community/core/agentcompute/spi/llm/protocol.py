from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from agentclaw.community.core.agentcompute.domain.models import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
)


@runtime_checkable
class LLMProvider(Protocol):

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...


@runtime_checkable
class StreamingLLMProvider(Protocol):
    """Optional streaming seam: one-shot complete() callers are unaffected,
    and hand-fakes stay valid LLMProviders without implementing this; the
    shipped providers implement both, so streaming consumers accept this
    narrower protocol and fall back to complete() when absent."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    def stream_complete(
        self, request: CompletionRequest,
    ) -> AsyncIterator[CompletionChunk]: ...