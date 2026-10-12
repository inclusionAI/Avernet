"""Registry write atomicity against disposable MySQL schemas and pooled proxies."""

import os
import threading
from types import SimpleNamespace
from uuid import uuid4

import mysql.connector
import pytest

from src.domain.models.worker import Worker, WorkerIdentity, WorkerType
from src.infra.public.database.mysql_connection_pool import _TrackedConnection
from src.infra.public.stores.mysql_worker_registry_store import MySQLWorkerRegistryStore


pytestmark = pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1",
    reason="requires an explicitly enabled disposable MySQL test database",
)
SIBLINGS = (
    "bcsfuse_worker_profile_contents", "bcsfuse_worker_runtime_states",
    "bcsfuse_worker_profile_bindings", "bcsfuse_worker_audit_logs",
)


@pytest.fixture
def pool():
    config = {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
    }
    database = "bcsfuse_delete_test_" + uuid4().hex
    with mysql.connector.connect(**config, autocommit=True) as admin:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}`")

        class Pool:
            tracked = False
            fail_readback = False

            def get_connection(self):
                conn = mysql.connector.connect(**config, database=database, autocommit=True)
                if self.fail_readback:
                    original_cursor = conn.cursor

                    def failing_cursor(*args, **kwargs):
                        cursor = original_cursor(*args, **kwargs)
                        original_execute = cursor.execute

                        def execute(sql, *execute_args, **execute_kwargs):
                            # Fail only after the real INSERT/UPDATE has executed.
                            if sql.startswith("SELECT gmt_create, gmt_modify"):
                                raise mysql.connector.Error("injected readback failure", errno=1644)
                            return original_execute(sql, *execute_args, **execute_kwargs)

                        cursor.execute = execute
                        return cursor

                    conn.cursor = failing_cursor
                if self.tracked:
                    provider = SimpleNamespace(
                        _lock=threading.Lock(), _connection_count=1, pool_name="delete-test",
                    )
                    return _TrackedConnection(conn, provider)
                return conn

            def execute(self, sql, params=()):
                with self.get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(sql, params)
                        return cursor.fetchall() if cursor.with_rows else []

        try:
            yield Pool()
        finally:
            # Drop only the UUID database created by this fixture.
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database}`")


def create_worker(pool):
    store = MySQLWorkerRegistryStore(pool)
    store.create(Worker(
        id="delete-test", type=WorkerType.BOT,
        identity=WorkerIdentity(name="Delete test", handle="@delete-test"),
        responsibilities=[], capabilities=[],
        state={"availability": "private", "trust_level": "trusted"},
    ))
    return store


def create_siblings(pool, tables):
    for table in tables:
        pool.execute(f"CREATE TABLE {table} (worker_id VARCHAR(128)) ENGINE=InnoDB")
        pool.execute(f"INSERT INTO {table} (worker_id) VALUES (%s)", ("delete-test",))


@pytest.mark.parametrize("tables", [(), SIBLINGS, SIBLINGS[:2] + SIBLINGS[3:]])
def test_delete_handles_uninitialized_sibling_stores(pool, tables):
    store = create_worker(pool)
    create_siblings(pool, tables)
    assert store.delete("delete-test") is True
    assert store.get_by_id("delete-test") is None
    for table in tables:
        assert pool.execute(f"SELECT worker_id FROM {table}") == []


@pytest.mark.parametrize("errno", [1142, 1644])
@pytest.mark.parametrize("tracked", [False, True])
def test_delete_rolls_back_all_rows_on_write_or_permission_failure(pool, errno, tracked):
    store = create_worker(pool)
    create_siblings(pool, SIBLINGS)
    pool.execute(
        "CREATE TRIGGER reject_runtime_delete BEFORE DELETE ON bcsfuse_worker_runtime_states "
        f"FOR EACH ROW SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO={errno}, MESSAGE_TEXT='reject delete'"
    )
    pool.tracked = tracked
    with pytest.raises(mysql.connector.Error, match="reject delete"):
        store.delete("delete-test")
    assert store.get_by_id("delete-test") is not None
    for table in SIBLINGS:
        assert pool.execute(f"SELECT worker_id FROM {table}") == [("delete-test",)]


def test_missing_table_inside_trigger_is_not_treated_as_missing_sibling(pool):
    store = create_worker(pool)
    create_siblings(pool, SIBLINGS)
    pool.execute(
        "CREATE TRIGGER fail_runtime_delete BEFORE DELETE ON bcsfuse_worker_runtime_states "
        "FOR EACH ROW DELETE FROM missing_trigger_target WHERE worker_id=OLD.worker_id"
    )
    with pytest.raises(mysql.connector.Error) as caught:
        store.delete("delete-test")
    assert caught.value.errno == 1146
    assert store.get_by_id("delete-test") is not None
    assert pool.execute("SELECT worker_id FROM bcsfuse_worker_profile_contents") == [("delete-test",)]


@pytest.mark.parametrize("tracked", [False, True])
def test_create_readback_failure_rolls_back_insert(pool, tracked):
    store = MySQLWorkerRegistryStore(pool)
    pool.tracked = tracked
    pool.fail_readback = True
    worker = Worker(
        id="create-failure", type=WorkerType.BOT,
        identity=WorkerIdentity(name="Create test", handle="@create-test"),
        responsibilities=[], capabilities=[],
        state={"availability": "private", "trust_level": "trusted"},
    )
    with pytest.raises(mysql.connector.Error, match="injected readback failure"):
        store.create(worker)
    assert store.get_by_id(worker.id) is None


@pytest.mark.parametrize("tracked", [False, True])
def test_update_readback_failure_rolls_back_values_and_version(pool, tracked):
    store = create_worker(pool)
    worker = store.get_by_id("delete-test")
    worker.identity.name = "Should not persist"
    pool.tracked = tracked
    pool.fail_readback = True
    with pytest.raises(mysql.connector.Error, match="injected readback failure"):
        store.update(worker)
    persisted = store.get_by_id(worker.id)
    assert persisted.identity.name == "Delete test"
    assert persisted.version == 1
