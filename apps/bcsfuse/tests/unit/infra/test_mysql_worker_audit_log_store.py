from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.models.worker_audit_log import WorkerAuditAction, WorkerAuditLog
from src.infra.public.audit.mysql_worker_audit_log_store import (
    MySQLWorkerAuditLogStore,
)


class RecordingCursor:
    def __init__(self) -> None:
        self.params: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        if params is not None:
            self.params = params

    def close(self) -> None:
        pass


class RecordingConnection:
    def __init__(self) -> None:
        self.cursor_instance = RecordingCursor()

    def cursor(self) -> RecordingCursor:
        return self.cursor_instance

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


class RecordingPool:
    def __init__(self) -> None:
        self.connections: list[RecordingConnection] = []

    def get_connection(self) -> RecordingConnection:
        connection = RecordingConnection()
        self.connections.append(connection)
        return connection


def test_append_log_serializes_plain_string_values_for_mysql_json_columns() -> None:
    pool = RecordingPool()
    store = MySQLWorkerAuditLogStore(connection_pool=pool)

    store.append_log(
        WorkerAuditLog(
            worker_id="worker-1",
            action=WorkerAuditAction.RUNTIME_STATE_CHANGED,
            old_value="offline",
            new_value="online",
        )
    )

    params = pool.connections[-1].cursor_instance.params
    assert params is not None
    assert json.loads(params[3]) == "offline"
    assert json.loads(params[4]) == "online"


def test_append_log_preserves_values_that_are_already_json() -> None:
    pool = RecordingPool()
    store = MySQLWorkerAuditLogStore(connection_pool=pool)

    store.append_log(
        WorkerAuditLog(
            worker_id="worker-1",
            action=WorkerAuditAction.UPDATED,
            old_value='{"enabled": false}',
            new_value='{"enabled": true}',
        )
    )

    params = pool.connections[-1].cursor_instance.params
    assert params is not None
    assert params[3] == '{"enabled": false}'
    assert params[4] == '{"enabled": true}'


@pytest.mark.parametrize("aware", [False, True])
def test_append_log_preserves_event_epoch_independent_of_local_timezone(aware):
    pool = RecordingPool()
    store = MySQLWorkerAuditLogStore(connection_pool=pool)
    instant = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
    value = instant.astimezone(timezone(timedelta(hours=8))) if aware else instant.replace(tzinfo=None)
    store.append_log(WorkerAuditLog(
        worker_id="worker-1", action=WorkerAuditAction.CREATED, performed_at=value,
    ))
    params = pool.connections[-1].cursor_instance.params
    assert params[-1] == instant.timestamp()
