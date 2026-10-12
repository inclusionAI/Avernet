"""Existing SQLite Profile databases gain the description column safely."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from src.domain.models.worker_profile_content import WorkerProfileContent
from src.infra.public.stores.sqlite_worker_profile_content_store import (
    SQLiteWorkerProfileContentStore,
)


def test_legacy_sqlite_profile_table_is_upgraded_without_losing_rows(tmp_path):
    path = str(tmp_path / "legacy-profiles.sqlite")
    store = SQLiteWorkerProfileContentStore(db_path=path)
    store.save(WorkerProfileContent(worker_id="bot:owner", soul_md="Old soul"))
    store._conn.execute(
        "ALTER TABLE bcsfuse_worker_profile_contents DROP COLUMN description"
    )
    store._conn.commit()
    store.close()

    upgraded = SQLiteWorkerProfileContentStore(db_path=path)
    previous = upgraded.get("bot:owner", "default")
    assert previous.soul_md == "Old soul"
    upgraded.save(WorkerProfileContent(
        worker_id="bot:owner", soul_md="Old soul", description="New description",
    ))
    assert upgraded.get("bot:owner", "default").description == "New description"
    upgraded.close()


def test_concurrent_sqlite_profile_migrations_accept_a_column_added_by_peer(tmp_path, monkeypatch):
    path = str(tmp_path / "legacy-profiles.sqlite")
    store = SQLiteWorkerProfileContentStore(db_path=path)
    store._conn.execute(
        "ALTER TABLE bcsfuse_worker_profile_contents DROP COLUMN description"
    )
    store._conn.commit()
    store.close()

    before_alter = Barrier(2)
    real_connect = sqlite3.connect

    class RacingCursor(sqlite3.Cursor):
        def execute(self, sql, parameters=()):
            if sql.startswith("ALTER TABLE bcsfuse_worker_profile_contents ADD COLUMN"):
                before_alter.wait(timeout=5)
            return super().execute(sql, parameters)

    class RacingConnection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(*args, factory=RacingCursor, **kwargs)

    monkeypatch.setattr(
        sqlite3, "connect",
        lambda *args, **kwargs: real_connect(*args, factory=RacingConnection, **kwargs),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(SQLiteWorkerProfileContentStore, db_path=path)
        second = executor.submit(SQLiteWorkerProfileContentStore, db_path=path)
        for future in (first, second):
            future.result().close()

    with real_connect(path) as connection:
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(bcsfuse_worker_profile_contents)"
        )}
    assert "description" in columns
