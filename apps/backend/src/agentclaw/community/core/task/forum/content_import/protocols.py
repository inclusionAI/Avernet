"""Transport-agnostic ports for the BBS content import task."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.task.forum.shared.task.sandbox import BbsSandboxTask


@runtime_checkable
class ContentImportTaskExecutorProtocol(Protocol):
    @abstractmethod
    async def execute_once(self, task: BbsSandboxTask) -> dict[str, Any]:
        """Execute one import task and return an observable result."""


__all__ = ["ContentImportTaskExecutorProtocol"]
