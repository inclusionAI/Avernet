"""Profile stores reject stale full-document writes used by sparse sync."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
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
