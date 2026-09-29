"""Transport-agnostic persistence contract for Worker registry data."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.worker import TrustLevel, Worker
from src.domain.models.worker_config import WorkerConfig
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_source_info import WorkerSourceType


@runtime_checkable
class WorkerRegistryStore(Protocol):
    """Canonical registry API implemented by public and internal providers."""

    def create(self, worker: Worker) -> Worker: ...

    def get_by_id(self, worker_id: str) -> Worker | None: ...

    def list(
        self,
        lifecycle_states: list[WorkerLifecycleState] | None = None,
        source_types: list[WorkerSourceType] | None = None,
        domains: list[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Worker]: ...

    def update(self, worker: Worker) -> Worker: ...

    def update_lifecycle_state(
        self,
        worker_id: str,
        lifecycle_state: WorkerLifecycleState,
        version: int,
    ) -> Worker: ...

    def delete(self, worker_id: str) -> bool: ...

    def exists(self, worker_id: str) -> bool: ...

    def count(
        self,
        lifecycle_states: list[WorkerLifecycleState] | None = None,
    ) -> int: ...

    def update_trust_level(
        self,
        worker_id: str,
        trust_level: TrustLevel,
    ) -> Worker: ...

    def get_by_ids(self, worker_ids: list[str]) -> dict[str, Worker]: ...

    def batch_get_configs(
        self,
        worker_ids: list[str],
    ) -> tuple[dict[str, WorkerConfig], list[str]]: ...


__all__ = ["WorkerRegistryStore"]
