"""Transport-agnostic ports for the BBS Topic-tag task."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.task.forum.shared.task.sandbox import BbsSandboxTask


@runtime_checkable
class TopicTagTaskExecutorProtocol(Protocol):
    @abstractmethod
    async def execute_once(self, task: BbsSandboxTask) -> dict[str, Any]:
        """Execute one automatic Topic-tag task."""


__all__ = ["TopicTagTaskExecutorProtocol"]
