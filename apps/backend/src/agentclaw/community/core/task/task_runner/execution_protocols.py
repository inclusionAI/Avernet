"""Execution-mode adapter contracts.

These contracts separate orchestration policy from the existing TaskRunner
service API.  They intentionally do not change TaskRunner's public methods.
"""
from __future__ import annotations

from typing import Any, Protocol


class CentralizedExecutionAdapterProtocol(Protocol):
    async def start(self, task_id: str) -> Any:
        """Start or resume centralized execution for one task."""
        ...

    async def on_start(self, patch: Any) -> Any:
        ...

    async def on_report(self, patch: Any) -> Any:
        ...

    async def on_harness(self, patch: Any) -> Any:
        ...


class RelayExecutionAdapterProtocol(Protocol):
    @property
    def runner(self) -> Any:
        ...

    @property
    def search(self) -> Any:
        ...


class TaskSearchProtocol(Protocol):
    async def search(self, query: str) -> Any:
        """Return candidates only; never mutate task state."""
        ...
