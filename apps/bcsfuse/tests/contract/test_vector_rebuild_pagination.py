"""Rebuild reads only bounded, active vector pages from durable storage."""

import pickle
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from src.domain.models.vector_point import VectorPoint
from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
    MySQLVectorPersistenceBackend,
)
from src.infra.vectorstore_backends.sqlite_vector_persistence_backend import (
    SQLiteVectorPersistenceBackend,
)


class PageConnection:
    def __init__(self):
        self.rows = [
            ("a", pickle.dumps([1.0, 0.0]), "{}", False),
            ("b", pickle.dumps([0.0, 1.0]), "{}", False),
            ("c", pickle.dumps([1.0, 1.0]), "{}", True),
            ("d", pickle.dumps([0.5, 0.5]), "{}", False),
        ]
        self.queries = []
        self.page = []
        self.rollbacks = 0
        self.upper_id = None

    def is_connected(self):
        return True

    def cursor(self):
        return self

    def execute(self, sql, params):
        if "SELECT MAX(`id`)" in sql:
            active_ids = [row[0] for row in self.rows if not row[3]]
            self.upper_id = max(active_ids, default=None)
            self.page = [(self.upper_id,)]
            return
        assert "is_deleted" in sql and "ORDER BY" in sql and "LIMIT %s" in sql
        assert "`id` <= %s" in sql
        self.queries.append(params)
        after = None if len(params) == 2 else params[0]
        upper_id, limit = params[-2:]
        self.page = [
            row[:3]
            for row in self.rows
            if not row[3] and row[0] <= upper_id
            and (after is None or row[0] > after)
        ][:limit]

    def fetchone(self):
        return self.page[0] if self.page else None

    def fetchall(self):
        return self.page

    def close(self):
        pass

    def rollback(self):
        self.rollbacks += 1


def test_mysql_rebuild_pages_skip_deleted_rows_and_bound_each_query():
    connection = PageConnection()
    backend = MySQLVectorPersistenceBackend(vector_dimension=2)
    backend._conn = connection
    backend._schema_initialized = True

    batches = list(backend.iter_batches(2))

    assert [[point.id for point in batch] for batch in batches] == [["a", "b"], ["d"]]
    assert connection.queries == [("d", 2), ("b", "d", 2)]
    assert connection.rollbacks == 3


def test_mysql_rebuild_excludes_rows_inserted_after_initial_max_id():
    connection = PageConnection()
    backend = MySQLVectorPersistenceBackend(vector_dimension=2)
    backend._conn = connection
    backend._schema_initialized = True

    pages = backend.iter_batches(2)
    first = next(pages)
    connection.rows.append(("z", pickle.dumps([1.0, 0.0]), "{}", False))
    remaining = list(pages)

    assert [[point.id for point in page] for page in [first, *remaining]] == [
        ["a", "b"], ["d"]
    ]
    assert connection.upper_id == "d"


def test_sqlite_rebuild_pages_without_loading_all_vectors(tmp_path):
    backend = SQLiteVectorPersistenceBackend(str(tmp_path / "vectors.sqlite"))
    try:
        backend.save_batch([
            VectorPoint(id=key, vector=[1.0, 0.0], payload={"content": key})
            for key in ("a", "b", "c")
        ])
        backend.load_all = lambda: (_ for _ in ()).throw(AssertionError("unbounded load"))

        batches = list(backend.iter_batches(2))

        assert [[point.id for point in batch] for batch in batches] == [["a", "b"], ["c"]]
    finally:
        backend._conn.close()


def test_sqlite_rebuild_excludes_rows_inserted_after_initial_max_id(tmp_path):
    backend = SQLiteVectorPersistenceBackend(str(tmp_path / "vectors.sqlite"))
    try:
        backend.save_batch([
            VectorPoint(id=key, vector=[1.0, 0.0], payload={})
            for key in ("a", "b", "d")
        ])
        pages = backend.iter_batches(2)
        first = next(pages)
        backend.save(VectorPoint(id="z", vector=[1.0, 0.0], payload={}))
        remaining = list(pages)

        assert [[point.id for point in page] for page in [first, *remaining]] == [
            ["a", "b"], ["d"]
        ]
        assert backend.exists("z")
    finally:
        backend._conn.close()


