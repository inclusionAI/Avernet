"""
In-Memory Worker Registry Store - OSS Wrapper

Wraps existing in-memory implementation for OSS compatibility in tests.
"""

import logging

from src.application.ports.worker_profile_content_store import (
    WorkerProfileContentStore,
)
from src.domain.services.adapters.worker_profile_binding_store_adapter import (
    WorkerProfileBindingStoreAdapter,
)
from src.infra.adapters.in_memory_worker_registry_store import (
    InMemoryWorkerRegistryStore as _InMemoryWorkerRegistryStore,
)


logger = logging.getLogger(__name__)


class InMemoryWorkerRegistryStore(_InMemoryWorkerRegistryStore):
    """
    In-Memory Worker Registry Store for OSS testing.

    This is a thin wrapper around the existing in-memory implementation
    to maintain consistent naming and future extensibility.

    Suitable for testing only. DO NOT use in production.
    Data is NOT persisted and is lost on restart.
    """

    def __init__(
        self,
        profile_store: WorkerProfileContentStore | None = None,
        binding_store: WorkerProfileBindingStoreAdapter | None = None,
    ) -> None:
        super().__init__()
        self._profile_store = profile_store
        self._binding_store = binding_store

    def delete(self, worker_id: str) -> bool:
        """Delete the in-memory Worker aggregate without partial cleanup."""
        if self._profile_store is None or self._binding_store is None:
            return super().delete(worker_id)

        profiles = list(self._profile_store.list_by_worker(worker_id).items)
        active_profile = self._profile_store.get_active(worker_id)
        active_binding = self._binding_store.get_active_binding(worker_id)
        try:
            for profile in profiles:
                if not self._profile_store.delete(worker_id, profile.profile_id):
                    raise RuntimeError(
                        f"profile {worker_id}:{profile.profile_id} was not deleted"
                    )
            if active_binding is not None:
                if not self._binding_store.unbind_profile(
                    worker_id,
                    active_binding.profile_key,
                ):
                    raise RuntimeError(
                        f"active profile binding for {worker_id} was not deleted"
                    )
            return super().delete(worker_id)
        except Exception:
            for profile in profiles:
                try:
                    self._profile_store.save(profile)
                except Exception:
                    logger.exception(
                        "Failed to restore profile %s:%s after aggregate "
                        "delete failure",
                        worker_id,
                        profile.profile_id,
                    )
            if active_profile is not None:
                try:
                    self._profile_store.activate(worker_id, active_profile.profile_id)
                except Exception:
                    logger.exception(
                        "Failed to restore active profile for %s after "
                        "aggregate delete failure",
                        worker_id,
                    )
            if active_binding is not None:
                try:
                    self._binding_store.bind_profile(
                        worker_id=active_binding.worker_id,
                        profile_key=active_binding.profile_key,
                        source_type=active_binding.source_type,
                    )
                except Exception:
                    logger.exception(
                        "Failed to restore profile binding for %s after "
                        "aggregate delete failure",
                        worker_id,
                    )
            raise
