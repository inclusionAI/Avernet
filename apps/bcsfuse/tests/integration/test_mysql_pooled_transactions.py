"""Exercise real runtime connection wrappers against a disposable MySQL database."""

import os
import uuid

import pytest

from src.domain.models.profile_fusion import ConversationTurn, FusedProfileRecord
from src.domain.models.worker_profile_content import WorkerProfileContent
from src.domain.models.worker_source_info import WorkerSourceType
from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider
from src.infra.public.stores.mysql_fused_profile_store import MySQLFusedProfileStore
from src.infra.public.stores.mysql_worker_profile_binding_store import (
    MySQLWorkerProfileBindingStore,
)
from src.infra.public.stores.mysql_worker_profile_content_store import (
    MySQLWorkerProfileContentStore,
)
from tests.integration import test_mysql_storage_timestamps as mysql_fixtures

pool = mysql_fixtures.pool

pytestmark = pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1",
    reason="requires an explicitly enabled MySQL test server",
)


@pytest.fixture
def runtime_pool(pool):
    config = {
        k: pool.config[k] for k in ("host", "port", "user", "password", "database")
    }
    provider = MySQLConnectionPoolProvider(
        **config, pool_size=2, pool_name="transaction_test_" + uuid.uuid4().hex
    )
    acquire = provider.get_connection

    def get_connection():
        conn = acquire()
        with conn.cursor() as cursor:
            cursor.execute("SET time_zone = %s", (pool.config["time_zone"],))
        return conn

    provider.get_connection = get_connection
    try:
        yield provider
    finally:
        provider.close()


def snapshot(pool, table):
    with pool.get_connection() as conn, conn.cursor(dictionary=True) as cursor:
        cursor.execute(f"SELECT * FROM {table} ORDER BY 1")
        return cursor.fetchall()


def inject_failure(monkeypatch, provider, failure):
    acquire = provider.get_connection

    def get_connection():
        conn = acquire()
        make_cursor = conn.cursor

        def cursor(*args, **kwargs):
            result = make_cursor(*args, **kwargs)
            execute = result.execute
            writes = 0

            def checked_execute(sql, params=()):
                nonlocal writes
                statement = sql.lstrip()
                readback = (
                    "bcsfuse_worker_profile_contents" in statement
                    or "WHERE binding_id" in statement
                )
                if (
                    failure == "readback"
                    and writes
                    and statement.startswith("SELECT")
                    and readback
                ):
                    raise RuntimeError("injected transaction failure")
                value = execute(sql, params)
                if statement.startswith(("UPDATE", "INSERT")):
                    writes += 1
                    if failure == "write":
                        raise RuntimeError("injected transaction failure")
                return value

            result.execute = checked_execute
            return result

        conn.cursor = cursor
        return conn

    monkeypatch.setattr(provider, "get_connection", get_connection)


@pytest.mark.parametrize("operation", ["create", "update", "activate"])
def test_profile_failure_rolls_back_content_version_and_active_state(
    pool, runtime_pool, monkeypatch, operation
):
    store = MySQLWorkerProfileContentStore(runtime_pool)
    first = store.save(WorkerProfileContent(worker_id="worker", profile_id="first"))
    store.save(WorkerProfileContent(worker_id="worker", profile_id="second"))
    store.activate("worker", "first")
    before = snapshot(pool, "bcsfuse_worker_profile_contents")
    if operation == "activate":
        call = lambda: store.activate("worker", "second")
    else:
        content = (
            first.model_copy(update={"soul_md": "changed"})
            if operation == "update"
            else WorkerProfileContent(worker_id="new")
        )
        call = lambda: store.save(content)
    with monkeypatch.context() as patch:
        inject_failure(
            patch, runtime_pool, "write" if operation == "activate" else "readback"
        )
        with pytest.raises(RuntimeError, match="injected transaction failure"):
            call()
    assert snapshot(pool, "bcsfuse_worker_profile_contents") == before
    with runtime_pool.get_connection() as conn:
        assert conn.autocommit is True
        assert not conn.in_transaction
    # The same operation remains retryable on the returned connection pool.
    assert call() is not None


@pytest.mark.parametrize("operation", ["create", "rebind", "activate"])
def test_binding_failure_keeps_previous_active_binding(
    pool, runtime_pool, monkeypatch, operation
):
    store = MySQLWorkerProfileBindingStore(runtime_pool)
    store.bind_profile("worker", "worker:first", WorkerSourceType.API)
    store.bind_profile("worker", "worker:second", WorkerSourceType.API)
    before = snapshot(pool, "bcsfuse_worker_profile_bindings")
    if operation == "activate":
        call = lambda: store.set_active_profile("worker", "worker:first")
    else:
        key = "worker:new" if operation == "create" else "worker:first"
        call = lambda: store.bind_profile("worker", key, WorkerSourceType.API)
    with monkeypatch.context() as patch:
        inject_failure(
            patch, runtime_pool, "write" if operation == "activate" else "readback"
        )
        with pytest.raises(RuntimeError, match="injected transaction failure"):
            call()
    assert snapshot(pool, "bcsfuse_worker_profile_bindings") == before
    assert call()


def test_fusion_turn_failure_keeps_conversation_and_statistics(
    pool, runtime_pool, monkeypatch
):
    store = MySQLFusedProfileStore(connection_pool=runtime_pool)
    store.save(
        FusedProfileRecord(
            fusion_id="fusion",
            fusion_mode="g9",
            group_id="group",
            driver_bot_id="worker",
            question="question",
            participant_ids="worker",
            status="pending",
            env="test",
            created_by="test",
        )
    )
    before = snapshot(pool, "bcsfuse_fused_profiles")
    turn = ConversationTurn(turn_index=1, question="question")
    with monkeypatch.context() as patch:
        inject_failure(patch, runtime_pool, "write")
        with pytest.raises(RuntimeError, match="injected transaction failure"):
            store.append_turn("fusion", turn)
    assert snapshot(pool, "bcsfuse_fused_profiles") == before
    store.append_turn("fusion", turn)
    assert store.get_conversation("fusion")["total_turns"] == 1
