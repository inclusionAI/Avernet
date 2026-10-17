"""Transport-agnostic ports shared by backend-owned BBS tasks."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.task.forum.shared.task.sandbox import BbsSandboxTask


@runtime_checkable
class BbsTaskExecutorProtocol(Protocol):
    @abstractmethod
    async def execute_once(self, task: BbsSandboxTask) -> dict[str, Any]:
        """Execute one backend-owned BBS task and return an observable result."""


@runtime_checkable
class BbsLLMProtocol(Protocol):
    @abstractmethod
    async def summarize(self, prompt: str, *, max_tokens: int) -> str:
        """Return one model-completion string, or the clear disabled sentinel."""


__all__ = ["BbsLLMProtocol", "BbsTaskExecutorProtocol"]
