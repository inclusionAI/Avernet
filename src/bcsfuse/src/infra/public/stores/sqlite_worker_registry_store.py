"""
SQLite Worker Registry Store - OSS Wrapper

Wraps existing SQLite implementation for OSS compatibility.
"""
from src.infra.adapters.sqlite_worker_registry_store import SQLiteWorkerRegistryStore as _SQLiteWorkerRegistryStore


class SQLiteWorkerRegistryStore(_SQLiteWorkerRegistryStore):
    """
    SQLite Worker Registry Store for OSS.

    This is a thin wrapper around the existing SQLite implementation
    to maintain consistent naming and future extensibility.

    Suitable for development and single-instance deployments.
    For production, consider MySQLWorkerRegistryStore.
    """

    pass