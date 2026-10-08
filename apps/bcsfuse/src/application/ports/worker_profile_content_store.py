"""Transport-agnostic persistence contract for Worker Profile content."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.worker_profile_content import (
    WorkerProfileContent,
    WorkerProfileContentList,
)


@runtime_checkable
class WorkerProfileContentStore(Protocol):
    """Canonical store API implemented by public and internal providers."""

    def save(self, content: WorkerProfileContent) -> WorkerProfileContent: ...

    def get(self, worker_id: str, profile_id: str) -> WorkerProfileContent | None: ...

    def list_by_worker(self, worker_id: str) -> WorkerProfileContentList: ...

    def delete(self, worker_id: str, profile_id: str) -> bool: ...

    def activate(
        self,
        worker_id: str,
        profile_id: str,
    ) -> WorkerProfileContent | None: ...

    def get_active(self, worker_id: str) -> WorkerProfileContent | None: ...

    def exists(self, worker_id: str, profile_id: str) -> bool: ...

    def count(self, worker_id: str | None = None) -> int: ...

    def get_all_active(self) -> list[WorkerProfileContent]: ...


__all__ = ["WorkerProfileContentStore"]
