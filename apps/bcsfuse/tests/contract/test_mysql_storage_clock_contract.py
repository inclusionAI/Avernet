"""Clock/read-back failures are covered even when real MySQL is unavailable."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.domain.models.worker import Worker
from src.domain.models.worker_profile_content import WorkerProfileContent
from src.infra.public.stores.mysql_worker_registry_store import MySQLWorkerRegistryStore
from src.infra.public.stores.mysql_worker_profile_content_store import (
    MySQLWorkerProfileContentStore,
)


@pytest.mark.parametrize(
    "operation", ["worker_create", "worker_update", "profile_create", "profile_update"]
)
@pytest.mark.parametrize("read_failure", [False, True])
def test_database_times_are_returned_and_readback_failure_rolls_back(
    operation, read_failure
):
    pool = MagicMock()
    connection = pool.get_connection.return_value
    connection.autocommit = True
    cursor = connection.cursor.return_value
    created = datetime(2026, 1, 1, 19, 0, 0)
    modified = datetime(2026, 1, 2, 19, 0, 0)
    persisted = {
        "gmt_create": created,
        "gmt_modify": modified,
        "version": 2,
        "is_active": 1,
    }
    row = None if read_failure else persisted
    if operation.startswith("worker"):
        with patch.object(MySQLWorkerRegistryStore, "_ensure_schema"):
            store = MySQLWorkerRegistryStore(pool)
        worker = Worker(
            id="clock-test",
            type="bot",
            identity={"name": "Clock", "handle": "@clock"},
            responsibilities=[],
            capabilities=[],
            state={"availability": "public", "trust_level": "trusted"},
        )
        store.exists = lambda _: False
        store.get_by_id = lambda _: worker.model_copy(deep=True)
        cursor.fetchone.return_value = row
        call = lambda: getattr(store, operation.removeprefix("worker_"))(worker)
    else:
        with patch.object(MySQLWorkerProfileContentStore, "_ensure_schema"):
            store = MySQLWorkerProfileContentStore(pool)
        cursor.fetchone.side_effect = [
            {"version": 1} if operation.endswith("update") else None,
            row,
        ]
        call = lambda: store.save(WorkerProfileContent(worker_id="clock-test"))

    if read_failure:
        with pytest.raises(RuntimeError, match="read back"):
            call()
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
    else:
        saved = call()
        assert saved.created_at == created
        assert saved.updated_at == modified
        if operation.startswith("profile"):
            assert saved.is_active is True
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()

    assert connection.autocommit is True
    connection.close.assert_called_once()
    for execution in cursor.execute.call_args_list:
        sql, params = execution.args
        if sql.strip().startswith(("INSERT", "UPDATE")):
            assert not any(isinstance(value, datetime) for value in params)
