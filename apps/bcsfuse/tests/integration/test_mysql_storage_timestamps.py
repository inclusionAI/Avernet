"""Real MySQL regression for storage timestamps, using a disposable database.

Enable with BCSFUSE_RUN_MYSQL_INTEGRATION=1. MYSQL_HOST/PORT/USER/PASSWORD
select the test server; no existing application database is used or changed.
"""

from datetime import datetime, timedelta, timezone
import os
import uuid

import mysql.connector
import pytest

from src.domain.models.worker import Worker, WorkerIdentity, WorkerType
from src.domain.models.worker_audit_log import WorkerAuditAction, WorkerAuditLog
from src.domain.models.worker_profile_content import WorkerProfileContent
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.models.worker_source_info import WorkerSourceType
from src.infra.public.audit.mysql_worker_audit_log_store import MySQLWorkerAuditLogStore
from src.infra.public.stores.mysql_worker_profile_binding_store import (
    MySQLWorkerProfileBindingStore,
)
from src.infra.public.stores.mysql_worker_profile_content_store import (
    MySQLWorkerProfileContentStore,
)
from src.infra.public.stores.mysql_worker_registry_store import MySQLWorkerRegistryStore
from src.infra.public.stores.mysql_worker_runtime_state_store import (
    MySQLWorkerRuntimeStateStore,
)


pytestmark = pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1",
    reason="requires an explicitly enabled MySQL test server",
)


class TestPool:
    __test__ = False

    def __init__(self, config, database, zone):
        self.config = dict(config, database=database, time_zone=zone, autocommit=True)

    def get_connection(self):
        return mysql.connector.connect(**self.config)

    def row(self, sql, params=()):
        with self.get_connection() as conn:
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute(sql, params)
                return cursor.fetchone()


