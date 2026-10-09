from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentcompute.domain.models import (
    CompletionRequest,
    CompletionResponse,
)


@runtime_checkable
class LLMProvider(Protocol):

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...