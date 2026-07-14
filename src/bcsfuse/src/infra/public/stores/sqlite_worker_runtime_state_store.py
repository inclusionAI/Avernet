"""
SQLite Worker Runtime State Store - OSS Wrapper

Wraps existing SQLite implementation for OSS compatibility.
"""
from src.infra.adapters.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore as _SQLiteWorkerRuntimeStateStore


class SQLiteWorkerRuntimeStateStore(_SQLiteWorkerRuntimeStateStore):
    """
    SQLite Worker Runtime State Store for OSS.

    This is a thin wrapper around the existing SQLite implementation
    to maintain consistent naming and future extensibility.

    Suitable for development and single-instance deployments.
    For production, consider MySQLWorkerRuntimeStateStore.
    """

    pass