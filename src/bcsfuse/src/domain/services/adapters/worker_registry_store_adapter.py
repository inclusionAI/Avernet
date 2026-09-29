"""Backward-compatible name for the canonical Worker registry port."""

from src.application.ports.worker_registry_store import WorkerRegistryStore


WorkerRegistryStoreAdapter = WorkerRegistryStore


__all__ = ["WorkerRegistryStoreAdapter"]