@pytest.fixture(params=["+08:00", "+00:00", "-05:00"])
def pool(request):
    config = {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
    }
    database = "bcsfuse_time_test_" + uuid.uuid4().hex
    with mysql.connector.connect(**config) as admin:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}`")
            try:
                yield TestPool(config, database, request.param)
            finally:
                # Only the unique database created by this fixture is removed.
                cursor.execute(f"DROP DATABASE `{database}`")


def assert_current(pool, value):
    now = pool.row("SELECT CURRENT_TIMESTAMP AS now")["now"]
    assert abs((now - value).total_seconds()) < 5


def test_worker_create_update_return_persisted_database_times(pool):
    store = MySQLWorkerRegistryStore(pool)
    worker = Worker(
        id="time-test",
        type=WorkerType.BOT,
        identity=WorkerIdentity(name="Clock", handle="@clock"),
        responsibilities=[],
        capabilities=[],
        state={"availability": "public", "trust_level": "trusted"},
    )
    created = store.create(worker)
    assert_current(pool, created.created_at)
    assert created.created_at == store.get_by_id(worker.id).created_at
    created.identity.name = "Updated"
    updated = store.update(created)
    persisted = store.get_by_id(worker.id)
    assert_current(pool, updated.updated_at)
    assert updated.updated_at == persisted.updated_at
    assert updated.created_at == persisted.created_at == created.created_at
    assert updated.version == created.version + 1


def test_profile_save_update_activate_return_persisted_database_times(pool):
    store = MySQLWorkerProfileContentStore(pool)
    profile = store.save(
        WorkerProfileContent(worker_id="time-test", profile_id="default")
    )
    assert_current(pool, profile.created_at)
    original_created = profile.created_at
    store.activate("time-test", "default")
    # A fresh PUT model must retain the original DB creation time and active state.
    updated = store.save(WorkerProfileContent(worker_id="time-test", soul_md="Updated"))
    persisted = store.get("time-test", "default")
    assert updated.created_at == original_created == persisted.created_at
    assert updated.updated_at == persisted.updated_at
    assert updated.is_active is True
    assert_current(pool, updated.updated_at)
    activated = store.activate("time-test", "default")
    assert_current(pool, activated.updated_at)


def test_runtime_state_insert_and_update_use_database_clock(pool):
    store = MySQLWorkerRuntimeStateStore(pool)
    for state in (WorkerRuntimeState.ONLINE, WorkerRuntimeState.OFFLINE):
        assert store.set_runtime_state("time-test", state)
        row = pool.row(
            "SELECT * FROM bcsfuse_worker_runtime_states WHERE worker_id = %s",
            ("time-test",),
        )
        assert_current(pool, row["gmt_create"])
        assert_current(pool, row["gmt_modify"])
        assert store.get_runtime_state("time-test") == state


@pytest.mark.parametrize("aware", [False, True])
def test_audit_preserves_event_instant_and_uses_database_record_time(pool, aware):
    store = MySQLWorkerAuditLogStore(pool)
    utc_event = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    event = (
        utc_event.astimezone(timezone(timedelta(hours=8)))
        if aware
        else utc_event.replace(tzinfo=None)
    )
    log = WorkerAuditLog(
        worker_id="time-test", action=WorkerAuditAction.CREATED, performed_at=event
    )
    store.append_log(log)
    row = pool.row(
        "SELECT *, UNIX_TIMESTAMP(performed_at) AS event_epoch FROM bcsfuse_worker_audit_logs WHERE id = %s",
        (log.id,),
    )
    assert row["event_epoch"] == utc_event.timestamp()
    assert_current(pool, row["gmt_create"])
    assert_current(pool, row["gmt_modify"])

    assert store.get_latest_log("time-test").performed_at == utc_event.replace(
        tzinfo=None
    )
    assert store.list_logs("time-test")[0].performed_at == utc_event.replace(
        tzinfo=None
    )
    queried = store.query(event - timedelta(seconds=1), event + timedelta(seconds=1))
    assert [item["id"] for item in queried] == [log.id]
    assert queried[0]["timestamp"] == utc_event.replace(tzinfo=None).isoformat()


def test_binding_bind_reactivate_switch_unbind_use_database_clock(pool):
    store = MySQLWorkerProfileBindingStore(connection_pool=pool)
    first = store.bind_profile("time-test", "time-test:first", WorkerSourceType.API)
    assert_current(pool, first.bound_at)
    assert_current(pool, first.updated_at)
    second = store.bind_profile("time-test", "time-test:second", WorkerSourceType.API)
    assert_current(pool, second.bound_at)
    old = pool.row(
        "SELECT * FROM bcsfuse_worker_profile_bindings WHERE binding_id = %s",
        (first.id,),
    )
    assert_current(pool, old["unbound_at"])
    rebound = store.bind_profile("time-test", "time-test:first", WorkerSourceType.API)
    assert_current(pool, rebound.updated_at)
    assert store.set_active_profile("time-test", "time-test:second")
    assert_current(pool, store.get_active_binding("time-test").updated_at)
    assert store.unbind_profile("time-test", "time-test:second")
    row = pool.row(
        "SELECT * FROM bcsfuse_worker_profile_bindings WHERE binding_id = %s",
        (second.id,),
    )
    assert_current(pool, row["unbound_at"])
    assert_current(pool, row["updated_at"])


@pytest.mark.parametrize("kind", ["worker", "profile", "binding"])
def test_readback_failure_does_not_leave_a_committed_record(pool, monkeypatch, kind):
    if kind == "worker":
        store = MySQLWorkerRegistryStore(pool)
        call = lambda: store.create(
            Worker(
                id="failed-clock",
                type=WorkerType.BOT,
                identity=WorkerIdentity(name="Clock", handle="@clock"),
                responsibilities=[],
                capabilities=[],
                state={"availability": "public", "trust_level": "trusted"},
            )
        )
        table = "bcsfuse_workers"
    elif kind == "profile":
        store = MySQLWorkerProfileContentStore(pool)
        call = lambda: store.save(WorkerProfileContent(worker_id="failed-clock"))
        table = "bcsfuse_worker_profile_contents"
    else:
        store = MySQLWorkerProfileBindingStore(pool)
        with pool.get_connection() as connection:
            store._ensure_schema(connection)
        call = lambda: store.bind_profile(
            "failed-clock", "failed-clock:default", WorkerSourceType.API
        )
        table = "bcsfuse_worker_profile_bindings"

    get_connection = pool.get_connection

    def failing_connection():
        connection = get_connection()
        make_cursor = connection.cursor

        def failing_cursor(*args, **kwargs):
            cursor = make_cursor(*args, **kwargs)
            execute = cursor.execute
            inserted = False

            def fail_after_insert(sql, params=()):
                nonlocal inserted
                if inserted and sql.strip().startswith("SELECT"):
                    raise RuntimeError("injected read-back failure")
                result = execute(sql, params)
                inserted = inserted or sql.strip().startswith("INSERT")
                return result

            cursor.execute = fail_after_insert
            return cursor

        connection.cursor = failing_cursor
        return connection

    with monkeypatch.context() as injected:
        injected.setattr(pool, "get_connection", failing_connection)
        with pytest.raises(RuntimeError, match="injected read-back failure"):
            call()
    assert pool.row(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0
