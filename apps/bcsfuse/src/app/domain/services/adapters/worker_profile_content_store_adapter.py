"""Backward-compatible name for the canonical profile-content store port."""

from app.application.ports.worker_profile_content_store import (
    WorkerProfileContentStore,
)


WorkerProfileContentStoreAdapter = WorkerProfileContentStore


__all__ = ["WorkerProfileContentStoreAdapter"]