def test_mysql_change_replay_pages_include_tombstones():
    class ChangeConnection(PageConnection):
        def __init__(self):
            super().__init__()
            self.rows = [
                ("a", pickle.dumps([1.0, 0.0]), "{}", 0, 100.0),
                ("b", b"deleted", "{", 1, 101.0),
                ("c", pickle.dumps([0.0, 1.0]), "{}", 0, 102.0),
            ]

        def execute(self, sql, params):
            if "ORDER BY `gmt_modify` DESC" in sql:
                self.page = [(102.0, "c")]
                return
            assert "ORDER BY" in sql and "LIMIT %s" in sql
            assert "is_deleted = 0" not in sql
            self.queries.append(params)
            floor = params[0]
            after = None if len(params) == 5 else (params[1], params[3])
            limit = params[-1]
            self.page = [
                row for row in self.rows
                if row[4] >= floor
                and (after is None or (row[4], row[0]) > after)
                and (row[4], row[0]) <= (params[-3], params[-2])
            ][:limit]

        def fetchone(self):
            return self.page[0] if self.page else None

    connection = ChangeConnection()
    backend = MySQLVectorPersistenceBackend(vector_dimension=2)
    backend._conn = connection
    backend._schema_initialized = True

    batches = list(backend.iter_change_batches_since(100.0, 2))

    assert [[point.id for point in batch.upserts] for batch in batches] == [["a"], ["c"]]
    assert [batch.deleted_ids for batch in batches] == [["b"], []]
    assert [batch.checkpoint for batch in batches] == [101.0, 102.0]
    assert connection.queries == [
        (99.0, 102.0, 102.0, "c", 2),
        (99.0, 101.0, 101.0, "b", 102.0, 102.0, "c", 2),
    ]


def test_mysql_change_replay_stops_at_initial_watermark():
    class GrowingConnection(PageConnection):
        def __init__(self):
            super().__init__()
            self.next_time = 100.0
            self.queries = 0

        def execute(self, sql, params):
            if "ORDER BY `gmt_modify` DESC" in sql:
                self.page = [(100.0, "initial")]
                return
            self.queries += 1
            assert self.queries < 4, "change feed followed later writes indefinitely"
            self.page = (
                [("initial", pickle.dumps([1.0]), "{}", 0, self.next_time)]
                if (self.next_time, "initial") <= (params[-3], params[-2])
                else []
            )
            self.next_time += 1.0

        def fetchone(self):
            return self.page[0] if self.page else None

    connection = GrowingConnection()
    backend = MySQLVectorPersistenceBackend(vector_dimension=1)
    backend._conn = connection
    backend._schema_initialized = True

    batches = list(backend.iter_change_batches_since(100.0, 1))
    assert [[point.id for point in batch.upserts] for batch in batches] == [["initial"]]
    assert connection.queries == 2


def test_mysql_reader_cannot_rollback_an_uncommitted_write():
    class SharedConnection(PageConnection):
        def __init__(self):
            super().__init__()
            self.write_started = threading.Event()
            self.allow_commit = threading.Event()
            self.pending = False
            self.persisted = False

        def execute(self, sql, params):
            if "SELECT 1" in sql:
                self.page = []
            elif "INSERT INTO" in sql:
                self.pending = True
                self.write_started.set()
                assert self.allow_commit.wait(2)
            else:
                self.page = []

        def fetchone(self):
            return None

        def commit(self):
            self.persisted = self.pending
            self.pending = False

        def rollback(self):
            super().rollback()
            self.pending = False

    connection = SharedConnection()
    backend = MySQLVectorPersistenceBackend(vector_dimension=1)
    backend._conn = connection
    backend._schema_initialized = True
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(
            backend.save_batch, [VectorPoint(id="new", vector=[1.0], payload={})]
        )
        assert connection.write_started.wait(2)
        reader = executor.submit(lambda: list(backend.iter_batches(1)))
        try:
            time.sleep(0.05)
            assert not reader.done()
        finally:
            connection.allow_commit.set()
        writer.result(timeout=2)
        reader.result(timeout=2)

    assert connection.persisted
