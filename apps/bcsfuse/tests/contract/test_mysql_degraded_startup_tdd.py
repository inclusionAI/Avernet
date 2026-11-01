"""Runtime stores must not claim a process-wide connection at construction."""

from __future__ import annotations

import pytest


class ExhaustedPool:
    def __init__(self) -> None:
        self.calls = 0

    def get_connection(self):
        self.calls += 1
        raise RuntimeError("MySQL connection quota exhausted")


def test_mysql_store_construction_survives_connection_quota_exhaustion() -> None:
    from app.infra.public.stores.mysql_worker_profile_content_store import (
        MySQLWorkerProfileContentStore,
    )
    from app.infra.public.audit.mysql_worker_audit_log_store import (
        MySQLWorkerAuditLogStore,
    )
    from app.infra.public.stores.mysql_worker_registry_store import (
        MySQLWorkerRegistryStore,
    )
    from app.infra.public.stores.mysql_worker_runtime_state_store import (
        MySQLWorkerRuntimeStateStore,
    )

    for store_type in (
        MySQLWorkerRegistryStore,
        MySQLWorkerRuntimeStateStore,
        MySQLWorkerProfileContentStore,
        MySQLWorkerAuditLogStore,
    ):
        pool = ExhaustedPool()
        store = store_type(pool)
        assert store is not None
        assert pool.calls == 0


def test_mysql_store_schema_is_lazy_on_first_operation() -> None:
    from app.infra.public.stores.mysql_worker_runtime_state_store import (
        MySQLWorkerRuntimeStateStore,
    )

    pool = ExhaustedPool()
    store = MySQLWorkerRuntimeStateStore(pool)

    with pytest.raises(RuntimeError, match="MySQL connection quota exhausted"):
        store.get_runtime_state("worker")

    assert pool.calls == 1
    assert store._schema_initialized is False


def test_mysql_store_first_operation_reuses_one_connection_for_schema() -> None:
    """Schema initialization must not double borrow the quota-constrained pool."""

    from unittest.mock import MagicMock

    from app.infra.public.stores.mysql_worker_runtime_state_store import (
        MySQLWorkerRuntimeStateStore,
    )

    pool = MagicMock()
    connection = pool.get_connection.return_value
    store = MySQLWorkerRuntimeStateStore(pool)

    # The contract invokes the operation-provided connection; it must not call
    # pool.get_connection a second time during lazy schema initialization.
    store._ensure_schema = MagicMock(wraps=lambda conn: None)
    assert store._connection() is connection
    store._ensure_schema.assert_called_once_with(connection)
    assert pool.get_connection.call_count == 1
