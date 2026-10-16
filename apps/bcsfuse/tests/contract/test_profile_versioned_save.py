"""Profile stores reject stale full-document writes used by sparse sync."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import Barrier, Event, Lock
from unittest.mock import MagicMock, patch

import pytest
from mysql.connector import IntegrityError as MySQLIntegrityError

from src.application.services.worker_sync_merge import save_merged_profile
from src.domain.models.worker_profile_content import WorkerProfileContent
from src.infra.public.stores.in_memory_worker_profile_content_store import (
    InMemoryWorkerProfileContentStore,
)
from src.infra.public.stores.mysql_worker_profile_content_store import (
    MySQLWorkerProfileContentStore,
)
from src.infra.public.stores.sqlite_worker_profile_content_store import (
    SQLiteWorkerProfileContentStore,
)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_versioned_save_does_not_overwrite_a_newer_profile(tmp_path, backend):
    if backend == "memory":
        first = second = InMemoryWorkerProfileContentStore()
    else:
        db_path = str(tmp_path / "profiles.sqlite")
        first = SQLiteWorkerProfileContentStore(db_path=db_path)
        second = SQLiteWorkerProfileContentStore(db_path=db_path)

    initial = first.save(WorkerProfileContent(
        worker_id="bot:owner", profile_id="default", soul_md="Original",
    ))
    winner = initial.model_copy(update={"soul_md": "Updated"})
    saved = first.save_if_version(winner, initial.version)
    assert saved is not None

    stale = initial.model_copy(update={"metadata": {"revision": 2}})
    assert second.save_if_version(stale, initial.version) is None
    persisted = second.get("bot:owner", "default")
    assert persisted.soul_md == "Updated"
    assert persisted.metadata == {}


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_versioned_create_rejects_a_second_insert(tmp_path, backend):
    store = (
        InMemoryWorkerProfileContentStore()
        if backend == "memory"
        else SQLiteWorkerProfileContentStore(db_path=str(tmp_path / "profiles.sqlite"))
    )
    first = WorkerProfileContent(worker_id="bot:owner", profile_id="default")
    assert store.save_if_version(first, None) is not None
    assert store.save_if_version(first, None) is None


def test_same_sqlite_profile_store_serializes_delete_until_versioned_save_commits(tmp_path):
    store = SQLiteWorkerProfileContentStore(db_path=str(tmp_path / "profiles.sqlite"))
    initial = store.save(WorkerProfileContent(
        worker_id="bot:owner", profile_id="default", soul_md="Original",
    ))
    changed = initial.model_copy(update={"soul_md": "Updated"})
    first_updated = Event()
    release_first = Event()
    delete_started = Event()

    class PausingCursor:
        def __init__(self, actual):
            self.actual = actual

        def execute(self, sql, parameters=()):
            result = self.actual.execute(sql, parameters)
            if sql.startswith("UPDATE bcsfuse_worker_profile_contents SET") and not first_updated.is_set():
                first_updated.set()
                assert release_first.wait(timeout=5)
            return result

        def __getattr__(self, name):
            return getattr(self.actual, name)

    class PausingConnection:
        def __init__(self, actual):
            self.actual = actual

        def cursor(self):
            return PausingCursor(self.actual.cursor())

        def __getattr__(self, name):
            return getattr(self.actual, name)

    store._conn = PausingConnection(store._conn)

    def delete_after_start():
        delete_started.set()
        return store.delete("bot:owner", "default")

    with ThreadPoolExecutor(max_workers=2) as executor:
        saved_result = executor.submit(store.save_if_version, changed, initial.version)
        assert first_updated.wait(timeout=5)
        deleted_result = executor.submit(delete_after_start)
        assert delete_started.wait(timeout=5)
        try:
            with pytest.raises(FutureTimeoutError):
                deleted_result.result(timeout=0.5)
        finally:
            release_first.set()
        assert saved_result.result(timeout=5).soul_md == "Updated"
        assert deleted_result.result(timeout=5) is True

    assert store.get("bot:owner", "default") is None


def test_scoped_sqlite_profile_read_waits_for_versioned_save_commit(tmp_path):
    store = SQLiteWorkerProfileContentStore(db_path=str(tmp_path / "profiles.sqlite"))
    initial = store.save(WorkerProfileContent(
        worker_id="bot:owner", profile_id="default", soul_md="Original",
        is_active=True,
    ))
    changed = initial.model_copy(update={"soul_md": "Updated"})
    first_updated = Event()
    release_first = Event()
    read_started = Event()

    class PausingCursor:
        def __init__(self, actual):
            self.actual = actual

        def execute(self, sql, parameters=()):
            result = self.actual.execute(sql, parameters)
            if sql.startswith("UPDATE bcsfuse_worker_profile_contents SET") and not first_updated.is_set():
                first_updated.set()
                assert release_first.wait(timeout=5)
            return result

        def __getattr__(self, name):
            return getattr(self.actual, name)

    class PausingConnection:
        def __init__(self, actual):
            self.actual = actual

        def cursor(self):
            return PausingCursor(self.actual.cursor())

        def __getattr__(self, name):
            return getattr(self.actual, name)

    store._conn = PausingConnection(store._conn)

    def read_after_start():
        read_started.set()
        return store.get_active_by_workers(["bot:owner"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        saved_result = executor.submit(store.save_if_version, changed, initial.version)
        assert first_updated.wait(timeout=5)
        read_result = executor.submit(read_after_start)
        assert read_started.wait(timeout=5)
        try:
            with pytest.raises(FutureTimeoutError):
                read_result.result(timeout=0.5)
        finally:
            release_first.set()
        assert saved_result.result(timeout=5).soul_md == "Updated"
        assert [item.soul_md for item in read_result.result(timeout=5)] == ["Updated"]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_concurrent_sparse_merges_keep_both_supplied_fields(tmp_path, monkeypatch, backend):
    if backend == "memory":
        first = second = InMemoryWorkerProfileContentStore()
    else:
        db_path = str(tmp_path / "profiles.sqlite")
        first = SQLiteWorkerProfileContentStore(db_path=db_path)
        second = SQLiteWorkerProfileContentStore(db_path=db_path)
    first.save(WorkerProfileContent(
        worker_id="bot:owner", profile_id="default", soul_md="Original",
    ))

    barrier = Barrier(2)
    lock = Lock()
    reads = 0

    def coordinate_reads(store):
        original_get = store.get

        def get(worker_id, profile_id):
            nonlocal reads
            profile = original_get(worker_id, profile_id)
            with lock:
                reads += 1
                should_wait = reads <= 2
            if should_wait:
                barrier.wait(timeout=5)
            return profile

        monkeypatch.setattr(store, "get", get)

    coordinate_reads(first)
    if second is not first:
        coordinate_reads(second)

    def apply(store, patch_data, supplied):
        return save_merged_profile(
            store, patch_data, supplied,
            worker_id="bot:owner", profile_id="default",
            worker_name="Bot", description=None,
            description_supplied=False, fallback_profile=None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(apply, first, {"soul_md": "Updated"}, {"soul_md"})
        two = executor.submit(apply, second, {"metadata": {"revision": 2}}, {"metadata"})
        one.result()
        two.result()

    persisted = first.get("bot:owner", "default")
    assert persisted.soul_md == "Updated"
    assert persisted.metadata == {"revision": 2}


def test_mysql_versioned_save_rolls_back_when_profile_changed():
    pool = MagicMock()
    connection = pool.get_connection.return_value
    cursor = connection.cursor.return_value
    cursor.rowcount = 0
    with patch.object(MySQLWorkerProfileContentStore, "_ensure_schema"):
        store = MySQLWorkerProfileContentStore(pool)

    result = store.save_if_version(
        WorkerProfileContent(worker_id="bot:owner", profile_id="default"), 1,
    )

    assert result is None
    update_sql, parameters = cursor.execute.call_args.args
    assert "WHERE worker_id = %s AND profile_id = %s AND version = %s" in update_sql
    assert parameters[-1] == 1
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_mysql_versioned_save_commits_and_returns_persisted_version():
    pool = MagicMock()
    connection = pool.get_connection.return_value
    cursor = connection.cursor.return_value
    cursor.rowcount = 1
    cursor.fetchone.return_value = {
        "gmt_create": "2026-10-01 10:00:00",
        "gmt_modify": "2026-10-01 10:01:00",
        "version": 2,
        "is_active": 1,
    }
    with patch.object(MySQLWorkerProfileContentStore, "_ensure_schema"):
        store = MySQLWorkerProfileContentStore(pool)

    saved = store.save_if_version(
        WorkerProfileContent(worker_id="bot:owner", profile_id="default"), 1,
    )

    assert saved.version == 2
    assert saved.is_active is True
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()


def test_mysql_versioned_create_treats_duplicate_as_conflict():
    pool = MagicMock()
    connection = pool.get_connection.return_value
    connection.cursor.return_value.execute.side_effect = MySQLIntegrityError(
        msg="Duplicate entry", errno=1062,
    )
    with patch.object(MySQLWorkerProfileContentStore, "_ensure_schema"):
        store = MySQLWorkerProfileContentStore(pool)

    result = store.save_if_version(
        WorkerProfileContent(worker_id="bot:owner", profile_id="default"), None,
    )

    assert result is None
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
