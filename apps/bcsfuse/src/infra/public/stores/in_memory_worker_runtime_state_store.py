"""
In-Memory Worker Runtime State Store - OSS Wrapper

Wraps existing in-memory implementation for OSS compatibility in tests.
"""
from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore as _InMemoryWorkerRuntimeStateStore


class InMemoryWorkerRuntimeStateStore(_InMemoryWorkerRuntimeStateStore):
    """
    In-Memory Worker Runtime State Store for OSS testing.

    This is a thin wrapper around the existing in-memory implementation
    to maintain consistent naming and future extensibility.

    Suitable for testing only. DO NOT use in production.
    Data is NOT persisted and is lost on restart.
    """

    pass