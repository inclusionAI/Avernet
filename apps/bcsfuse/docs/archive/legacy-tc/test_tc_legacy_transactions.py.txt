"""TC's legacy SQL scope commits its three stores together, across instances.

SQLite checks transaction ownership here; live ZDAS/OceanBase is not emulated.
"""

import sqlite3
from threading import Event, Thread

import pytest

from app.tc.legacy_transaction import LegacyMutationPool


class Connection:
    def __init__(self, path):
        self.raw = sqlite3.connect(path, timeout=5)

    def start_transaction(self):
        self.raw.execute("BEGIN")

    def __getattr__(self, name):
        return getattr(self.raw, name)


class Pool:
    def __init__(self, path):
        self.path = path

    def get_connection(self):
        return Connection(self.path)


def store_write(pool, table, text):
    connection = pool.get_connection()
    try:
        connection.start_transaction()
        connection.execute(f"UPDATE {table} SET value = ?", (text,))
        connection.commit()
    finally:
        connection.close()


def test_legacy_stores_commit_as_one_transaction_across_independent_pools(tmp_path):
    path = tmp_path / "transactions.sqlite"
    tables = ("workers", "profiles", "states")
    with sqlite3.connect(path) as db:
        for table in tables:
            db.execute(f"CREATE TABLE {table} (value TEXT)")
            db.execute(f"INSERT INTO {table} VALUES ('initial')")
    first, second = (LegacyMutationPool(Pool(path)) for _ in range(2))
    started, finished = Event(), Event()
    failures = []

    def newer_write():
        try:
            with second.transaction():
                started.set()
                for table in tables:
                    store_write(second, table, "newer")
        except (sqlite3.Error, RuntimeError) as error:
            failures.append(error)
        finally:
            finished.set()

    with first.transaction():
        store_write(first, "workers", "older")
        writer = Thread(target=newer_write)
        writer.start()
        assert started.wait(1)
        assert not finished.wait(0.1)
        with sqlite3.connect(path) as observer:
            assert observer.execute("SELECT value FROM workers").fetchone() == (
                "initial",
            )
        for table in tables[1:]:
            store_write(first, table, "older")
    writer.join(5)
    assert not writer.is_alive() and not failures
    with sqlite3.connect(path) as db:
        assert [
            db.execute(f"SELECT value FROM {table}").fetchone()[0] for table in tables
        ] == ["newer"] * 3

    with pytest.raises(RuntimeError, match="profile write failed"), first.transaction():
        store_write(first, "workers", "must rollback")
        raise RuntimeError("profile write failed")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM workers").fetchone() == ("newer",)
