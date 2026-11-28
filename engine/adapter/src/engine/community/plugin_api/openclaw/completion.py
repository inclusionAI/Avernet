"""Optional native, stateless model completion port. No chat side effects."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.models.completion import CompletionRequest, ModelCompletion


@runtime_checkable
class OpenClawCompletionPort(Protocol):
    async def complete_model(self, request: CompletionRequest) -> ModelCompletion:
        """Resolve live engine configuration and infer once without exporting it."""
        ...
