"""
SQLite Worker Audit Log Store - OSS Wrapper

Wraps existing SQLite implementation for OSS compatibility.
"""
from src.infra.adapters.sqlite_worker_audit_log_store import SQLiteWorkerAuditLogStore as _SQLiteWorkerAuditLogStore


class SQLiteWorkerAuditLogStore(_SQLiteWorkerAuditLogStore):
    """
    SQLite Worker Audit Log Store for OSS.

    This is a thin wrapper around the existing SQLite implementation
    to maintain consistent naming and future extensibility.

    Suitable for development and single-instance deployments.
    For production, consider MySQLWorkerAuditLogStore.
    """

    pass