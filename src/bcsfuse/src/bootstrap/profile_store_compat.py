"""Delivery compatibility for typed Worker Profile persistence providers."""

from __future__ import annotations

from typing import Any

from src.application.ports.worker_profile_content_store import (
    WorkerProfileContentStore,
)
from src.domain.models.worker_profile_content import WorkerProfileContent


_LEGACY_ROUTE_METHODS = (
    "activate_profile",
    "delete_profile",
    "get_active_profile_for_worker",
    "get_active_profiles",
    "get_profile",
    "list_profiles",
    "upsert_profile",
)


class WorkerProfileContentStoreCompatibilityAdapter:
    """Expose legacy delivery methods over the canonical typed Plugin API."""

    def __init__(self, store: WorkerProfileContentStore) -> None:
        self._store = store

    def save(self, content: WorkerProfileContent) -> WorkerProfileContent:
        return self._store.save(content)

    def get(self, worker_id: str, profile_id: str) -> WorkerProfileContent | None:
        return self._store.get(worker_id, profile_id)

    def list_by_worker(self, worker_id: str):
        return self._store.list_by_worker(worker_id)

    def delete(self, worker_id: str, profile_id: str) -> bool:
        return self._store.delete(worker_id, profile_id)

    def activate(
        self,
        worker_id: str,
        profile_id: str,
    ) -> WorkerProfileContent | None:
        return self._store.activate(worker_id, profile_id)

    def get_active(self, worker_id: str) -> WorkerProfileContent | None:
        return self._store.get_active(worker_id)

    def exists(self, worker_id: str, profile_id: str) -> bool:
        return self._store.exists(worker_id, profile_id)

    def count(self, worker_id: str | None = None) -> int:
        return self._store.count(worker_id)

    def get_all_active(self) -> list[WorkerProfileContent]:
        return self._store.get_all_active()

    @staticmethod
    def _as_route_dict(content: WorkerProfileContent) -> dict[str, Any]:
        return content.model_dump(mode="json")

    def upsert_profile(
        self,
        worker_id: str,
        profile_id: str,
        content: dict[str, Any],
    ) -> bool:
        payload = dict(content)
        payload["worker_id"] = worker_id
        payload["profile_id"] = profile_id
        existing = self._store.get(worker_id, profile_id)
        if existing is not None:
            # Ordinary content updates must not implicitly deactivate a profile.
            payload["is_active"] = existing.is_active
        self._store.save(WorkerProfileContent.model_validate(payload))
        return True

    def get_profile(self, worker_id: str, profile_id: str) -> dict[str, Any] | None:
        content = self._store.get(worker_id, profile_id)
        return self._as_route_dict(content) if content is not None else None

    def list_profiles(self, worker_id: str) -> list[dict[str, Any]]:
        return [
            self._as_route_dict(content)
            for content in self._store.list_by_worker(worker_id).items
        ]

    def delete_profile(self, worker_id: str, profile_id: str) -> bool:
        return self._store.delete(worker_id, profile_id)

    def activate_profile(self, worker_id: str, profile_id: str) -> bool:
        return self._store.activate(worker_id, profile_id) is not None

    def get_active_profile_for_worker(
        self,
        worker_id: str,
    ) -> dict[str, Any] | None:
        content = self._store.get_active(worker_id)
        return self._as_route_dict(content) if content is not None else None

    def get_active_profiles(
        self,
        worker_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        allowed = set(worker_ids) if worker_ids is not None else None
        return [
            self._as_route_dict(content)
            for content in self._store.get_all_active()
            if allowed is None or content.worker_id in allowed
        ]


def ensure_profile_route_compatibility(store: Any) -> Any:
    """Keep legacy route adapters working without widening the typed port."""
    if all(callable(getattr(store, method, None)) for method in _LEGACY_ROUTE_METHODS):
        return store
    if not isinstance(store, WorkerProfileContentStore):
        raise TypeError(
            "worker_profile_content_store must implement WorkerProfileContentStore"
        )
    return WorkerProfileContentStoreCompatibilityAdapter(store)


__all__ = [
    "WorkerProfileContentStoreCompatibilityAdapter",
    "ensure_profile_route_compatibility",
]
