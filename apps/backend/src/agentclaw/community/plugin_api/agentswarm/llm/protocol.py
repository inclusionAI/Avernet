from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

# ``CompletionChunk`` / ``CompletionRequest`` / ``CompletionResponse``
# (core.agentswarm.domain.models) use postponed annotations below —
# plugin_api/ must not import core/ (layer rule), so no
# ``from agentclaw.community.core... import`` lives here.


@runtime_checkable
class LLMProvider(Protocol):

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...  # noqa: F821


@runtime_checkable
class StreamingLLMProvider(Protocol):
    """Optional streaming seam: one-shot complete() callers are unaffected,
    and hand-fakes stay valid LLMProviders without implementing this; the
    shipped providers implement both, so streaming consumers accept this
    narrower protocol and fall back to complete() when absent."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...  # noqa: F821

    def stream_complete(
        self, request: CompletionRequest,  # noqa: F821
    ) -> AsyncIterator[CompletionChunk]: ...  # noqa: F821